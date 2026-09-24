"""
Tool-calling agent mode.

The loop is written against langchain-core's bind_tools directly rather than
AgentExecutor, because the agent APIs moved between LangChain 0.x and 1.x and
this keeps the builder working on both. The model drives: it drafts a spine,
calls validate_spine, reads the errors and iterates until VALID.

Chain mode (spine_builder.build_spine) is the default and is cheaper and more
predictable. Use agent mode when you want the model to explore the domain and
self-correct without a fixed repair budget.
"""
from __future__ import annotations
import pathlib, sys
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder.tools import TOOLS, validate_spine                       # noqa: E402
from builder.retry import invoke_with_retry                           # noqa: E402
from builder.spine_builder import (BuildResult, extract_json, semantic_check,   # noqa: E402
                                   PROMPT_PATH, compile_jsonld, build_digest,
                                   validate, Report)

AGENT_SUFFIX = """
You have tools available.

Workflow you must follow:
1. Draft the spine.
2. Call `validate_spine` with it.
3. If the result is INVALID, fix every listed error and call `validate_spine` again.
4. Once it returns VALID, reply with the final spine as one JSON object and nothing else.

Never present a spine as final until `validate_spine` has returned VALID for it.
"""


def build_spine_agentic(description: str, llm, max_steps: int = 12,
                        on_event=None) -> BuildResult:
    say = on_event or (lambda *a: None)
    res = BuildResult()
    bound = llm.bind_tools(TOOLS)
    by_name = {t.name: t for t in TOOLS}

    messages = [SystemMessage(content=PROMPT_PATH.read_text() + AGENT_SUFFIX),
                HumanMessage(content=f"Business domain:\n\n{description.strip()}")]
    last_spine = None

    def _fallback(reason):
        """Agent mode round-trips the whole spine through a tool-call argument,
        which roughly doubles its token cost and gives small or rate-limited
        models a second chance to mangle it. When that breaks, chain mode does
        the same job without the double serialization."""
        say("fail", f"  agent mode unusable ({reason}); falling back to chain mode")
        from builder.spine_builder import build_spine
        out = build_spine(description, llm, on_event=on_event)
        out.log.insert(0, f"fell back from agent mode: {reason}")
        return out

    for step in range(1, max_steps + 1):
        res.attempts = step
        try:
            reply = llm_invoke(bound, messages, res, say=say)
        except Exception as e:
            low = str(e).lower()
            if any(t in low for t in ("tool_use_failed", "tool call", "tool_calls",
                                      "request too large", "does not support tools")):
                return _fallback(type(e).__name__)
            raise
        messages.append(reply)

        calls = getattr(reply, "tool_calls", None) or []
        if not calls:
            text = _text(reply)
            try:
                last_spine = extract_json(text)
            except Exception as e:
                say("fail", f"step {step}: {e}")
                messages.append(HumanMessage(
                    content=f"That was not parseable JSON ({e}). "
                            "Reply with the final spine as one JSON object."))
                continue
            errs, warns = semantic_check(last_spine)
            if errs:
                say("fail", f"step {step}: model stopped with {len(errs)} errors")
                messages.append(HumanMessage(
                    content="Still invalid:\n" + "\n".join("- " + e for e in errs[:20])
                            + "\nCall validate_spine after fixing."))
                continue
            res.spine, res.warnings = last_spine, warns
            say("ok", f"valid spine after {step} steps")
            break

        for call in calls:
            say("tool", f"step {step}: {call['name']}")
            out = by_name[call["name"]].invoke(call["args"])
            if call["name"] == "validate_spine":
                say("detail", "  " + out.splitlines()[0])
                try:
                    last_spine = extract_json(list(call["args"].values())[0])
                except Exception:
                    pass
            messages.append(ToolMessage(content=str(out), tool_call_id=call["id"]))

    if res.spine is None:
        res.errors = [f"agent did not converge in {max_steps} steps"]
        res.spine = last_spine
        return res

    rep = Report()
    ents, sdesc, ops, out_edges = validate(res.spine, rep)
    res.jsonld, decisions = compile_jsonld(res.spine, ents, sdesc, ops, out_edges)
    res.digest = build_digest(res.spine, ents, ops, decisions)
    return res


def _text(reply):
    c = reply.content if hasattr(reply, "content") else str(reply)
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c


def llm_invoke(bound, messages, res, say=None):
    reply = invoke_with_retry(bound, messages, say=say)
    if getattr(reply, "usage_metadata", None):
        for k, v in reply.usage_metadata.items():
            if isinstance(v, int):
                res.usage[k] = res.usage.get(k, 0) + v
    return reply
