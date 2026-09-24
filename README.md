# smart_agents_builder

Two-stage pipeline: business domain text → Context Studio schema → LangGraph agent topology.

```
domain text ──▶ [LLM #1: 01_schema_builder_v4] ──▶ spine.json
                                                      │
                                    compile.py (deterministic, 0 tokens)
                                                      │
                        ┌─────────────────────────────┼──────────────────────────┐
                        ▼                             ▼                          ▼
                 schema.jsonld               validation report            digest.json
              (Context Studio)          (reachability / liveness)              │
                                                                               ▼
                                                        [LLM #2: 02_agent_planner] ──▶ LangGraph topology
```

## Why a spine instead of direct JSON-LD

The v3.2 assistant made the model generate `@context`, every `clm:` prefix,
`"type":"State"` on every node, and the pretty-printing — all of it
deterministic constants. Measured on the same 8-entity claims domain:

| artifact | ~tokens | who pays |
|---|---|---|
| spine.json — **the model writes this** | **2,855** | model |
| schema.jsonld — compiled | 8,277 | free |
| digest.json — stage 2 reads this | 2,399 | free |

**66% fewer output tokens** for identical content, and stage 2 reads a 2.4k
digest instead of an 8.3k JSON-LD blob. The v3.2 prompt shrank from 7.3k to
4.2k chars too, since the compiler owns the format.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add at least one API key
.venv/bin/python -m builder --list-providers

.venv/bin/python -m builder "a hospital admits patients, assigns them beds, \
  runs tests, and discharges them" --provider xai
```

Writes `out/<slug>.spine.json`, `.jsonld` and `.digest.json`.

### Providers

`anthropic` (Claude) · `xai` (**Grok**) · `openai` · `google` (Gemini) ·
`groq` · `ollama` (local) · `openai_compat` (OpenRouter / DeepSeek / Together /
vLLM). `--provider auto` picks the first one that has both a key and its
package installed.

> xAI's **Grok** (`XAI_API_KEY`, `--provider xai`) and the **Groq** inference
> service (`GROQ_API_KEY`, `--provider groq`) are different companies with
> confusingly similar names. Both are supported.

Default model ids live in `builder/providers.py` and are overridable with
`--model` or `<PROVIDER>_MODEL` in `.env`. Check them against your provider's
current catalogue.

### Two modes

| mode | how it works | when |
|---|---|---|
| `--mode chain` *(default)* | generate → validate → feed errors back → repair, up to `--max-repairs` | production; predictable cost, bounded retries |
| `--mode agent` | LangChain tool-calling agent; the model calls `validate_spine` itself and iterates until VALID | exploratory domains, self-correction without a fixed budget |

Both modes gate on the **same deterministic validator**. Schema-constrained
decoding cannot catch unreachable states, deadlocks or cross-wired
transitions, so the validator — not the JSON Schema — is the real contract.
`spec/spine.schema.json` is applied first as a cheap structural pre-filter.

### Running against local Ollama

Local removes rate limits but **not** context limits. Two settings matter:

```bash
OLLAMA_NUM_CTX=16384    # Ollama defaults to 4096, which truncates the spine
OLLAMA_JSON=1           # native JSON mode, on by default; small models need it
```

```bash
.venv/bin/python -m builder "..." -p ollama -m qwen3:4b-instruct
```

**Fit the model in VRAM.** A 4 GB card running a 7B Q4 model (~4.7 GB, ~6.1 GB
resident with a 16k context) spills to CPU and drops to a crawl — check with
`ollama ps`, which shows the CPU/GPU split. On 4 GB prefer a 3-4B model:
`qwen3:4b-instruct`, `qwen2.5-coder:3b`, `llama3.2:3b`. Raising `OLLAMA_NUM_CTX`
also grows the KV cache, so context and model size compete for the same VRAM.

### Rate limits and oversized requests

`builder/retry.py` handles both without configuration. It parses the
provider's own numbers out of the error (`Limit 8000, Requested 10572`) and
shrinks `max_tokens` to fit, then backs off exponentially on 429/TPM errors,
honouring `try again in Ns` when the provider sends it.

Set `LLM_MAX_TOKENS` to cap output globally. The default is 16000 because
several providers default to 1-4k and truncate the spine mid-object — on the
first live run that cost 4 attempts and 20,850 tokens; setting it explicitly
brought the same domain down to 1 attempt and 7,201 tokens.

> On a low per-minute quota, prefer `--mode chain`. Agent mode sends the whole
> spine through a tool-call argument, which roughly doubles its cost; if the
> provider then fails the tool call, the builder falls back to chain mode
> automatically.

### Offline test

```bash
.venv/bin/python tests/test_loop.py    # no API key, no network
```

Feeds a fake model a spine with three planted defects, asserts the loop
rejects it, repairs on attempt 2, and compiles to 65 nodes.

## Compiler usage


```bash
python3 compiler/compile.py examples/claims.spine.json -o examples/
python3 compiler/compile.py my.spine.json -o out/ --strict   # exit 1 on errors
```

Use `spec/spine.schema.json` as the constrained-decoding / structured-output
schema for LLM #1 so malformed spines are impossible rather than merely
discouraged.

## Gaps closed

| # | Gap in v3.2 | Fix | Where |
|---|---|---|---|
| A1 | `attributes` expanded to an empty blank node — all field data lost; also violated the prompt's own "no blank nodes" rule | `@type: @json` literal | compiler |
| A2 | No `@container: @set`; single-value link properties round-tripped as strings | `@container: @set` on every multi-valued link | compiler |
| A3 | Squatted `ontology.<domain>.org` | `urn:ctxstudio:<ns>:` | compiler |
| A4 | `via` declared, never used | removed | compiler |
| A5 | Undeclared terms silently dropped on expansion | `@version: 1.1` + `@vocab` | compiler |
| A6 | No document identity or version | `schema:Dataset` node **inside** `@graph` (a root `@id` would make it a named graph) | compiler |
| B7 | `emitsEvent` pointed at State nodes; no Event type | real `Event` nodes, derived from `O[].emit` | compiler |
| B8 | State machines could be unreachable, deadlocked, cross-wired; Operations had no owning entity | `ownedBy` + 6 enforced rules | validator |
| B9 | Operations had no actor, system, HITL or risk | `by`, `sys`, `hitl`, `risk` | spine |
| B10 | `relatesTo` untyped and directionless | `rels` carries cardinality + label alongside visual `relatesTo` | spine + compiler |
| B11 | No decision points | derived: any state with outgoing fan-out > 1 | compiler |
| C13 | "code block" vs "output only JSON" contradiction | one unambiguous output contract | prompt |
| C14 | Three conflicting size signals | one sizing guide with a stated rationale | prompt |
| C15 | No worked example | `examples/claims.spine.json` | examples |
| C21 | Rules were prose, unverifiable | JSON Schema + executable validator | spec + compiler |
| D22 | `max_tokens: 10000` vs "do not truncate" | output is 66% smaller; 8-entity domain costs 2.9k not 8.3k | architecture |

Still to set on the platform: `temperature: 0` (D24), structured output bound
to `spine.schema.json` (D25), drop the dead `budget_tokens` or enable extended
thinking (D23/C16), and add `samplePrompts` (C19).

## Layout

```
builder/providers.py              multi-provider LLM factory (incl. Grok)
builder/spine_builder.py          generate -> validate -> repair loop
builder/agent.py                  tool-calling agent mode
builder/tools.py                  validator exposed as LangChain tools
builder/cli.py                    python -m builder
tests/test_loop.py                offline end-to-end test
prompts/01_schema_builder_v4.md   stage 1 prompt (spine, not JSON-LD)
prompts/02_agent_planner.md       stage 2 prompt (digest -> LangGraph)   [next]
spec/spine.schema.json            constrained-decoding schema for stage 1
compiler/compile.py               spine -> jsonld + report + digest
examples/claims.spine.json        worked example, 8 entities
examples/claims.jsonld            compiled, 65 nodes, 517 triples, 0 blank nodes
examples/claims.digest.json       stage 2 input
```
