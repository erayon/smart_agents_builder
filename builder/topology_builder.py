"""
digest.json -> validated topology -> runnable LangGraph module.

Same loop as stage 1: generate, validate deterministically, feed the errors
back, repair. The topology validator is the gate - it checks ownership,
least-privilege tools and router coverage, none of which a JSON Schema can
express.
"""
from __future__ import annotations
import json, pathlib, sys
from dataclasses import dataclass, field

from langchain_core.messages import SystemMessage, HumanMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compiler.compile import Report                                      # noqa: E402
from compiler.langgraph_gen import validate_topology, generate           # noqa: E402
from builder.retry import invoke_with_retry, grow_budget                 # noqa: E402
from builder.spine_builder import extract_json                           # noqa: E402

PROMPT_PATH = ROOT / "prompts" / "02_agent_planner.md"
SCHEMA_PATH = ROOT / "spec" / "topology.schema.json"

REPAIR = """Your previous topology failed validation.

Errors that MUST be fixed:
{errors}

Reminders:
- Every operation in the digest is owned exactly once, across agents and functions.
- An agent's tools must all appear in the `sys` of its own operations.
- One router per decision point; `decidedBy` must be a real agent or function id.
- Operations with `by: System` belong in `functions`, not `agents`.

Return the COMPLETE corrected topology as one JSON object. No prose."""


def schema_check(topo) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    return [f"schema: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(topo), key=lambda e: list(e.path))][:20]


def digest_brief(digest: dict) -> str:
    """Only what the planner needs. The full digest carries fields it must not
    re-derive, and a smaller prompt leaves more budget for the answer."""
    return json.dumps({
        "domain": digest["domain"], "ns": digest["ns"],
        "counts": digest["counts"],
        "operations": digest["operations"],
        "decisionPoints": [{"state": d["state"], "entity": d["entity"],
                            "branches": [b["op"] for b in d["branches"]]}
                           for d in digest["decisionPoints"]],
        "cohesion": digest["cohesion"],
    }, separators=(",", ":"))


@dataclass
class PlanResult:
    topology: dict | None = None
    source: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    usage: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.source is not None and not self.errors


def build_topology(digest: dict, llm, max_repairs: int = 3, on_event=None) -> PlanResult:
    say = on_event or (lambda *a: None)
    res = PlanResult()
    system = PROMPT_PATH.read_text()
    brief = digest_brief(digest)
    base = [SystemMessage(content=system),
            HumanMessage(content=f"Digest:\n\n{brief}")]
    messages = list(base)

    for attempt in range(1, max_repairs + 2):
        res.attempts = attempt
        say("call", f"attempt {attempt}: planning topology with "
                    f"{getattr(llm, '_sab_provider', '?')}/{getattr(llm, '_sab_model', '?')}")
        reply = invoke_with_retry(llm, messages, say=say)
        text = reply.content if hasattr(reply, "content") else str(reply)
        if isinstance(text, list):
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        if getattr(reply, "usage_metadata", None):
            for k, v in reply.usage_metadata.items():
                if isinstance(v, int):
                    res.usage[k] = res.usage.get(k, 0) + v

        try:
            topo = extract_json(text)
        except Exception as e:
            if "truncated" in str(e):
                grow_budget(llm, say)
            say("fail", f"attempt {attempt}: {e}")
            messages = base + [HumanMessage(content=REPAIR.format(errors=f"- parse: {e}"))]
            continue

        errs = schema_check(topo)
        rep = Report()
        try:
            validate_topology(digest, topo, rep)
        except Exception as e:
            rep.err("FATAL", f"{type(e).__name__}: {e}")
        errs += [f"[{c}] {m}" for c, m in rep.errors]

        if not errs:
            res.topology = topo
            res.warnings = [f"[{c}] {m}" for c, m in rep.warns]
            say("ok", f"valid topology on attempt {attempt}")
            break

        say("fail", f"attempt {attempt}: {len(errs)} errors -> repairing")
        for e in errs[:8]:
            say("detail", f"  {e}")
        if attempt == max_repairs + 1:
            res.errors, res.topology = errs, topo
            return res
        messages = base + [
            HumanMessage(content="Your previous attempt:\n"
                                 + json.dumps(topo, separators=(",", ":"))),
            HumanMessage(content=REPAIR.format(
                errors="\n".join("- " + e for e in errs[:25])))]

    if res.topology is None:
        res.errors = res.errors or ["no valid topology produced"]
        return res

    rep = Report()
    owner = validate_topology(digest, res.topology, rep)
    res.source, _, _ = generate(digest, res.topology, owner)
    return res
