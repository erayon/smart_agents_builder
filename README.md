# smart_agents_builder

Plain-English business brief in, a working agentic-platform project out.

```
brief ──▶ [LLM] ──▶ spine ──▶ [compiler] ──┬──▶ schema.jsonld   domain graph
                                           ├──▶ report          reachability, deadlocks
                                           └──▶ digest ──▶ [LLM] ──▶ topology
                                                                        │
                                                                   [compiler]
                                                                        │
                                                              a project folder
```

Two model calls. Everything else is deterministic Python: the compilers, both
validators, the code generators and the scaffolder. That split is what makes
the pipeline cheap, checkable and runnable without an API key.

This repo is the **builder**. The product is each folder it generates, which is
a self-contained project with its own `CLAUDE.md`, its own git history, and no
dependency on this repo at runtime.

---

## Two ways to run it

### With Claude Code — no API key

Only spine authoring and topology planning need a model, and Claude Code does
both through a skill and two subagents.

```
/build-platform health-claims-assistant-brief.md ~/platforms/health-claims
  domain modeller runs, repairs until valid, then STOPS

  you review: open the viewer, or edit schema/spine.json

/build-platform continue ~/platforms/health-claims
  agent architect plans the topology, shows you the split, then scaffolds
```

**Why it stops.** The validator proves a model is *well-formed*. It cannot
prove it is *true*. Nothing in a brief says claims can be withdrawn, or that a
technician can fall ill mid-visit — that lives in your head, and this is the
cheapest moment to add it. On a real 368-word brief the modeller reported **ten
assumptions it had to make** that the brief did not settle, several of which
were business decisions with money attached. All ten passed validation.

### With an API key — CI, batch, colleagues

```bash
python -m builder "a hospital admits patients, assigns beds, discharges them" -p groq
python -m builder plan out/hospital.digest.json -p groq
```

Same compilers. Providers: Anthropic, xAI (Grok), OpenAI, Google, Groq, Ollama,
and any OpenAI-compatible endpoint.

> xAI's **Grok** (`XAI_API_KEY`, `-p xai`) and the **Groq** inference service
> (`GROQ_API_KEY`, `-p groq`) are different companies. Both are supported.

---

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # only needed for the API path
.venv/bin/python -m builder --list-providers
.venv/bin/python tests/test_loop.py       # offline, no key, no network
```

---

## What gets generated

```
<project>/
  PROBLEM.md            the brief, verbatim              source of truth
  schema/
    spine.json          the domain model                 source of truth
    schema.jsonld       Context Studio graph             generated
    digest.json         planner input                    generated
  topology.json         the agent plan                   source of truth
  orchestrator/
    pipeline.py         StateGraph, one node per unit    generated
    state.py            PipelineState, typed             generated
    models.py           one payload model per entity     generated
  agents/<id>.py        SYSTEM_PROMPT + handle()         yours
  functions/<id>.py     deterministic, no model          yours
  tools/<system>.py     one client per system            yours
  tests/                smoke test
  viewer/               open in a browser
  CLAUDE.md             briefs the next session
  README.md             for humans
```

### Three tiers, kept apart

| tier | rule |
|---|---|
| **source of truth** | you edit these; everything else derives from them |
| **generated** | never hand-edit — regenerate |
| **yours** | written once if missing, **never** overwritten |

That boundary is what makes the pipeline survive contact with real work. Change
the business, edit the spine, regenerate: `orchestrator/` is rewritten and your
`agents/`, `functions/` and `tools/` implementations are untouched. Verified by
doing it, not by asserting it.

Changing the agent split is pure codegen — **no model call**, seconds.

---

## Granularity

| | `--granularity agent` (default) | `--granularity operation` |
|---|---|---|
| a node is | one agent or function | one state transition |
| health-claims | **7 nodes** | 54 nodes |
| generated | a package, 185-line pipeline | one 872-line file |
| state machine | in `state["status"]` | *is* the graph |
| use it for | development | proving reachability, the viewer |

Handoff edges at agent granularity are **derived**: unit U hands to V when U
owns an operation landing in a state V owns an operation leaving.

---

## Why a spine instead of emitting JSON-LD directly

The assistant this replaces made the model generate the `@context`, every
prefix, `"type":"State"` on every node and the pretty-printing — all
deterministic constants. On the same 8-entity domain:

| artifact | ~tokens | who pays |
|---|---|---|
| `spine.json` — the model writes this | **2,855** | model |
| `schema.jsonld` — compiled | 8,277 | free |
| `digest.json` — stage 2 reads this | 2,399 | free |

**66% fewer output tokens** for identical content.

The compiler also fixes defects a prose "quality gate" cannot catch, verified
against a real JSON-LD processor: `attributes` used to expand to an empty blank
node and lose every field; single-valued link properties round-tripped as bare
strings; the namespace squatted on domains nobody owns; `emitsEvent` pointed at
`State` nodes with no `Event` type existing.

---

## What the validators enforce

Non-negotiable, `--strict` exits non-zero:

**Domain model** — every state reachable from the initial state; no deadlocks;
terminal states have no outgoing transitions; an operation's endpoints belong to
its own entity; relation targets exist with valid cardinality; no isolated
entities; `creates` names a real, different entity.

**Topology** — every operation owned exactly once; an agent's tools appear in
its *own* operations (least privilege); one router per decision point;
`decidedBy` resolves.

**Advisory warnings** — a lifecycle entity with no origin, meaning nothing
creates it and it is not marked external; a lifecycle with no branch at all (no
failure path);
fewer than half the entities having a lifecycle; a `by: System` operation owned
by an agent; approaching one agent per operation; three or more agents with no
orchestrator.

Operations with `by: System` become **functions, not agents**. A model has no
business deciding whether a bank transfer settled.

### Read-only agents

Some questions change nothing. An agent that answers "where is my claim?"
owns no operations and declares `reads` instead:

```json
{"id": "status", "owns": [], "reads": ["Claim", "Assessment"], "tools": ["ClaimDB"]}
```

It is kept **out of the lifecycle graph** and given its own, entered when a
question arrives rather than when work does — an agent that can both answer and
act can move a claim while being asked about one. Its tools are drawn from the
systems touching the entities it reads, so it sees exactly what it answers
about. Only agents may be read-only; a function that changes nothing should not
exist.

### Where things come from

An operation's `from` and `to` must belong to one entity, so the moment a
*different* entity is born can never be a transition. Two ways to say it:

```json
{"n": "StartReview", "e": "Claim", "creates": ["Assessment"]}
{"n": "Claim", "origin": "external"}
```

`creates` becomes a real edge in the graph and reaches the generated code as a
`CREATES` list on the owning unit, so there is a named place to construct the
record. `origin: external` marks the things that arrive from outside — a
customer files them, a sensor raises them. A lifecycle entity with neither is
warned about, because otherwise nothing in the model says where it comes from.

---

## Viewer

```bash
xdg-open viewer/index.html          # drag a .jsonld onto the page
python3 -m http.server 8000         # or: /viewer/?src=../examples/claims.jsonld
```

No build step, no CDN, no network. It re-implements the validator in the
browser and draws a state machine per entity, marking decision points,
human-in-the-loop transitions and high-risk ones.

---

## Evals

```bash
.venv/bin/python evals/run_eval.py -p groq --pause 45        # 5 golden domains
.venv/bin/python evals/run_eval.py -p groq -d claims -r 3    # determinism
```

Scores validity, repair attempts, node counts, decision points, tokens, and
coverage — the share of operations carrying the actor and system fields stage 2
needs. Without this, "the next prompt is better" is a guess, which is how the
assistant this replaces reached version 3.2 at 2.8 stars with no way to tell.

---

## Verification status

What has been exercised, as opposed to written.

| area | status |
|---|---|
| Compiler output as RDF | **verified** — 0 blank nodes, via pyld |
| Both validators catch planted defects | **verified** |
| Repair loop | **verified** offline, no key or network |
| Browser validator agrees with the Python one | **verified** |
| Live generation, Groq | **verified** |
| Claude Code path, end to end | **verified** — two real domains |
| Agent-granularity codegen | **verified** — compiles, graph builds, tests pass |
| Regeneration preserves your code | **verified** — edited, regenerated, survived |
| Eval across 5 domains | **run** — 4/5, mean 2.25 attempts |
| Determinism at `temperature=0` | **fails** — see below |
| Live: xAI Grok, Anthropic, OpenAI, Google | **not verified** — wired, no key |
| Ollama | **partial** — a 4 GB GPU cannot hold a useful model plus a 16k context |
| `--mode agent` (API path) | **not verified** — falls back to chain mode |
| Constrained decoding | **not implemented** |

---

## Known gaps

1. **`temperature=0` is not reproducible on Groq.** Two runs of one domain, same
   prompt and same input, gave `6E 12S 10O` and `5E 10S 10O` — different models,
   different hashes. The setting is applied; the provider does not honour it as
   determinism. Do not assume re-running gives the same schema.

2. **The viewer only reads the domain graph**, not the agent topology, which is
   now the more interesting artifact.

3. **The pipeline has outgrown Groq's free tier.** A correctly sized schema
   averages ~9,700 output tokens against an 8,000 TPM cap. That is arithmetic,
   not a bug — and an argument for the Claude Code path.

---

## Layout

```
prompts/01_schema_builder_v4.md   brief -> spine
prompts/02_agent_planner.md       digest -> topology
spec/spine.schema.json            structural pre-filter
spec/topology.schema.json         structural pre-filter
compiler/compile.py               spine -> jsonld + report + digest
compiler/langgraph_gen.py         topology validator + operation granularity
compiler/pipeline_gen.py          agent granularity: orchestrator/, agents/, functions/
compiler/scaffold.py              the project folder, docs, git init
builder/                          the API path: providers, repair loop, retry, CLI
.claude/skills/build-platform/    the Claude Code path
.claude/agents/                   domain-modeller, agent-architect
evals/                            golden domains + scoring
viewer/                           dependency-free browser viewer
examples/                         a worked domain, end to end
tests/test_loop.py                offline test, no key or network
```
