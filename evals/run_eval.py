#!/usr/bin/env python3
"""
Eval harness: score the stage-1 builder across golden domains and providers.

Without this, "prompt v5 is better than v4" is a guess - which is exactly the
failure mode of the v3.2 assistant it replaces (version 3.2, 2.8 stars, no way
to tell whether any edit helped).

    python evals/run_eval.py -p groq                  # all domains, once
    python evals/run_eval.py -p groq -d claims -r 3   # determinism check
    python evals/run_eval.py -p groq --baseline evals/baseline.json
"""
from __future__ import annotations
import argparse, functools, hashlib, json, pathlib, statistics, sys, time

# unbuffered: an eval is long-running and its progress should be visible in a
# log or a pipe, not only when the process exits
print = functools.partial(__builtins__.print if not isinstance(__builtins__, dict)
                          else __builtins__["print"], flush=True)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv                                    # noqa: E402
load_dotenv(ROOT / ".env")
from builder.providers import get_llm                             # noqa: E402
from builder.spine_builder import build_spine                     # noqa: E402

DOMAINS = json.loads((ROOT / "evals" / "domains.json").read_text())


def spine_hash(spine) -> str:
    return hashlib.sha256(json.dumps(spine, sort_keys=True).encode()).hexdigest()[:12]


def score(res, secs) -> dict:
    """One run's metrics. 'coverage' is the share of operations carrying the
    actor/system/risk fields stage 2 needs to size the agent topology."""
    if not res.ok:
        return {"ok": False, "attempts": res.attempts, "secs": round(secs, 1),
                "errors": res.errors[:3]}
    d, sp = res.digest["counts"], res.spine
    ops = sp.get("O", [])
    covered = sum(1 for o in ops if o.get("by") and o.get("sys"))
    return {
        "ok": True, "attempts": res.attempts, "secs": round(secs, 1),
        "nodes": len(res.jsonld["@graph"]),
        "E": d["entities"], "S": d["states"], "O": d["operations"],
        "decisions": d["decisionPoints"],
        "actors": d["actors"], "systems": d["systems"],
        "hitl": d["humanInLoopOps"], "highRisk": d["highRiskOps"],
        "coverage": round(covered / max(1, len(ops)), 2),
        "warnings": len(res.warnings),
        "out_tokens": res.usage.get("output_tokens", 0),
        "hash": spine_hash(sp),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--provider", default=None)
    ap.add_argument("-m", "--model", default=None)
    ap.add_argument("-d", "--domain", action="append", help="limit to these domain ids")
    ap.add_argument("-r", "--repeat", type=int, default=1, help=">1 also checks determinism")
    ap.add_argument("-o", "--out", default="evals/report.json")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    ap.add_argument("--baseline", help="compare against a previous report")
    a = ap.parse_args()

    domains = [d for d in DOMAINS if not a.domain or d["id"] in a.domain]
    if not domains:
        sys.exit(f"no domains matched {a.domain}")

    llm = get_llm(a.provider, a.model, temperature=0.0)
    tag = f"{llm._sab_provider}/{llm._sab_model}"
    print(f"provider {tag}   domains={len(domains)}   repeat={a.repeat}\n")

    rows = []
    for d in domains:
        for rep in range(a.repeat):
            t0 = time.time()
            try:
                res = build_spine(d["text"], llm, on_event=lambda k, m: None)
                r = score(res, time.time() - t0)
            except Exception as e:
                r = {"ok": False, "attempts": 0, "secs": round(time.time() - t0, 1),
                     "errors": [f"{type(e).__name__}: {e}"[:160]]}
            r["domain"], r["run"] = d["id"], rep + 1
            rows.append(r)
            flag = "ok " if r["ok"] else "FAIL"
            extra = (f"{r['nodes']:>4} nodes  {r['E']}E {r['S']}S {r['O']}O  "
                     f"dec={r['decisions']}  cov={r['coverage']}  "
                     f"att={r['attempts']}  {r['secs']}s  {r['hash']}"
                     if r["ok"] else f"  {r['errors'][0][:90]}")
            print(f"  {flag} {d['id']:<14} {extra}")
            if a.pause and not (d is domains[-1] and rep == a.repeat - 1):
                time.sleep(a.pause)

    ok = [r for r in rows if r["ok"]]
    print(f"\n{'='*70}\npass rate      {len(ok)}/{len(rows)}")
    if ok:
        agg = lambda k: f"{statistics.mean(r[k] for r in ok):.2f}"
        print(f"mean attempts  {agg('attempts')}   (1.00 = no repair needed)")
        print(f"mean coverage  {agg('coverage')}   (1.00 = every op has actor+systems)")
        print(f"mean nodes     {agg('nodes')}")
        print(f"mean secs      {agg('secs')}")
        if any(r["out_tokens"] for r in ok):
            print(f"mean out tok   {agg('out_tokens')}")

    if a.repeat > 1:
        print("\ndeterminism (temperature=0, identical hash means identical spine)")
        for d in domains:
            hs = [r["hash"] for r in rows if r["domain"] == d["id"] and r["ok"]]
            verdict = "stable" if len(set(hs)) == 1 and hs else f"{len(set(hs))} distinct"
            print(f"  {d['id']:<14} {len(hs)} runs -> {verdict}  {sorted(set(hs))}")

    report = {"provider": tag, "when": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}
    pathlib.Path(a.out).write_text(json.dumps(report, indent=1))
    print(f"\nwrote {a.out}")

    if a.baseline:
        base = json.loads(pathlib.Path(a.baseline).read_text())
        bok = {r["domain"]: r for r in base["rows"] if r["ok"]}
        print(f"\nvs baseline ({base['provider']}, {base['when']})")
        for r in ok:
            b = bok.get(r["domain"])
            if not b:
                continue
            dn, da = r["nodes"] - b["nodes"], r["attempts"] - b["attempts"]
            print(f"  {r['domain']:<14} nodes {dn:+d}   attempts {da:+d}")
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
