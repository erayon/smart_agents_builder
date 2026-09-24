#!/usr/bin/env python3
"""
digest + topology -> an agent-granularity LangGraph package.

langgraph_gen.py emits one node per state transition, which makes the graph an
exact picture of the spine - useful for proving the state machine sound, but 54
node functions nobody wants to maintain.

This emits one node per agent, the shape production systems actually use:

    START -> member_assistant -> assessment -> claim_flow -> ... -> END

The state machine does not vanish; it moves into the state as `status`, and the
handoff edges between agents are derived from it. Which agent may follow which
is computed from state adjacency, not guessed.
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys
from collections import defaultdict, deque

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from compiler.langgraph_gen import snake, validate_topology            # noqa: E402
from compiler.compile import Report                                     # noqa: E402

PRIM = {"string": "str", "number": "float", "boolean": "bool",
        "date": "date", "datetime": "datetime", "object": "dict[str, Any]"}


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in snake(name).split("_"))


def py_type(spec: str) -> str:
    """Map a spine attribute type to a Python annotation."""
    optional = False
    s = spec.strip()
    if s.endswith("|null"):
        s, optional = s[: -len("|null")], True
    if s in PRIM:
        t = PRIM[s]
    elif "|" in s:                                    # an enum of literals
        t = "Literal[" + ", ".join(repr(v.strip()) for v in s.split("|")) + "]"
    else:
        t = "str"
    return f"{t} | None" if optional else t


# ---------------------------------------------------------------- units
def readers_of(topo):
    """Read-only agents: they answer questions and change nothing.

    They are deliberately not part of the lifecycle pipeline. Answering "where
    is my claim?" does not advance a claim, so putting such an agent in the
    flow would mean every route had to step around it. Each gets its own small
    graph instead, entered when a question arrives rather than when work does.
    """
    return [{**a, "kind": "reader"} for a in topo.get("agents", [])
            if not a.get("owns") and a.get("reads")]


def units_of(topo):
    """Working units: agents and functions that own operations."""
    out = []
    for a in topo.get("agents", []):
        if not a.get("owns") and a.get("reads"):
            continue
        out.append({**a, "kind": "agent"})
    for f in topo.get("functions", []):
        out.append({**f, "name": f.get("name", pascal(f["id"])), "kind": "function"})
    return out


def handoffs(digest, topo):
    """Which unit may follow which, derived from the state machine.

    Unit U hands to unit V when U owns an operation landing in state S and V
    owns an operation leaving S, within the same entity. Terminal states end
    the run.
    """
    ops = {o["n"]: o for o in digest["operations"]}
    owner = {}
    for u in units_of(topo):
        for op in u["owns"]:
            owner[op] = u["id"]

    leaving = defaultdict(list)                        # (entity, state) -> [op]
    for o in digest["operations"]:
        leaving[(o["e"], o["f"])].append(o["n"])

    terminal = {e["n"]: set(e.get("terminal") or []) for e in digest["entities"]}
    edges, ends = defaultdict(set), set()
    for o in digest["operations"]:
        src = owner.get(o["n"])
        if src is None:
            continue
        if o["t"] in terminal.get(o["e"], ()):
            ends.add(src)
        for nxt in leaving[(o["e"], o["t"])]:
            dst = owner.get(nxt)
            if dst:
                edges[src].add(dst)
    return owner, edges, ends


def entry_unit(digest, topo, owner):
    """Which unit a run starts in.

    `topology.json` may state `"entry"` outright, and should when the default
    is wrong. Otherwise: find the true origins - lifecycle states nothing
    transitions into - and take the first agent, in the order the topology
    lists them, that owns an operation leaving one. Agents are preferred over
    functions because a run starts with interaction or judgement, not with a
    mechanical step, and topology order is meaningful: the planner lists the
    front-door agent first.

    Picking by "most operations" does not work. That finds the hub entity, and
    the hub is the middle of a process, not its front door.
    """
    ids = {u["id"] for u in units_of(topo)}
    if topo.get("entry") in ids:
        return topo["entry"]

    targets = {(o["e"], o["t"]) for o in digest["operations"]}
    origins = [e for e in digest["entities"]
               if e.get("lifecycle") and (e["n"], e.get("initial")) not in targets]
    starters = set()
    for e in origins:
        for o in digest["operations"]:
            if o["e"] == e["n"] and o["f"] == e.get("initial") and owner.get(o["n"]):
                starters.add(owner[o["n"]])

    for u in units_of(topo):                      # agents first, topology order
        if u["kind"] == "agent" and u["id"] in starters:
            return u["id"]
    for u in units_of(topo):
        if u["id"] in starters:
            return u["id"]
    return units_of(topo)[0]["id"]


# ---------------------------------------------------------------- emitters
def gen_models(digest, spine) -> str:
    L = ['"""Typed payloads, generated from the domain model. Do not edit."""',
         "from __future__ import annotations", "",
         "from datetime import date, datetime",
         "from typing import Any, Literal", "",
         "from pydantic import BaseModel, Field", "", ""]
    for e in spine["E"]:
        L.append(f"class {pascal(e['n'])}(BaseModel):")
        L.append(f'    """{e.get("d", e["n"])}"""')
        L.append("")
        L.append(f"    {snake(e['k'])}: str | None = None")
        if e.get("h") and snake(e["h"]) != snake(e["k"]):
            L.append(f"    {snake(e['h'])}: str | None = None")
        for field, spec in (e.get("a") or {}).items():
            if snake(field) in (snake(e["k"]), snake(e.get("h") or "")):
                continue
            t = py_type(spec)
            # every payload field is optional because payloads fill in as the
            # run proceeds; py_type may already have added it for a |null spec
            if not t.endswith("| None"):
                t += " | None"
            L.append(f"    {snake(field)}: {t} = None")
        if e.get("s"):
            lit = ", ".join(repr(s) for s in e["s"])
            L.append(f"    lifecycle_state: Literal[{lit}] | None = None")
        if e.get("inv"):
            L.append("")
            L.append("    # invariants: " + " | ".join(e["inv"]))
        L.append("")
        L.append("")
    names = ", ".join(repr(pascal(e["n"])) for e in spine["E"])
    L.append(f"__all__ = [{names}]")
    return "\n".join(L) + "\n"


def gen_state(digest, spine, topo) -> str:
    lifecycles = [e for e in spine["E"] if e.get("s")]
    primary = max(lifecycles, key=lambda e: sum(
        1 for o in digest["operations"] if o["e"] == e["n"])) if lifecycles else None
    lit = ", ".join(repr(s) for s in primary["s"]) if primary else "str"
    fields = "\n".join(
        f"    {snake(e['n'])}: {pascal(e['n'])} | None" for e in spine["E"])
    imports = ", ".join(pascal(e["n"]) for e in spine["E"])
    return f'''"""Pipeline state. Generated from the domain model. Do not edit."""
from __future__ import annotations

from typing import Any, Literal
from typing_extensions import TypedDict

from .models import {imports}


class StepRecord(TypedDict, total=False):
    """One unit's turn, appended as the run proceeds."""
    unit: str
    kind: str
    operation: str
    from_state: str
    to_state: str
    note: str


class PipelineState(TypedDict, total=False):
    """State threaded through every node.

    Each unit reads what it needs and writes back its own payload, the way
    a stage pipeline accumulates typed output.
    """
    run_id: str

    # the primary lifecycle, mirrored here so routers can read it cheaply
    status: Literal[{lit}] | None

    # typed payloads, one per business object
{fields}

    history: list[StepRecord]
    decisions: dict[str, str]
    pending_operation: str | None
    awaiting_human: bool
    errors: list[str]

    # set when a question graph is invoked rather than the lifecycle one
    question: str | None
    answer: str | None
'''


def gen_agent_module(unit, digest) -> str:
    ops = [o for o in digest["operations"] if o["n"] in unit["owns"]]
    table = "\n".join(
        f"#   {o['n']:<28} {o['e']:<18} {o['f']} -> {o['t']}"
        + ("   [human approval]" if o.get("hitl") else "")
        + (f"   creates {', '.join(o['creates'])}" if o.get("creates") else "")
        for o in ops)
    makes = sorted({c for o in ops for c in o.get("creates", [])})
    prompt = unit.get("prompt", "").replace('"""', "'''")
    tools = unit.get("tools", [])
    return f'''"""{unit.get("name", unit["id"])}.

{unit.get("role", "")}

Why this grouping: {unit.get("why", "")}

This module is yours. It is created once and never regenerated, so real
implementations are safe here.

Operations it owns:
{table}
"""
from __future__ import annotations

import logging
from typing import Any

from orchestrator.state import PipelineState
from tools import TOOLS

logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = {tools!r}

OPERATIONS: list[str] = {[o["n"] for o in ops]!r}

HUMAN_APPROVAL: list[str] = {[o["n"] for o in ops if o.get("hitl")]!r}

# entities this unit brings into existence - it must construct and persist them
CREATES: list[str] = {makes!r}

SYSTEM_PROMPT = """{prompt}

You may use only these tools: {", ".join(tools) or "none"}.
You handle only these operations: {", ".join(o["n"] for o in ops)}.
Never act outside them - another unit owns the rest.
"""


async def handle(state: PipelineState) -> dict[str, Any]:
    """Advance the run and return the state update.

    Decide which of OPERATIONS applies from `status`, call the model with
    SYSTEM_PROMPT and the tools in TOOL_NAMES, then write back the payload
    and the new status.

    Anything in HUMAN_APPROVAL must not be committed without a person: set
    `awaiting_human` and let the graph interrupt.
    """
    raise NotImplementedError("implement {unit['id']}.handle")
'''


def gen_reader_module(unit, digest) -> str:
    reads = unit.get("reads", [])
    ops = [o for o in digest["operations"] if o["e"] in reads]
    prompt = unit.get("prompt", "").replace('"""', "'''")
    return f'''"""{unit.get("name", unit["id"])} - read-only.

{unit.get("role", "")}

Why this grouping: {unit.get("why", "")}

This agent answers questions and changes nothing. It owns no operations and
must not write. It is not a node in the lifecycle pipeline; it has its own
graph, entered when someone asks rather than when work arrives.

This module is yours. It is created once and never regenerated.

Reads: {", ".join(reads)}
"""
from __future__ import annotations

import logging
from typing import Any

from orchestrator.state import PipelineState
from tools import TOOLS

logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = {unit.get("tools", [])!r}

READS: list[str] = {reads!r}

# the operations whose outcomes it can be asked about, for context only -
# it must not perform them
VISIBLE_OPERATIONS: list[str] = {[o["n"] for o in ops]!r}

SYSTEM_PROMPT = """{prompt}

You may read {", ".join(reads) or "nothing"} and use only these tools: {", ".join(unit.get("tools", [])) or "none"}.

You answer questions. You never change anything, never start or advance work,
and never promise an outcome. If someone asks you to act, say which part of the
system does it. If the answer is not in what you can read, say so rather than
inferring it.
"""


async def answer(state: PipelineState, question: str) -> dict[str, Any]:
    """Answer `question` from state. Return the answer; change nothing.

    Read the payloads listed in READS, work out where things stand and what is
    holding them up, and reply. Any state update returned here is a bug.
    """
    raise NotImplementedError("implement {unit['id']}.answer")
'''


def gen_function_module(unit, digest) -> str:
    ops = [o for o in digest["operations"] if o["n"] in unit["owns"]]
    table = "\n".join(
        f"#   {o['n']:<28} {o['e']:<18} {o['f']} -> {o['t']}"
        + (f"   creates {', '.join(o['creates'])}" if o.get("creates") else "")
        for o in ops)
    makes = sorted({c for o in ops for c in o.get("creates", [])})
    return f'''"""{unit["id"]} - deterministic. No model decides these.

{unit.get("why", "")}

This module is yours. It is created once and never regenerated.

Operations:
{table}
"""
from __future__ import annotations

import logging
from typing import Any

from orchestrator.state import PipelineState
from tools import TOOLS

logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = {unit.get("tools", [])!r}

OPERATIONS: list[str] = {[o["n"] for o in ops]!r}

# entities this unit brings into existence
CREATES: list[str] = {makes!r}


async def handle(state: PipelineState) -> dict[str, Any]:
    """Apply the rule and return the state update.

    These are mechanical: a gateway result, a timer, a propagation of a
    decision already made. Call the system directly; do not call a model.
    """
    raise NotImplementedError("implement {unit['id']}.handle")
'''


def gen_pipeline(digest, topo, owner, edges, ends, entry, readers=()) -> str:
    units = units_of(topo)
    by_id = {u["id"]: u for u in units}
    hitl_units = sorted({owner[o["n"]] for o in digest["operations"]
                         if o.get("hitl") and o["n"] in owner})

    nodes, routers, wiring = [], [], []
    for u in units:
        uid = u["id"]
        ops = [o for o in digest["operations"] if o["n"] in u["owns"]]
        nodes.append(f'''
async def node_{uid}(state: PipelineState) -> dict[str, Any]:
    """{by_id[uid].get("name", uid)} - {len(ops)} operations, {u["kind"]}."""
    return await {uid}.handle(state)''')

        targets = sorted(edges.get(uid, set()))
        opts = [t for t in targets]
        if uid in ends or not opts:
            opts = opts + ["__end__"]
        lit = ", ".join(repr(o) for o in opts)
        routers.append(f'''
def route_after_{uid}(state: PipelineState) -> Literal[{lit}]:
    """Where the run goes after {uid}.

    Derived from the state machine: these are the only units owning an
    operation that can leave the states {uid} can land in. Decide from
    `status`; the explicit override in `decisions` exists for tests.
    """
    choice = state.get("decisions", {{}}).get({uid!r})
    if choice is not None:
        return choice
    raise NotImplementedError(
        "{uid}: implement route_after_{uid}, or set "
        "state['decisions']['{uid}'] to one of {opts!r}")''')

        # END is a LangGraph sentinel object, not the string "END"; emit it as a
        # bare identifier or compile() rejects the branch as an unknown target.
        mapping = ", ".join(
            ("'__end__': END" if o == "__end__" else f"{o!r}: {o!r}") for o in opts)
        wiring.append(f'    g.add_conditional_edges({uid!r}, route_after_{uid}, '
                      f'{{{mapping}}})')

    readers_src = ""
    if readers:
        blocks = []
        for r in readers:
            blocks.append(f'''

async def node_{r["id"]}(state: PipelineState) -> dict[str, Any]:
    """{r.get("name", r["id"])} - read-only. Answers and changes nothing."""
    return await {r["id"]}.answer(state, state.get("question", ""))


def build_{r["id"]}_graph(checkpointer: Any | None = None):
    """A question graph, not a lifecycle one: START -> {r["id"]} -> END.

    It is separate because answering does not advance any entity. Invoke it
    when someone asks something; invoke build_graph() when work arrives.
    """
    g = StateGraph(PipelineState)
    g.add_node({r["id"]!r}, node_{r["id"]})
    g.add_edge(START, {r["id"]!r})
    g.add_edge({r["id"]!r}, END)
    return g.compile(checkpointer=checkpointer or MemorySaver())''')
        mapping = ", ".join(f"{r['id']!r}: build_{r['id']}_graph" for r in readers)
        blocks.append(f"\n\nQUERY_GRAPHS = {{{mapping}}}\n")
        readers_src = "".join(blocks)

    add_nodes = "\n".join(f'    g.add_node({u["id"]!r}, node_{u["id"]})' for u in units)
    imports = "\n".join(
        [f"from agents import {u['id']}" if u["kind"] == "agent"
         else f"from functions import {u['id']}" for u in units]
        + [f"from agents import {r['id']}" for r in readers])
    interrupts = f",\n        interrupt_before={hitl_units!r}" if hitl_units else ""

    return f'''"""{digest["domain"]} - agent pipeline. GENERATED, do not edit.

One node per unit, not per state transition. The state machine lives in
`state["status"]`; the edges here are the handoffs between units, derived
from which unit owns an operation that can leave a state another unit lands
in.

Units: {", ".join(u["id"] for u in units)}
Entry: {entry}
Human approval required in: {", ".join(hitl_units) or "none"}
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

{imports}
from .state import PipelineState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- nodes
{"".join(nodes)}


# ---------------------------------------------------------------- routers
{"".join(routers)}


# ---------------------------------------------------------------- graph
def build_graph(checkpointer: Any | None = None):
    """Compile the pipeline.

    `interrupt_before` pauses before any unit that owns an operation needing
    human approval. That is deliberately coarse: it stops the whole unit, not
    one operation. For finer control raise an interrupt inside the handler
    when the pending operation is in that unit's HUMAN_APPROVAL list.
    """
    g = StateGraph(PipelineState)
{add_nodes}

    g.add_edge(START, {entry!r})
{chr(10).join(wiring)}

    return g.compile(
        checkpointer=checkpointer or MemorySaver(){interrupts},
    )


UNITS: dict[str, str] = {{{", ".join(f"{u['id']!r}: {u['kind']!r}" for u in units)}}}
{readers_src}'''



GENERATED = ("orchestrator/models.py", "orchestrator/state.py",
             "orchestrator/pipeline.py", "orchestrator/__init__.py",
             "agents/__init__.py", "functions/__init__.py")


def gen_inits(units, readers=()) -> dict[str, str]:
    """Package registries. Generated; the modules they import are yours."""
    agents = [u["id"] for u in units if u["kind"] == "agent"] + [r["id"] for r in readers]
    funcs = [u["id"] for u in units if u["kind"] == "function"]
    mk = lambda names, what: (
        f'"""{what} registry. GENERATED - do not edit.\n\n'
        f'The modules imported here are yours and are never regenerated.\n"""\n'
        + "".join(f"from . import {n}\n" for n in names)
        + "\n" + what.upper() + " = {"
        + ", ".join(f"{n!r}: {n}" for n in names) + "}\n\n"
        + f'__all__ = [{", ".join(repr(n) for n in names)}, "{what.upper()}"]\n')
    return {
        "agents/__init__.py": mk(agents, "agents"),
        "functions/__init__.py": mk(funcs, "functions"),
        "orchestrator/__init__.py": ('"""Orchestrator package. GENERATED."""\n'
                                     "from .pipeline import build_graph\n"
                                     "from .state import PipelineState\n\n"
                                     '__all__ = ["build_graph", "PipelineState"]\n'),
    }


def write_files(files, outdir) -> dict[str, str]:
    """Generated files are overwritten; unit modules are written once."""
    out, acts = pathlib.Path(outdir), {}
    for rel, src in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel not in GENERATED and path.exists():
            acts[rel] = "skipped"
            continue
        acts[rel] = "updated" if path.exists() else "created"
        path.write_text(src)
    return acts


# ---------------------------------------------------------------- main
def generate(digest, spine, topo):
    rep = Report()
    owner = validate_topology(digest, topo, rep)
    edges, ends = handoffs(digest, topo)[1:]
    entry = entry_unit(digest, topo, owner)
    units = units_of(topo)
    readers = readers_of(topo)
    return {
        "orchestrator/models.py": gen_models(digest, spine),
        "orchestrator/state.py": gen_state(digest, spine, topo),
        "orchestrator/pipeline.py": gen_pipeline(digest, topo, owner, edges, ends,
                                                 entry, readers),
        **{f"agents/{r['id']}.py": gen_reader_module(r, digest) for r in readers},
        **{f"agents/{u['id']}.py": gen_agent_module(u, digest)
           for u in units if u["kind"] == "agent"},
        **{f"functions/{u['id']}.py": gen_function_module(u, digest)
           for u in units if u["kind"] == "function"},
    }, rep, units, entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("digest")
    ap.add_argument("topology")
    ap.add_argument("--spine", required=True)
    ap.add_argument("-o", "--outdir", help="write the package here")
    ap.add_argument("--check", action="store_true",
                    help="validate the topology and report, writing nothing")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    digest = json.loads(pathlib.Path(a.digest).read_text())
    spine = json.loads(pathlib.Path(a.spine).read_text())
    topo = json.loads(pathlib.Path(a.topology).read_text())
    if not a.check and not a.outdir:
        ap.error("give -o/--outdir, or --check to validate without writing")

    files, rep, units, entry = generate(digest, spine, topo)
    files.update(gen_inits(units, readers_of(topo)))

    print(f"validation ({len(rep.errors)} errors, {len(rep.warns)} warnings)")
    print(rep.render())
    print()
    if a.strict and rep.errors:
        sys.exit(1)

    if a.check:
        agents = [u for u in units if u["kind"] == "agent"]
        funcs = [u for u in units if u["kind"] == "function"]
        for u in agents:
            print(f"  agent    {u['id']:<20} {len(u['owns']):>2} ops  "
                  f"tools={','.join(u.get('tools', [])) or '-'}")
        for u in funcs:
            print(f"  function {u['id']:<20} {len(u['owns']):>2} ops  (deterministic)")
        print(f"\nunits: {len(units)}  entry: {entry}  "
              f"(nothing written - this was --check)")
        return files

    acts = write_files(files, a.outdir)
    for rel in sorted(acts):
        print(f"  {acts[rel]:<8} {rel:<40} {len(files[rel].splitlines()):>4} lines")
    print(f"\nunits: {len(units)}  entry: {entry}  ->  {a.outdir}")
    return files


if __name__ == "__main__":
    main()
