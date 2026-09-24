"""
Plain English -> validated spine -> Context Studio JSON-LD + agent digest.

The loop is deterministic on purpose:

    generate -> JSON Schema check -> semantic check -> repair -> ... -> compile

Schema-constrained decoding alone cannot catch the failures that actually
matter here (unreachable states, deadlocks, operations cross-wired to another
entity's lifecycle), so the compiler's validator is the real gate and its
errors are fed back to the model verbatim.
"""
from __future__ import annotations
import json, re, sys, pathlib
from dataclasses import dataclass, field

from langchain_core.messages import SystemMessage, HumanMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compiler.compile import validate, compile_jsonld, build_digest, Report  # noqa: E402
from builder.retry import invoke_with_retry, grow_budget                     # noqa: E402

PROMPT_PATH = ROOT / "prompts" / "01_schema_builder_v4.md"
SCHEMA_PATH = ROOT / "spec" / "spine.schema.json"


# ---------------------------------------------------------------- JSON rescue
def extract_json(text: str) -> dict:
    """Pull one JSON object out of a model response, fenced or not."""
    if isinstance(text, (dict, list)):
        return text
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model response")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:            esc = False
            elif ch == "\\":   esc = True
            elif ch == '"':    in_str = False
            continue
        if ch == '"':          in_str = True
        elif ch == "{":        depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON object is truncated - raise max_tokens")


# ---------------------------------------------------------------- checks
def schema_check(spine: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    schema = json.loads(SCHEMA_PATH.read_text())
    v = jsonschema.Draft202012Validator(schema)
    return [f"schema: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(spine), key=lambda e: list(e.path))][:20]


def semantic_check(spine: dict) -> tuple[list[str], list[str]]:
    rep = Report()
    try:
        validate(spine, rep)
    except Exception as e:                       # malformed beyond validation
        rep.err("FATAL", f"{type(e).__name__}: {e}")
    return ([f"[{c}] {m}" for c, m in rep.errors],
            [f"[{c}] {m}" for c, m in rep.warns])


# ---------------------------------------------------------------- result
@dataclass
class BuildResult:
    spine: dict | None = None
    jsonld: dict | None = None
    digest: dict | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    log: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.jsonld is not None and not self.errors


# ---------------------------------------------------------------- the loop
REPAIR_TEMPLATE = """Your previous spine failed validation.

Errors that MUST be fixed:
{errors}

Reminders for the rules that were broken:
- Every state in an entity's "s" must be reachable from "s0" by that entity's operations.
- Every non-terminal state needs at least one outgoing operation (no deadlocks).
- Terminal states ("sT") must have NO outgoing operations.
- An operation's "f" and "t" must both be states of the entity named in "e".
- Relation targets must exist in "E"; cardinality is one of 1-1, 1-n, n-1, n-n.

Return the COMPLETE corrected spine as one JSON object. No prose."""


def build_spine(description: str, llm, max_repairs: int = 3,
                on_event=None) -> BuildResult:
    say = on_event or (lambda *a: None)
    res = BuildResult()
    system = PROMPT_PATH.read_text()
    messages = [SystemMessage(content=system),
                HumanMessage(content=f"Business domain:\n\n{description.strip()}")]

    for attempt in range(1, max_repairs + 2):
        res.attempts = attempt
        say("call", f"attempt {attempt}: calling "
                    f"{getattr(llm, '_sab_provider', '?')}/{getattr(llm, '_sab_model', '?')}")
        reply = invoke_with_retry(llm, messages, say=say)
        text = reply.content if hasattr(reply, "content") else str(reply)
        if isinstance(text, list):                      # content blocks
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        if getattr(reply, "usage_metadata", None):
            for k, v in reply.usage_metadata.items():
                if isinstance(v, int):
                    res.usage[k] = res.usage.get(k, 0) + v

        try:
            spine = extract_json(text)
        except Exception as e:
            if "truncated" in str(e):
                grow_budget(llm, say)
            errs = [f"parse: {e}"]
            res.log.append(f"attempt {attempt}: {errs[0]}")
            say("fail", errs[0])
            messages = [SystemMessage(content=system),
                        HumanMessage(content=f"Business domain:\n\n{description.strip()}"),
                        HumanMessage(content=REPAIR_TEMPLATE.format(
                            errors="- " + errs[0]))]
            continue

        errs = schema_check(spine) or []
        sem_errs, warns = semantic_check(spine)
        errs += sem_errs

        if not errs:
            res.spine, res.warnings = spine, warns
            res.log.append(f"attempt {attempt}: valid")
            say("ok", f"valid spine on attempt {attempt}")
            break

        res.log.append(f"attempt {attempt}: {len(errs)} errors")
        say("fail", f"attempt {attempt}: {len(errs)} errors -> repairing")
        for e in errs[:8]:
            say("detail", f"  {e}")
        if attempt == max_repairs + 1:
            res.errors, res.spine = errs, spine
            return res
        # Window the conversation instead of appending. Re-send the previous
        # spine compactly (no indentation) with the errors, so the request size
        # stays flat across attempts rather than growing by a spine each time.
        messages = [SystemMessage(content=system),
                    HumanMessage(content=f"Business domain:\n\n{description.strip()}"),
                    HumanMessage(content="Your previous attempt:\n"
                                         + json.dumps(spine, separators=(",", ":"))),
                    HumanMessage(content=REPAIR_TEMPLATE.format(
                        errors="\n".join("- " + e for e in errs[:25])))]

    if res.spine is None:
        res.errors = res.errors or ["no valid spine produced"]
        return res

    rep = Report()
    ents, sdesc, ops, out_edges = validate(res.spine, rep)
    res.jsonld, decisions = compile_jsonld(res.spine, ents, sdesc, ops, out_edges)
    res.digest = build_digest(res.spine, ents, ops, decisions)
    return res


def write_artifacts(res: BuildResult, outdir: str, stem: str) -> dict[str, str]:
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, obj, indent in (("spine.json", res.spine, 1),
                              ("jsonld", res.jsonld, 2),
                              ("digest.json", res.digest, 1)):
        if obj is None:
            continue
        ext = {"spine.json": "spine.json", "jsonld": "jsonld",
               "digest.json": "digest.json"}[name]
        path = out / f"{stem}.{ext}"
        path.write_text(json.dumps(obj, indent=indent))
        written[ext] = str(path)
    return written
