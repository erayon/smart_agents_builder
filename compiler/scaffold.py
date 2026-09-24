#!/usr/bin/env python3
"""
Turn generated artifacts into a working project folder.

Three tiers, kept strictly apart so regeneration is always safe:

  source of truth  PROBLEM.md, schema/spine.json, topology.json  - you edit
  generated        schema/*.jsonld, digest.json, graph.py        - never edit
  yours            handlers.py, tools/*.py, tests/               - written once

Anything in the third tier is created only when missing. Regenerating a
project never overwrites an implementation you have written.
"""
from __future__ import annotations
import argparse, datetime, json, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from compiler.langgraph_gen import snake                                  # noqa: E402
from compiler.compile import qstate                                       # noqa: E402


def write(path: pathlib.Path, text: str, *, once: bool = False) -> str:
    """Return 'skipped' when a once-only file already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if once and path.exists():
        return "skipped"
    existed = path.exists()
    path.write_text(text)
    return "updated" if existed else "created"


# ---------------------------------------------------------------- tools
def tool_module(system: str, ops: list[str]) -> str:
    return f'''"""{system} client.

This file is yours. It is created once and never regenerated, so it is safe
to put real implementations here.

Used by: {', '.join(sorted(ops))}
"""
from __future__ import annotations
from typing import Any


def call(action: str, **kwargs: Any) -> Any:
    """Perform `action` against {system}."""
    raise NotImplementedError("implement {system}.call")
'''


def tools_init(systems: list[str]) -> str:
    imports = "\n".join(f"from . import {snake(s)}" for s in systems)
    entries = ",\n    ".join(f"{s!r}: {snake(s)}.call" for s in systems)
    return f'''"""Tool registry. GENERATED - do not edit.

Add or remove systems in schema/spine.json and regenerate; the modules this
imports are yours and are left alone.
"""
{imports}

TOOLS = {{
    {entries},
}}

__all__ = ["TOOLS"]
'''


HANDLERS = '''"""Operation effects.

This file is yours. It is created once and never regenerated.

`graph.py` imports run_operation from here when it exists, so you can rebuild
the graph at any time without losing what you write below.
"""
from __future__ import annotations
from typing import Any

from tools import TOOLS


def run_operation(state: dict, *, op: str, owner: str, kind: str,
                  to_state: str, tools: list[str]) -> dict:
    """Carry out one operation and return the state update.

    kind == "function": call the system directly. No model is involved; these
    are the steps whose outcome a gateway or a timer decides.

    kind == "agent": call the model with AGENT_PROMPTS[owner] from graph.py,
    giving it only the tools listed, then apply what it decides.
    """
    # for tool in tools:
    #     TOOLS[tool]("...")
    return {
        "status": to_state,
        "history": [*state.get("history", []),
                    {"op": op, "owner": owner, "kind": kind}],
    }
'''


def smoke_test(digest, topo, first_decisions) -> str:
    dec = json.dumps(first_decisions, indent=8).replace("\n", "\n    ")
    return f'''"""Smoke test: the graph reaches a terminal state.

Run with: python -m pytest tests/ -q
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from graph import build_graph


def test_graph_reaches_end():
    app = build_graph()
    cfg = {{"configurable": {{"thread_id": "smoke"}}}}
    state = {{
        "entity_id": "TEST-1",
        "history": [],
        "data": {{}},
        "decisions": {dec},
    }}
    app.invoke(state, cfg)
    # resume through every human interrupt
    for _ in range(20):
        if not app.get_state(cfg).next:
            break
        app.invoke(None, cfg)
    final = app.get_state(cfg)
    assert not final.next, f"graph did not finish, stuck at {{final.next}}"
    assert final.values["history"], "no operations ran"
'''



def terminating_decisions(digest) -> dict[str, str]:
    """Pick, for each decision point, the branch that makes progress.

    Seeding the first branch is not safe: several decision points sit on a
    cycle (request more info, resubmit, back to review) and the smoke test
    would spin until the recursion limit. For each branch we measure the
    distance from its target state to a terminal state and take the nearest,
    which guarantees the test walks forward and finishes.
    """
    from collections import defaultdict, deque

    by_entity = defaultdict(list)
    for o in digest["operations"]:
        by_entity[o["e"]].append(o)

    chosen = {}
    for ent in digest["entities"]:
        name, terminal = ent["n"], set(ent.get("terminal") or [])
        if not terminal:
            continue
        back = defaultdict(list)
        for o in by_entity[name]:
            back[o["t"]].append(o["f"])
        dist = {t: 0 for t in terminal}
        q = deque(terminal)
        while q:
            cur = q.popleft()
            for prev in back[cur]:
                if prev not in dist:
                    dist[prev] = dist[cur] + 1
                    q.append(prev)
        # decisionPoints carry QUALIFIED state names (ClaimApproved) while the
        # distance map is keyed on plain ones (Approved). Without this mapping
        # every lookup misses, every branch scores infinity, and min() silently
        # falls back to the first branch - which is what loops.
        plain = {qstate(name, st): st for st in ent.get("states", [])}
        for d in digest["decisionPoints"]:
            if d["entity"] != name:
                continue
            best = min(d["branches"],
                       key=lambda b: dist.get(plain.get(b["to"], b["to"]), 10**6))
            chosen[d["state"]] = snake(best["op"])
    return chosen


# ---------------------------------------------------------------- docs
def readme(problem, spine, digest, topo, systems) -> str:
    c = digest["counts"]
    agents = "\n".join(
        f"| `{a['id']}` | {len(a['owns'])} | {', '.join(a['tools']) or '-'} | {a['why']} |"
        for a in topo["agents"])
    funcs = "\n".join(
        f"| `{f['id']}` | {len(f['owns'])} | {', '.join(f.get('tools', [])) or '-'} | {f['why']} |"
        for f in topo["functions"])
    decisions = "\n".join(
        f"- **{d['state']}** → " + " | ".join(f"`{b['op']}`" for b in d["branches"])
        for d in digest["decisionPoints"])
    hitl = [o["n"] for o in digest["operations"] if o.get("hitl")]
    return f'''# {digest["domain"]}

Agentic platform scaffold generated from a plain-English brief.
See [PROBLEM.md](PROBLEM.md) for the original statement.

## The domain

| | |
|---|---|
| entities | {c["entities"]} |
| lifecycle states | {c["states"]} |
| operations | {c["operations"]} |
| decision points | {c["decisionPoints"]} |
| distinct systems | {c["systems"]} |
| human approvals | {c["humanInLoopOps"]} |
| high-risk operations | {c["highRiskOps"]} |

Open `viewer/index.html` and drop `schema/schema.jsonld` on it to see each
entity's lifecycle drawn out.

## The agents

{topo["rationale"]}

| agent | operations | tools | why this grouping |
|---|---|---|---|
{agents}

### Deterministic functions

No model decides these.

| function | operations | tools | why |
|---|---|---|---|
{funcs}

## Decision points

Each becomes a conditional edge. The routers in `graph.py` raise until you
implement them — several sit on cycles, so there is no safe default.

{decisions}

## Human approvals

`graph.py` sets `interrupt_before` on these, so the graph pauses and waits:

{chr(10).join(f"- `{h}`" for h in hitl) or "- none"}

## Layout

```
PROBLEM.md          the brief, verbatim
schema/
  spine.json        the domain model  - EDIT THIS
  schema.jsonld     Context Studio graph (generated)
  digest.json       planner input (generated)
topology.json       the agent plan  - EDIT THIS
graph.py            LangGraph wiring (generated - do not edit)
handlers.py         what an operation actually does  - YOURS
tools/              one module per system  - YOURS
tests/              smoke test
viewer/             open in a browser
```

## Running it

```bash
pip install langgraph
python -m pytest tests/ -q
```

## What to implement first

1. `tools/` — {", ".join(f"`{s}`" for s in systems[:4])}{" …" if len(systems) > 4 else ""}
2. `handlers.py` — `run_operation`
3. The routers in `graph.py`, by moving their logic into `handlers.py`

Generated {datetime.date.today().isoformat()}.
'''


def claude_md(digest, topo, brain) -> str:
    return f'''# {digest["domain"]}

Agentic platform scaffold. Read this before changing anything.

## Three tiers — keep them apart

| tier | files | rule |
|---|---|---|
| **source of truth** | `PROBLEM.md`, `schema/spine.json`, `topology.json` | edit these |
| **generated** | `schema/schema.jsonld`, `schema/digest.json`, `graph.py`, `tools/__init__.py` | **never hand-edit** — regenerate |
| **yours** | `handlers.py`, `tools/*.py`, `tests/` | written once, never regenerated |

**`graph.py` is generated.** If it is wrong, the fix is in `topology.json` or
`schema/spine.json`, then regenerate. Editing `graph.py` directly means the
next regeneration silently deletes your change.

`graph.py` imports `run_operation` from `handlers.py` and `TOOLS` from
`tools/` when they exist. That is what makes regeneration safe: the wiring is
generated, the behaviour is yours.

## Making changes

The builder lives at `{brain}`.

**The business changed** — a new state, a new step:
```bash
$EDITOR schema/spine.json
python {brain}/compiler/compile.py schema/spine.json -o schema/ --strict \\
    --jsonld-out schema/schema.jsonld --digest-out schema/digest.json
python {brain}/compiler/langgraph_gen.py schema/digest.json topology.json -o graph.py --strict
```
Re-run the planner only if the new operations need owners.

**The agent split is wrong** — merge two agents, move an operation:
```bash
$EDITOR topology.json
python {brain}/compiler/langgraph_gen.py schema/digest.json topology.json -o graph.py --strict
```
No model call. Pure codegen, seconds.

**The brief itself was wrong** — re-run from the top:
```bash
$EDITOR PROBLEM.md
/build-platform "$(cat PROBLEM.md)" .
```

Always commit before regenerating, so `git diff` shows exactly what moved.

## Rules the validators enforce

Both compilers exit non-zero on these; do not work around them.

- every state reachable from the initial state; no deadlocks
- terminal states have no outgoing transitions
- no isolated entities
- every operation owned by exactly one agent or function
- an agent's tools come only from its own operations
- one router per decision point

## Conventions

- Operations with `by: System` are **functions, not agents**. A model does not
  decide whether a payment settled.
- Routers raise rather than defaulting to a branch. Several decision points
  sit on cycles and an arbitrary default loops forever.
- `interrupt_before` marks human approvals. Resuming is
  `app.invoke(None, config)`.
'''


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="scaffold a project folder from generated artifacts")
    ap.add_argument("outdir")
    ap.add_argument("--spine", required=True)
    ap.add_argument("--digest", required=True)
    ap.add_argument("--topology", required=True)
    ap.add_argument("--graph", required=True)
    ap.add_argument("--jsonld")
    ap.add_argument("--problem", help="file holding the original brief")
    ap.add_argument("--no-git", action="store_true")
    a = ap.parse_args()

    out = pathlib.Path(a.outdir)
    spine = json.loads(pathlib.Path(a.spine).read_text())
    digest = json.loads(pathlib.Path(a.digest).read_text())
    topo = json.loads(pathlib.Path(a.topology).read_text())
    systems = sorted({s for o in digest["operations"] for s in o.get("sys", [])})
    used = {s: [o["n"] for o in digest["operations"] if s in o.get("sys", [])] for s in systems}

    acts = {}
    acts["schema/spine.json"] = write(out / "schema/spine.json", json.dumps(spine, indent=1))
    acts["schema/digest.json"] = write(out / "schema/digest.json", json.dumps(digest, indent=1))
    if a.jsonld:
        acts["schema/schema.jsonld"] = write(out / "schema/schema.jsonld",
                                             pathlib.Path(a.jsonld).read_text())
    acts["topology.json"] = write(out / "topology.json", json.dumps(topo, indent=1))
    acts["graph.py"] = write(out / "graph.py", pathlib.Path(a.graph).read_text())

    if a.problem:
        acts["PROBLEM.md"] = write(out / "PROBLEM.md", pathlib.Path(a.problem).read_text())

    # tier three - created once, never regenerated
    acts["handlers.py"] = write(out / "handlers.py", HANDLERS, once=True)
    for s in systems:
        acts[f"tools/{snake(s)}.py"] = write(out / "tools" / f"{snake(s)}.py",
                                             tool_module(s, used[s]), once=True)
    acts["tools/__init__.py"] = write(out / "tools/__init__.py", tools_init(systems))

    first = terminating_decisions(digest)
    acts["tests/test_smoke.py"] = write(out / "tests/test_smoke.py",
                                        smoke_test(digest, topo, first), once=True)

    viewer_src = ROOT / "viewer"
    if viewer_src.is_dir():
        shutil.copytree(viewer_src, out / "viewer", dirs_exist_ok=True)
        acts["viewer/"] = "copied"

    problem_text = pathlib.Path(a.problem).read_text() if a.problem else ""
    acts["README.md"] = write(out / "README.md",
                              readme(problem_text, spine, digest, topo, systems))
    acts["CLAUDE.md"] = write(out / "CLAUDE.md", claude_md(digest, topo, ROOT))
    acts[".gitignore"] = write(out / ".gitignore",
                               "__pycache__/\n*.py[cod]\n.venv/\n.env\n.pytest_cache/\n", once=True)

    state = {"stage": "complete", "domain": digest["domain"],
             "updated": datetime.datetime.now().isoformat(timespec="seconds"),
             "brain": str(ROOT)}
    write(out / ".pipeline.json", json.dumps(state, indent=1))

    for k, v in sorted(acts.items()):
        print(f"  {v:<8} {k}")

    if not a.no_git and not (out / ".git").exists():
        try:
            subprocess.run(["git", "init", "-q"], cwd=out, check=True)
            subprocess.run(["git", "add", "-A"], cwd=out, check=True)
            subprocess.run(["git", "commit", "-q", "-m",
                            f"Scaffold {digest['domain']} platform"], cwd=out, check=True)
            print("\n  git repo initialised, first commit made")
        except Exception as e:
            print(f"\n  (git init skipped: {e})")

    print(f"\nscaffolded {out}")


if __name__ == "__main__":
    main()
