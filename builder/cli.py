"""
smart_agents_builder CLI - plain English in, Context Studio JSON-LD out.

    python -m builder "a hospital admits patients, assigns beds, discharges them"
    python -m builder --file domain.txt --provider xai --model grok-4
    python -m builder "procurement" --mode agent --provider anthropic
    python -m builder --list-providers
"""
from __future__ import annotations
import argparse, json, os, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


C = {"dim": "\033[2m", "red": "\033[31m", "grn": "\033[32m",
     "yel": "\033[33m", "cya": "\033[36m", "b": "\033[1m", "x": "\033[0m"}
if not sys.stderr.isatty() or os.getenv("NO_COLOR"):
    C = {k: "" for k in C}


def event_printer(quiet: bool):
    def emit(kind, msg):
        if quiet:
            return
        col = {"call": C["cya"], "ok": C["grn"], "fail": C["yel"],
               "tool": C["cya"], "detail": C["dim"]}.get(kind, "")
        print(f"{col}{msg}{C['x']}", file=sys.stderr)
    return emit


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "domain").rstrip("-")


def plan_main(argv):
    """Stage 2: digest -> agent topology -> runnable LangGraph module."""
    _load_env()
    from builder.providers import get_llm

    ap = argparse.ArgumentParser(prog="python -m builder plan",
                                 description="digest.json -> agent topology -> graph.py")
    ap.add_argument("digest", help="a .digest.json from stage 1")
    ap.add_argument("-p", "--provider", default=None)
    ap.add_argument("-m", "--model", default=None)
    ap.add_argument("-t", "--temperature", type=float, default=0.0)
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("-n", "--name", default=None)
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    digest = json.loads(pathlib.Path(a.digest).read_text())
    say = event_printer(a.quiet)
    try:
        llm = get_llm(a.provider, a.model, a.temperature)
    except Exception as e:
        print(f"{C['red']}{e}{C['x']}", file=sys.stderr)
        return 2

    from builder.topology_builder import build_topology
    say("call", f"{C['b']}provider{C['x']} {llm._sab_provider}/{llm._sab_model}  "
                f"domain={digest['domain']}")
    res = build_topology(digest, llm, max_repairs=a.max_repairs, on_event=say)

    stem = a.name or pathlib.Path(a.digest).name.replace(".digest.json", "")
    out = pathlib.Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)

    if not res.ok:
        print(f"\n{C['red']}FAILED after {res.attempts} attempts{C['x']}", file=sys.stderr)
        for e in res.errors[:20]:
            print(f"  {e}", file=sys.stderr)
        if res.topology:
            (out / f"{stem}.topology.invalid.json").write_text(json.dumps(res.topology, indent=1))
        return 1

    (out / f"{stem}.topology.json").write_text(json.dumps(res.topology, indent=1))
    (out / f"{stem}_graph.py").write_text(res.source)
    t = res.topology
    tools = {s for o in digest["operations"] for s in o.get("sys", [])}
    print(f"\n{C['grn']}{C['b']}OK{C['x']}  {len(t['agents'])} agents  "
          f"{len(t['functions'])} functions  {len(tools)} tools  "
          f"{len(t['routers'])} routers  attempts={res.attempts}")
    for a_ in t["agents"]:
        print(f"  {C['cya']}{a_['id']:<14}{C['x']} {len(a_['owns']):>2} ops  "
              f"tools={','.join(a_['tools'])}")
    for f_ in t["functions"]:
        print(f"  {C['dim']}{f_['id']:<14}{C['x']} {len(f_['owns']):>2} ops  (deterministic)")
    if res.usage:
        print(f"{C['dim']}tokens: " + "  ".join(f"{k}={v}" for k, v in res.usage.items()) + C["x"])
    for w in res.warnings[:10]:
        print(f"{C['yel']}  WARN {w}{C['x']}")
    print(f"  topology     {out / f'{stem}.topology.json'}")
    print(f"  graph        {out / f'{stem}_graph.py'}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "plan":
        return plan_main(argv[1:])
    _load_env()
    from builder.providers import get_llm, available_providers, PROVIDERS

    ap = argparse.ArgumentParser(prog="python -m builder",
                                 description="Plain English -> Context Studio JSON-LD + agent digest")
    ap.add_argument("description", nargs="?", help="the business domain, in plain English")
    ap.add_argument("-f", "--file", help="read the description from a file ('-' for stdin)")
    ap.add_argument("-p", "--provider", default=None,
                    help=f"{', '.join(PROVIDERS)} (default: auto)")
    ap.add_argument("-m", "--model", default=None, help="override the model id")
    ap.add_argument("-t", "--temperature", type=float, default=0.0)
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("-n", "--name", default=None, help="output file stem")
    ap.add_argument("--mode", choices=["chain", "agent"], default="chain")
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=12, help="agent mode only")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--list-providers", action="store_true")
    a = ap.parse_args(argv)

    if a.list_providers:
        ready = set(available_providers())
        print(f"{'provider':<16}{'key':<26}{'default model':<32}status")
        print("-" * 86)
        for n, p in PROVIDERS.items():
            if n == "mock":
                continue
            mark = f"{C['grn']}ready{C['x']}" if n in ready else f"{C['dim']}no key{C['x']}"
            print(f"{n:<16}{p.env_key or '-':<26}{p.default_model:<32}{mark}")
        return 0

    if a.file:
        desc = sys.stdin.read() if a.file == "-" else pathlib.Path(a.file).read_text()
    elif a.description:
        desc = a.description
    else:
        ap.error("give a description, --file, or --list-providers")

    if len(desc.strip()) < 10:
        ap.error("description is too short to model a domain from")

    say = event_printer(a.quiet)
    try:
        llm = get_llm(a.provider, a.model, a.temperature)
    except Exception as e:
        print(f"{C['red']}{e}{C['x']}", file=sys.stderr)
        return 2

    say("call", f"{C['b']}provider{C['x']} {llm._sab_provider}/{llm._sab_model}  "
                f"mode={a.mode}  temperature={a.temperature}")

    if a.mode == "agent":
        from builder.agent import build_spine_agentic
        res = build_spine_agentic(desc, llm, max_steps=a.max_steps, on_event=say)
    else:
        from builder.spine_builder import build_spine
        res = build_spine(desc, llm, max_repairs=a.max_repairs, on_event=say)

    from builder.spine_builder import write_artifacts
    stem = a.name or slugify(desc.split("\n")[0])

    if not res.ok:
        print(f"\n{C['red']}FAILED after {res.attempts} attempts{C['x']}", file=sys.stderr)
        for e in res.errors[:20]:
            print(f"  {e}", file=sys.stderr)
        if res.spine:
            written = write_artifacts(res, a.outdir, stem + ".invalid")
            print(f"  {C['dim']}last attempt saved to "
                  f"{written.get('spine.json')}{C['x']}", file=sys.stderr)
        return 1

    written = write_artifacts(res, a.outdir, stem)
    d = res.digest["counts"]
    print(f"\n{C['grn']}{C['b']}OK{C['x']}  {len(res.jsonld['@graph'])} nodes  "
          f"({d['entities']}E {d['states']}S {d['operations']}O "
          f"{d['decisionPoints']} decision points)  "
          f"attempts={res.attempts}")
    if res.usage:
        print(f"{C['dim']}tokens: " + "  ".join(f"{k}={v}" for k, v in res.usage.items()) + C["x"])
    for w in res.warnings[:10]:
        print(f"{C['yel']}  WARN {w}{C['x']}")
    for k, v in written.items():
        print(f"  {k:<12} {v}")
    print(f"\nnext: feed {written.get('digest.json')} to stage 2 (agent planner)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
