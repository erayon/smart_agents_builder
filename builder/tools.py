"""LangChain tools exposing the deterministic compiler to a tool-calling agent."""
from __future__ import annotations
import json, sys, pathlib
from langchain_core.tools import tool

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compiler.compile import validate, compile_jsonld, build_digest, Report  # noqa: E402
from builder.spine_builder import schema_check, extract_json                 # noqa: E402


@tool
def validate_spine(spine_json: str) -> str:
    """Validate a domain spine. Pass the spine as a JSON string.

    Returns 'VALID' plus node counts, or a numbered list of errors to fix.
    Checks JSON Schema conformance, state reachability, deadlocks, terminal
    states with outgoing transitions, cross-entity transitions, and relations.
    """
    try:
        spine = extract_json(spine_json)
    except Exception as e:
        return f"INVALID\n1. could not parse JSON: {e}"

    errs = schema_check(spine)
    rep = Report()
    try:
        ents, sdesc, ops, out_edges = validate(spine, rep)
    except Exception as e:
        return f"INVALID\n1. {type(e).__name__}: {e}"
    errs += [f"[{c}] {m}" for c, m in rep.errors]

    if errs:
        return "INVALID\n" + "\n".join(f"{i}. {e}" for i, e in enumerate(errs, 1))

    _, decisions = compile_jsonld(spine, ents, sdesc, ops, out_edges)
    warns = "\n".join(f"- [{c}] {m}" for c, m in rep.warns)
    return (f"VALID\nentities={len(ents)} "
            f"states={sum(len(e.get('s', [])) for e in ents.values())} "
            f"operations={len(ops)} decisionPoints={len(decisions)}"
            + (f"\nwarnings:\n{warns}" if warns else ""))


@tool
def compile_spine(spine_json: str) -> str:
    """Compile a validated spine into Context Studio JSON-LD and an agent digest.

    Returns a JSON object with 'nodes', 'triples' and 'digest_counts'.
    Call validate_spine first; this fails on an invalid spine.
    """
    spine = extract_json(spine_json)
    rep = Report()
    ents, sdesc, ops, out_edges = validate(spine, rep)
    if rep.errors:
        return "ERROR: spine is invalid, call validate_spine first"
    doc, decisions = compile_jsonld(spine, ents, sdesc, ops, out_edges)
    digest = build_digest(spine, ents, ops, decisions)
    return json.dumps({"nodes": len(doc["@graph"]),
                       "digest_counts": digest["counts"]})


TOOLS = [validate_spine, compile_spine]
