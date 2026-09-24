#!/usr/bin/env python3
"""
digest.json + topology.json -> runnable LangGraph module + validation report.

Stage 1 asked "is this a valid domain model". Stage 2 asks "does this graph
actually run". Nodes, edges, routers and interrupts are derived from the
digest; the topology only supplies the grouping decisions an LLM must make.
"""
from __future__ import annotations
import argparse, json, keyword, pathlib, re, sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from compiler.compile import Report, qstate                       # noqa: E402


def snake(name: str) -> str:
    """CamelCase to snake_case, keeping acronyms intact.

    ClaimDB -> claim_db, OCRSvc -> ocr_svc, not claim_d_b / o_c_r_svc.
    """
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()
    s = re.sub(r"[^a-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s + "_" if keyword.iskeyword(s) else s


# ---------------------------------------------------------------- validate
def validate_topology(digest, topo, rep: Report):
    ops = {o["n"]: o for o in digest["operations"]}
    systems = {s for o in digest["operations"] for s in o.get("sys", [])}
    agents = topo.get("agents", [])
    funcs = topo.get("functions", [])
    units = agents + funcs

    ids = [u["id"] for u in units]
    for i in set(ids):
        if ids.count(i) > 1:
            rep.err("T1", f"duplicate id '{i}'")

    owner = {}
    for u in units:
        kind = "agent" if u in agents else "function"
        for op in u.get("owns", []):
            if op not in ops:
                rep.err("T2", f"{u['id']}: owns unknown operation '{op}'")
            elif op in owner:
                rep.err("T3", f"operation '{op}' owned twice ({owner[op]} and {u['id']})")
            else:
                owner[op] = u["id"]
        reads = u.get("reads", [])
        if not u.get("owns") and not reads:
            rep.err("T4", f"{u['id']}: owns no operations and reads nothing")
        if reads and kind == "function":
            rep.err("T15", f"{u['id']}: only an agent can be read-only")
        entities = {o["e"] for o in digest["operations"]}
        for r in reads:
            if r not in entities:
                rep.err("T16", f"{u['id']}: reads unknown entity '{r}'")

        # Least privilege. A working unit's tools come from its own operations.
        # A read-only agent has none, so they come from the systems that touch
        # the entities it reads - it can look at exactly what it answers about.
        allowed = {s for op in u.get("owns", []) if op in ops for s in ops[op].get("sys", [])}
        if reads:
            allowed |= {s for o in digest["operations"] if o["e"] in reads
                        for s in o.get("sys", [])}
        for t in u.get("tools", []):
            if t not in systems:
                rep.err("T5", f"{u['id']}: tool '{t}' is not a system in this domain")
            elif t not in allowed:
                where = ("the entities it reads" if reads and not u.get("owns")
                         else "its own operations")
                rep.err("T6", f"{u['id']}: tool '{t}' is touched by none of {where}")
        if kind == "agent" and u.get("owns"):
            ents = {ops[op]["e"] for op in u.get("owns", []) if op in ops}
            if len(ents) > 2:
                rep.warn("T7", f"{u['id']}: spans {len(ents)} entities ({', '.join(sorted(ents))}); "
                               f"consider whether that is one seam or several")

    for op in ops:
        if op not in owner:
            rep.err("T8", f"operation '{op}' is owned by no agent or function")

    # System operations should be deterministic, not agent-driven
    fn_ids = {f["id"] for f in funcs}
    for op, o in ops.items():
        if o.get("by") == "System" and owner.get(op) not in fn_ids:
            rep.warn("T9", f"{op}: by=System but owned by agent '{owner.get(op)}'; "
                           f"deterministic steps belong in functions")

    # routers: one per decision point, decidedBy must exist
    dps = {d["state"]: d for d in digest.get("decisionPoints", [])}
    seen = set()
    for r in topo.get("routers", []):
        if r["state"] not in dps:
            rep.err("T10", f"router for '{r['state']}' is not a decision point")
        seen.add(r["state"])
        if r["decidedBy"] not in set(ids):
            rep.err("T11", f"router '{r['state']}': decidedBy '{r['decidedBy']}' is not an id")
    for s in dps:
        if s not in seen:
            rep.err("T12", f"decision point '{s}' has no router")

    # anti fan-out
    # a read-only agent does no work, so it should not flatter the ratio
    n_ops, n_ag = len(ops), len([a for a in agents if a.get("owns")])
    if n_ag and n_ops / n_ag < 2:
        rep.warn("T13", f"{n_ag} agents for {n_ops} operations; one agent per operation "
                        f"is a known anti-pattern - justify or merge")
    if n_ag >= 3 and not topo.get("orchestrator", {}).get("enabled"):
        rep.warn("T14", f"{n_ag} agents with no orchestrator; "
                        f"orchestrator-worker is the common production shape")
    return owner


# ---------------------------------------------------------------- graph shape
def graph_for(digest, entity):
    """Per-entity graph: nodes are operations, edges follow the state machine."""
    name = entity["n"]
    ops = [o for o in digest["operations"] if o["e"] == name]
    out = defaultdict(list)
    for o in ops:
        out[o["f"]].append(o)
    terminal = set(entity.get("terminal") or [])
    return {"entity": name, "ops": ops, "out": out,
            "initial": entity.get("initial"), "terminal": terminal}


# ---------------------------------------------------------------- codegen
HEADER = '''"""
{domain} - generated agent graph.

Generated by compiler/langgraph_gen.py from the stage-1 digest and the stage-2
topology. Regenerate rather than editing by hand; replace the tool stubs and
run_operation with real implementations.

Topology rationale:
{rationale}
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


class DomainState(TypedDict, total=False):
    """Shared state. `decisions` drives routers until real logic replaces them."""
    entity_id: str
    status: str
    history: list[dict[str, Any]]
    decisions: dict[str, str]
    data: dict[str, Any]


'''

RUNNER = '''
# ---------------------------------------------------------------- effects
# handlers.py is yours and is never regenerated. When it exists it replaces
# the default below, so graph.py can be rebuilt at any time without touching
# your implementations.
def _default_run_operation(state: DomainState, *, op: str, owner: str,
                           kind: str, to_state: str, tools: list[str]) -> dict:
    """Record the operation and advance the state.

    Implement the real thing in handlers.py: for an agent node call the model
    with AGENT_PROMPTS[owner] and the tools listed; for a function node call
    the system directly, with no model involved.
    """
    return {
        "status": to_state,
        "history": [*state.get("history", []),
                    {"op": op, "owner": owner, "kind": kind, "tools": tools}],
    }


try:
    from handlers import run_operation  # type: ignore
except ImportError:
    run_operation = _default_run_operation


'''


def gen_tools(digest):
    systems = sorted({s for o in digest["operations"] for s in o.get("sys", [])})
    used = defaultdict(set)
    for o in digest["operations"]:
        for s in o.get("sys", []):
            used[s].add(o["n"])
    lines = ["# ---------------------------------------------------------------- tools",
             "# One stub per system named in the domain. Wire these to real clients."]
    for s in systems:
        lines.append(f'''
def {snake(s)}(action: str, **kwargs: Any) -> Any:
    """{s}. Used by: {', '.join(sorted(used[s]))}."""
    raise NotImplementedError("wire {s} to a real client")''')
    entries = ", ".join("%r: %s" % (s, snake(s)) for s in systems)
    lines.append("""

# A project scaffolded by compiler/scaffold.py has a tools/ package holding
# real implementations. It wins. These stubs only exist so a freshly generated
# graph.py runs on its own.
try:
    from tools import TOOLS  # type: ignore
except ImportError:
    TOOLS = {%s}

""" % entries)
    return "\n".join(lines)


def gen_agents(topo):
    lines = ["# ---------------------------------------------------------------- agents",
             "AGENT_PROMPTS: dict[str, str] = {"]
    for a in topo.get("agents", []):
        prompt = a["prompt"].replace('"""', "'''")
        lines.append(f'    {a["id"]!r}: """{prompt}""",')
    lines.append("}\n")
    lines.append("AGENT_TOOLS: dict[str, list[str]] = {")
    for u in topo.get("agents", []) + topo.get("functions", []):
        lines.append(f'    {u["id"]!r}: {u.get("tools", [])!r},')
    lines.append("}\n\n")
    return "\n".join(lines)


def gen_nodes(digest, owner, topo):
    kinds = {}
    for a in topo.get("agents", []):
        kinds.update({op: ("agent", a["id"]) for op in a["owns"]})
    for f in topo.get("functions", []):
        kinds.update({op: ("function", f["id"]) for op in f["owns"]})

    lines = ["# ---------------------------------------------------------------- nodes"]
    for o in digest["operations"]:
        kind, own = kinds.get(o["n"], ("agent", "unassigned"))
        tag = " [human approval required]" if o.get("hitl") else ""
        lines.append(f'''
def {snake(o["n"])}(state: DomainState) -> dict:
    """{o["n"]}: {o["e"]} {o["f"]} -> {o["t"]}. {kind} `{own}`, risk={o.get("risk", "low")}.{tag}"""
    return run_operation(state, op={o["n"]!r}, owner={own!r}, kind={kind!r},
                         to_state={o["t"]!r}, tools={o.get("sys", [])!r})''')
    return "\n".join(lines) + "\n\n"


def gen_routers(digest, topo, graphs):
    rules = {r["state"]: r for r in topo.get("routers", [])}
    lines = ["\n# ---------------------------------------------------------------- routers"]
    made = {}
    for g in graphs:
        for state, outs in g["out"].items():
            if len(outs) < 2:
                continue
            qual = qstate(g["entity"], state)
            fn = f"route_{snake(qual)}"
            made[qual] = (fn, [snake(o["n"]) for o in outs])
            r = rules.get(qual, {})
            opts = ", ".join(repr(snake(o["n"])) for o in outs)
            targets_repr = repr([snake(o["n"]) for o in outs])
            lines.append(f'''
def {fn}(state: DomainState) -> Literal[{opts}]:
    """{r.get("rule", "No rule supplied.")}

    Decided by: {r.get("decidedBy", "unassigned")}

    Implement the rule above. Until then this reads state["decisions"], which
    lets you drive the graph in tests. It deliberately raises rather than
    defaulting to a branch: several of these decision points sit on a cycle,
    and an arbitrary default loops forever instead of failing.
    """
    choice = state.get("decisions", {{}}).get({qual!r})
    if choice is None:
        raise NotImplementedError(
            "{qual}: no decision recorded. Implement {fn}, or set "
            "state['decisions']['{qual}'] to one of {targets_repr}")
    return choice''')
    return "\n".join(lines) + "\n\n", made


def gen_graphs(digest, graphs, routers, hitl_nodes):
    lines = ["\n# ---------------------------------------------------------------- graphs"]
    builders = []
    for g in graphs:
        ent = g["entity"]
        fn = f"build_{snake(ent)}_graph"
        builders.append((ent, fn))
        body = [f'''
def {fn}(checkpointer: Any | None = None):
    """{ent} lifecycle: {len(g["ops"])} operations, initial={g["initial"]!r}."""
    g = StateGraph(DomainState)''']
        for o in g["ops"]:
            body.append(f'    g.add_node({snake(o["n"])!r}, {snake(o["n"])})')

        starts = g["out"].get(g["initial"], [])
        if len(starts) == 1:
            body.append(f'    g.add_edge(START, {snake(starts[0]["n"])!r})')
        elif len(starts) > 1:
            qual = qstate(ent, g["initial"])
            rfn, targets = routers[qual]
            body.append(f'    g.add_conditional_edges(START, {rfn}, '
                        f'{{{", ".join(f"{t!r}: {t!r}" for t in targets)}}})')

        for o in g["ops"]:
            node, nxt = snake(o["n"]), g["out"].get(o["t"], [])
            if o["t"] in g["terminal"] or not nxt:
                body.append(f'    g.add_edge({node!r}, END)')
            elif len(nxt) == 1:
                body.append(f'    g.add_edge({node!r}, {snake(nxt[0]["n"])!r})')
            else:
                qual = qstate(ent, o["t"])
                rfn, targets = routers[qual]
                body.append(f'    g.add_conditional_edges({node!r}, {rfn}, '
                            f'{{{", ".join(f"{t!r}: {t!r}" for t in targets)}}})')

        stops = sorted(n for n in hitl_nodes if any(snake(o["n"]) == n for o in g["ops"]))
        extra = f',\n                     interrupt_before={stops!r}' if stops else ""
        body.append(f'    return g.compile(checkpointer=checkpointer or MemorySaver(){extra})')
        lines.append("\n".join(body))

    primary = max(graphs, key=lambda g: len(g["ops"]))
    lines.append(f'''

GRAPHS = {{{", ".join(f"{e!r}: {f}" for e, f in builders)}}}


def build_graph(checkpointer: Any | None = None):
    """Primary lifecycle: {primary["entity"]}."""
    return build_{snake(primary["entity"])}_graph(checkpointer)
''')
    return "\n".join(lines)


def generate(digest, topo, owner):
    graphs = [graph_for(digest, e) for e in digest["entities"] if e.get("lifecycle")]
    hitl = {snake(o["n"]) for o in digest["operations"] if o.get("hitl")}
    routers_src, routers = gen_routers(digest, topo, graphs)
    return (HEADER.format(domain=digest["domain"],
                          rationale="  " + topo["rationale"].replace("\n", "\n  "))
            + gen_tools(digest) + gen_agents(topo) + RUNNER
            + gen_nodes(digest, owner, topo) + routers_src
            + gen_graphs(digest, graphs, routers, hitl)), graphs, hitl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("digest")
    ap.add_argument("topology")
    ap.add_argument("-o", "--out", default="graph.py")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    digest = json.loads(pathlib.Path(a.digest).read_text())
    topo = json.loads(pathlib.Path(a.topology).read_text())
    rep = Report()
    owner = validate_topology(digest, topo, rep)
    src, graphs, hitl = generate(digest, topo, owner)
    pathlib.Path(a.out).write_text(src)

    print(f"validation ({len(rep.errors)} errors, {len(rep.warns)} warnings)")
    print(rep.render())
    print(f"\nagents={len(topo.get('agents', []))}  functions={len(topo.get('functions', []))}  "
          f"tools={len({s for o in digest['operations'] for s in o.get('sys', [])})}  "
          f"routers={len(topo.get('routers', []))}  interrupts={len(hitl)}")
    print(f"graphs: " + ", ".join(f"{g['entity']}({len(g['ops'])})" for g in graphs))
    print(f"wrote {a.out} ({len(src.splitlines())} lines)")
    if a.strict and rep.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
