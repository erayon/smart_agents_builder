---
name: build-platform
description: Turn a plain-English business brief into a complete agentic platform scaffold - domain model, agent topology, runnable LangGraph, tool stubs, CLAUDE.md and README.md - in its own folder. Stops after the domain model for human review. No API key needed.
trigger: /build-platform
---

# /build-platform

Plain English in, a working agentic-platform project out.

## Usage

```
/build-platform "<the brief>" <outdir>     # stage 1, then stop for review
/build-platform <file.md> <outdir>         # brief from a file
/build-platform continue <outdir>          # stage 2, after you have reviewed
/build-platform continue <outdir> --yes    # skip the topology review too
/build-platform <brief> <outdir> --auto    # run everything, no stops
```

## What it produces

```
<outdir>/
  PROBLEM.md            the brief, verbatim        <- source of truth
  schema/spine.json     the domain model           <- source of truth, edit freely
  schema/*.jsonld       Context Studio graph       <- generated
  topology.json         the agent plan             <- source of truth
  orchestrator/
    pipeline.py         StateGraph, one node per unit   <- generated
    state.py            PipelineState, typed            <- generated
    models.py           one payload model per entity    <- generated
  agents/<id>.py        SYSTEM_PROMPT + handle()   <- yours, written once
  functions/<id>.py     deterministic, no model    <- yours, written once
  tools/<system>.py     one module per system      <- yours, written once
  tests/                smoke test
  viewer/               open in a browser
  CLAUDE.md             briefs the next session
  README.md             for humans
```

One node per **agent**, not per state transition. The state machine lives in
`state["status"]`; handoff edges between units are derived from it. Use
`--granularity operation` for the exhaustive graph instead - a node per
transition, useful for proving reachability, unwieldy to develop against.

## Find the brain first

Everything runs from the smart_agents_builder repo. Locate it in this order
and store it as `$BRAIN`:

1. `$SMART_AGENTS_BUILDER` if set
2. the current directory, if `compiler/compile.py` exists in it
3. `~/working_dir/Adv/smart_agents_builder` if it exists
4. otherwise ask the user, once

Use `$BRAIN/.venv/bin/python` for every command. Never plain `python`.

---

# Stage 1 — domain model

Run this when the first argument is **not** `continue`.

1. Create `<outdir>`. Write the brief verbatim to `<outdir>/PROBLEM.md`. If the
   brief came from a file, copy it; never paraphrase it.

2. Delegate to the **domain-modeller** subagent. Give it, explicitly:
   - `$BRAIN` and `$OUT` as absolute paths
   - the full brief text
   - the instruction to read `$BRAIN/prompts/01_schema_builder_v4.md` first
   - the instruction to run `compile.py --strict` itself and repair until clean

   Use one subagent. Do not model the domain yourself in the main thread — it
   needs a clean context window for the prompt plus the brief.

3. When it returns, verify yourself, do not take its word:
   ```bash
   $BRAIN/.venv/bin/python $BRAIN/compiler/compile.py $OUT/schema/spine.json -o $OUT/schema/ --strict \
       --jsonld-out $OUT/schema/schema.jsonld --digest-out $OUT/schema/digest.json
   ```
   If that fails, send the subagent back with the errors.

4. Copy the viewer so the user can look at it:
   ```bash
   cp -r $BRAIN/viewer $OUT/viewer
   ```

5. Write `$OUT/.pipeline.json`:
   ```json
   {"stage": "awaiting-review", "brain": "<$BRAIN>", "updated": "<iso8601>"}
   ```

6. **Stop.** Report to the user:
   - the counts, and which entities have lifecycles
   - the decision points, by name
   - anything the subagent flagged as ambiguous in the brief
   - how to review:
     ```
     open <outdir>/viewer/index.html   and drop schema/schema.jsonld on it
     edit <outdir>/schema/spine.json   to correct the model
     then: /build-platform continue <outdir>
     ```

   Do not run stage 2. Do not ask "shall I continue?" and then continue anyway.
   The point of stopping is that a human looks at the model. The validator can
   prove a model is well-formed; only the user knows whether it is *true*.

   Skip the stop only if `--auto` was passed.

---

# Stage 2 — agent topology and scaffold

Run this when the first argument is `continue`, or straight after stage 1 when
`--auto` was passed.

1. Read `<outdir>/.pipeline.json` for `$BRAIN`. If it is missing, locate the
   brain as above.

2. **Recompile before anything else.** The user may have edited the spine, and
   the digest that stage 2 reads would be stale:
   ```bash
   $BRAIN/.venv/bin/python $BRAIN/compiler/compile.py $OUT/schema/spine.json -o $OUT/schema/ --strict \
       --jsonld-out $OUT/schema/schema.jsonld --digest-out $OUT/schema/digest.json
   ```
   If this fails, the user's edits broke a rule. Show them the errors, offer to
   fix, and stop. **Never plan against a stale digest.**

3. Delegate to the **agent-architect** subagent with `$BRAIN` and `$OUT`.

4. Verify its output yourself. `--check` writes nothing, so the user still
   sees the split before any code exists:
   ```bash
   $BRAIN/.venv/bin/python $BRAIN/compiler/pipeline_gen.py \
       $OUT/schema/digest.json $OUT/topology.json \
       --spine $OUT/schema/spine.json --check --strict
   ```

5. Unless `--yes` or `--auto`, show the user the agent split — each agent, its
   operation count, its tools and its one-line reason — and ask whether to
   scaffold. This is a short read and the agent count is the decision their
   team will argue about.

6. Scaffold:
   ```bash
   $BRAIN/.venv/bin/python $BRAIN/compiler/scaffold.py $OUT \
       --spine $OUT/schema/spine.json --digest $OUT/schema/digest.json \
       --topology $OUT/topology.json \
       --jsonld $OUT/schema/schema.jsonld --problem $OUT/PROBLEM.md
   ```
   This writes `orchestrator/`, one module per agent and function, `tools/`,
   `tests/`, `README.md` and `CLAUDE.md`, and makes the first git commit.
   Files in the "yours" tier are created only when missing, so re-running
   never destroys an implementation.

7. Run the smoke test:
   ```bash
   cd $OUT && $BRAIN/.venv/bin/python -m pytest tests/ -q
   ```

8. Report: agents, functions, tools, interrupts, the entry unit, whether the
   smoke test passed, and what to implement first. Say plainly that nothing
   **runs** yet - every `handle()` raises `NotImplementedError` by design, and
   the smoke test asserts wiring, not behaviour.

---

## Rules

- **Never hand-edit `orchestrator/`.** `pipeline.py`, `state.py` and
  `models.py` are generated. Fix `topology.json` or `schema/spine.json` and
  regenerate. The modules under `agents/`, `functions/` and `tools/` are the
  user's and are never overwritten.
- **Never skip `--strict`.** A pipeline that generates an invalid model is
  worse than one that fails.
- **Never write the spine yourself in the main thread.** Use the subagent; it
  has the right context and the loop is cheaper there.
- If the brief is too vague to model — one line, no nouns — ask **one** round
  of questions before starting. Do not ask after stage 1 has run.
- Re-running stage 2 on an existing folder is safe and expected. That is how
  the user changes the agent split.
