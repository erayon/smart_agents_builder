#!/usr/bin/env python3
"""
spine.json -> (1) Context Studio JSON-LD  (2) validation report  (3) agent-planning digest

The LLM emits only the compact "spine". Everything derivable is derived here:
  - @context, node IDs, prefixes, Event nodes, reverse links
  - decision points (states with outgoing fan-out > 1)
  - candidate agent clusters (by entity / by actor / by system)

Fixes carried by this compiler (see README gap list):
  A1 attributes as @json literal (no more empty blank node)
  A2 @container:@set on every multi-valued link property
  A3 urn: namespace, no squatting on ontology.<domain>.org
  A4 no dead terms
  A5 @version 1.1 + @vocab fallback so unknown terms survive expansion
  A6 document @id / version / generatedAt
  B7 real Event nodes, distinct from State
  B8 Operations declare their owning Entity; reachability + liveness enforced
  B10 typed, cardinal relations preserved alongside visual relatesTo
"""
import argparse, json, pathlib, sys, datetime
from collections import OrderedDict, defaultdict, deque

CARDS = {"1-1", "1-n", "n-1", "n-n"}
RISKS = {"low", "medium", "high"}


# ---------------------------------------------------------------- helpers
def qstate(entity, state):
    """Qualified state local-name: Claim + Draft -> ClaimDraft (never ClaimClaimDraft)."""
    return state if state.startswith(entity) else entity + state


def approx_tokens(s):
    return round(len(s) / 3.5)


class Report:
    def __init__(self):
        self.errors, self.warns = [], []

    def err(self, code, msg):
        self.errors.append((code, msg))

    def warn(self, code, msg):
        self.warns.append((code, msg))

    def ok(self):
        return not self.errors

    def render(self):
        out = []
        for c, m in self.errors:
            out.append(f"  ERROR  [{c}] {m}")
        for c, m in self.warns:
            out.append(f"  WARN   [{c}] {m}")
        if not out:
            out.append("  clean - no errors, no warnings")
        return "\n".join(out)


# ---------------------------------------------------------------- @context
def build_context(ns, base):
    def linkset(p):
        return {"@id": f"{ns}:{p}", "@type": "@id", "@container": "@set"}

    def valset(p):
        return {"@id": f"{ns}:{p}", "@container": "@set"}

    return OrderedDict([
        ("@version", 1.1),
        ("@vocab", base),                     # A5: unknown terms survive expansion
        (ns, base),
        ("schema", "http://schema.org/"),
        ("id", "@id"), ("type", "@type"),
        ("name", "schema:name"),
        ("description", "schema:description"),
        ("attributes", {"@id": f"{ns}:attributes", "@type": "@json"}),   # A1
        ("rels",       {"@id": f"{ns}:rels",       "@type": "@json"}),   # B10
        ("identityKey", f"{ns}:identityKey"),
        ("humanRef",    f"{ns}:humanRef"),
        ("invariant",     valset("invariant")),
        ("precondition",  valset("precondition")),
        ("postcondition", valset("postcondition")),
        ("performedBy",   f"{ns}:performedBy"),
        ("usesSystem",    valset("usesSystem")),
        ("requiresHuman", f"{ns}:requiresHuman"),
        ("risk",          f"{ns}:risk"),
        ("isDecisionPoint", f"{ns}:isDecisionPoint"),
        ("hasState",       linkset("hasState")),                          # A2
        ("initialState",  {"@id": f"{ns}:initialState", "@type": "@id"}),
        ("terminalStates", linkset("terminalStates")),
        ("relatesTo",      linkset("relatesTo")),
        ("emitsEvent",     linkset("emitsEvent")),
        ("ownedBy",       {"@id": f"{ns}:ownedBy", "@type": "@id"}),      # B8
        ("from",          {"@id": f"{ns}:from", "@type": "@id"}),
        ("to",            {"@id": f"{ns}:to",   "@type": "@id"}),
        ("Entity",    f"{ns}:Entity"),
        ("State",     f"{ns}:State"),
        ("Operation", f"{ns}:Operation"),
        ("Event",     f"{ns}:Event"),                                     # B7
    ])


# ---------------------------------------------------------------- validate
def validate(spine, rep):
    ns = spine.get("ns")
    if not ns:
        rep.err("SPINE", "missing 'ns'")
    ents = {e["n"]: e for e in spine.get("E", [])}
    if not ents:
        rep.err("SPINE", "no entities in 'E'")
    sdesc = spine.get("S", {})
    ops = spine.get("O", [])

    if len(ents) != len(spine.get("E", [])):
        rep.err("DUP", "duplicate entity names in E")
    seen_ops = set()
    for o in ops:
        if o["n"] in seen_ops:
            rep.err("DUP", f"duplicate operation name {o['n']}")
        seen_ops.add(o["n"])

    # per-entity lifecycle integrity
    for name, e in ents.items():
        states = e.get("s", [])
        if states:
            if e.get("s0") not in states:
                rep.err("B8", f"{name}: initialState '{e.get('s0')}' not in its states")
            for t in e.get("sT", []):
                if t not in states:
                    rep.err("B8", f"{name}: terminal state '{t}' not in its states")
            if not e.get("sT"):
                rep.warn("B8", f"{name}: lifecycle entity with no terminal states")
        for r in e.get("r", []):
            tgt, card = r[0], r[1]
            if tgt not in ents:
                rep.err("B10", f"{name}: relation target '{tgt}' is not an entity")
            if card not in CARDS:
                rep.err("B10", f"{name}: bad cardinality '{card}' (use {sorted(CARDS)})")
        for st in states:
            if st not in sdesc and f"{name}.{st}" not in sdesc:
                rep.warn("DESC", f"{name}.{st}: no state description in S")

    # operations
    out_edges = defaultdict(list)   # (entity, state) -> [(op, to_state)]
    for o in ops:
        en = o.get("e")
        if en not in ents:
            rep.err("B8", f"{o['n']}: owning entity '{en}' does not exist")
            continue
        states = ents[en].get("s", [])
        for side in ("f", "t"):
            if o.get(side) not in states:
                rep.err("B8", f"{o['n']}: '{o.get(side)}' is not a state of {en}")
        if o.get("risk") and o["risk"] not in RISKS:
            rep.warn("CFG", f"{o['n']}: risk '{o['risk']}' not in {sorted(RISKS)}")
        if o.get("f") in states and o.get("t") in states:
            out_edges[(en, o["f"])].append((o["n"], o["t"]))

    # reachability + liveness per entity
    for name, e in ents.items():
        states = e.get("s", [])
        if not states:
            continue
        seen, q = {e.get("s0")}, deque([e.get("s0")])
        while q:
            cur = q.popleft()
            for _, nxt in out_edges[(name, cur)]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        for st in states:
            if st not in seen:
                rep.err("B8", f"{name}.{st}: unreachable from initialState '{e.get('s0')}'")
        for st in states:
            fanout = out_edges[(name, st)]
            if st in e.get("sT", []) and fanout:
                rep.err("B8", f"{name}.{st}: terminal state has outgoing operations "
                              f"({', '.join(o for o, _ in fanout)})")
            if st not in e.get("sT", []) and not fanout:
                rep.err("B8", f"{name}.{st}: non-terminal state has no outgoing operation (deadlock)")

    # connectivity: every entity must take part in at least one relation, in
    # either direction. v3.2 banned isolated nodes outright and it was right to
    # - a business object nothing references is almost always a modelling miss.
    related = set()
    for name, e in ents.items():
        for r in e.get("r", []):
            related.add(name)
            related.add(r[0])
    for name, e in ents.items():
        if name not in related:
            rep.err("A6", f"{name}: isolated entity - no relation to or from any "
                          f"other entity (add it to 'r', or reference it from one)")

    # sizing: advisory, but the eval showed models under-modelling badly
    # against the prompt's own guidance, so make it visible rather than silent.
    lifecycle = [e for e in ents.values() if e.get("s")]
    n_states = sum(len(e.get("s", [])) for e in ents.values())
    if ents and len(lifecycle) < len(ents) / 2:
        rep.warn("SIZE", f"only {len(lifecycle)} of {len(ents)} entities have a lifecycle; "
                         f"most operational entities should have one")
    # A purely linear lifecycle needs only states-1 transitions, so "ops < states"
    # is normal and not a signal. What does signal under-modelling is a lifecycle
    # with no branch at all: no failure path, no decision, one way through.
    for e in lifecycle:
        fan = {s: sum(1 for o in ops if o.get("e") == e["n"] and o.get("f") == s)
               for s in e["s"]}
        if e["s"] and max(fan.values(), default=0) < 2 and len(e["s"]) > 2:
            rep.warn("SIZE", f"{e['n']}: lifecycle is entirely linear - no state has two "
                             f"ways out, so there is no failure or rejection path")
    for e in lifecycle:
        if len(e["s"]) < 3:
            rep.warn("SIZE", f"{e['n']}: only {len(e['s'])} states; a lifecycle worth "
                             f"modelling usually has 4-7")

    return ents, sdesc, ops, out_edges


# ---------------------------------------------------------------- emit
def compile_jsonld(spine, ents, sdesc, ops, out_edges):
    ns = spine["ns"]
    base = spine.get("base") or f"urn:ctxstudio:{ns}:"      # A3
    P = lambda local: f"{ns}:{local}"
    graph = []

    # events: derived from operations, never hand-written            (B7, derive-don't-ask)
    events = defaultdict(set)      # event name -> {emitting entity}
    ev_by_entity = defaultdict(set)
    for o in ops:
        for ev in o.get("emit", []):
            events[ev].add(o["n"])
            ev_by_entity[o["e"]].add(ev)

    for name, e in ents.items():
        node = OrderedDict([("id", P(name)), ("type", "Entity"), ("name", name),
                            ("description", e.get("d", ""))])
        if e.get("k"): node["identityKey"] = e["k"]
        if e.get("h"): node["humanRef"] = e["h"]
        if e.get("a"): node["attributes"] = e["a"]
        if e.get("inv"): node["invariant"] = e["inv"]
        if e.get("s"):
            node["hasState"] = [P(qstate(name, s)) for s in e["s"]]
            node["initialState"] = P(qstate(name, e["s0"]))
            node["terminalStates"] = [P(qstate(name, s)) for s in e.get("sT", [])]
        if e.get("r"):
            node["relatesTo"] = [P(r[0]) for r in e["r"]]
            node["rels"] = [{"target": r[0], "cardinality": r[1],
                             "label": r[2] if len(r) > 2 else "relatesTo"} for r in e["r"]]
        if ev_by_entity[name]:
            node["emitsEvent"] = [P(ev) for ev in sorted(ev_by_entity[name])]
        graph.append(node)

    decisions = []
    for name, e in ents.items():
        for s in e.get("s", []):
            local = qstate(name, s)
            desc = sdesc.get(f"{name}.{s}") or sdesc.get(s) or f"{name} lifecycle state: {s}"
            fanout = out_edges[(name, s)]
            node = OrderedDict([("id", P(local)), ("type", "State"), ("name", local),
                                ("description", desc), ("ownedBy", P(name))])
            if len(fanout) > 1:
                node["isDecisionPoint"] = True
                decisions.append({"state": local, "entity": name,
                                  "branches": [{"op": o, "to": qstate(name, t)} for o, t in fanout]})
            graph.append(node)

    for o in ops:
        en = o["e"]
        node = OrderedDict([("id", P(o["n"])), ("type", "Operation"), ("name", o["n"]),
                            ("description", o.get("d", "")), ("ownedBy", P(en)),
                            ("from", P(qstate(en, o["f"]))), ("to", P(qstate(en, o["t"])))])
        if o.get("pre"):  node["precondition"] = o["pre"]
        if o.get("post"): node["postcondition"] = o["post"]
        if o.get("by"):   node["performedBy"] = o["by"]
        if o.get("sys"):  node["usesSystem"] = o["sys"]
        if o.get("hitl"): node["requiresHuman"] = True
        if o.get("risk"): node["risk"] = o["risk"]
        if o.get("emit"): node["emitsEvent"] = [P(ev) for ev in o["emit"]]
        graph.append(node)

    for ev, emitters in sorted(events.items()):
        graph.append(OrderedDict([("id", P(ev)), ("type", "Event"), ("name", ev),
                                  ("description", f"Emitted by {', '.join(sorted(emitters))}")]))

    # A6: document identity lives INSIDE @graph, so @graph stays the default
    # graph. A root-level @id would turn the whole document into a named graph
    # and move every triple out of the default graph.
    meta = OrderedDict([("id", f"{base}schema"), ("type", "schema:Dataset"),
                        ("name", spine.get("title", ns)),
                        ("description", f"Context Studio schema for {spine.get('title', ns)}"),
                        ("schema:version", str(spine.get("v", "1.0"))),
                        ("schema:dateCreated", datetime.date.today().isoformat())])
    doc = OrderedDict([("@context", build_context(ns, base)),
                       ("@graph", [meta] + graph)])
    return doc, decisions


# ---------------------------------------------------------------- digest
def build_digest(spine, ents, ops, decisions):
    by_entity, by_actor, by_system = defaultdict(list), defaultdict(list), defaultdict(list)
    for o in ops:
        by_entity[o["e"]].append(o["n"])
        by_actor[o.get("by", "unassigned")].append(o["n"])
        for s in o.get("sys", []):
            by_system[s].append(o["n"])

    return OrderedDict([
        ("domain", spine.get("title", spine["ns"])),
        ("ns", spine["ns"]),
        ("counts", {"entities": len(ents),
                    "states": sum(len(e.get("s", [])) for e in ents.values()),
                    "operations": len(ops),
                    "decisionPoints": len(decisions),
                    "actors": len(by_actor),
                    "systems": len(by_system),
                    "humanInLoopOps": sum(1 for o in ops if o.get("hitl")),
                    "highRiskOps": sum(1 for o in ops if o.get("risk") == "high")}),
        # stage 2 needs the actual state names plus initial/terminal to place
        # START and END; counts alone are not enough to build a graph.
        ("entities", [{"n": n,
                       "lifecycle": bool(e.get("s")),
                       "states": e.get("s", []),
                       "initial": e.get("s0"),
                       "terminal": e.get("sT", []),
                       "relatesTo": [r[0] for r in e.get("r", [])]}
                      for n, e in ents.items()]),
        ("operations", [OrderedDict([("n", o["n"]), ("e", o["e"]),
                                     ("f", o["f"]), ("t", o["t"]),
                                     ("by", o.get("by", "unassigned")),
                                     ("sys", o.get("sys", [])),
                                     ("hitl", bool(o.get("hitl"))),
                                     ("risk", o.get("risk", "low"))]) for o in ops]),
        ("decisionPoints", decisions),
        ("cohesion", {"byEntity": dict(by_entity),
                      "byActor": dict(by_actor),
                      "bySystem": dict(by_system)}),
    ])


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spine")
    ap.add_argument("-o", "--outdir", default=".")
    ap.add_argument("--strict", action="store_true", help="exit 1 on errors")
    ap.add_argument("--jsonld-out", help="exact path for the JSON-LD (overrides naming)")
    ap.add_argument("--digest-out", help="exact path for the digest (overrides naming)")
    a = ap.parse_args()

    raw = open(a.spine).read()
    spine = json.loads(raw)
    rep = Report()
    ents, sdesc, ops, out_edges = validate(spine, rep)
    doc, decisions = compile_jsonld(spine, ents, sdesc, ops, out_edges)
    digest = build_digest(spine, ents, ops, decisions)

    stem = a.spine.split("/")[-1].replace(".spine.json", "").replace(".json", "")
    jl = json.dumps(doc, indent=2)
    dg = json.dumps(digest, indent=1)
    jl_path = a.jsonld_out or f"{a.outdir}/{stem}.jsonld"
    dg_path = a.digest_out or f"{a.outdir}/{stem}.digest.json"
    for pth in (jl_path, dg_path):
        pathlib.Path(pth).parent.mkdir(parents=True, exist_ok=True)
    open(jl_path, "w").write(jl)
    open(dg_path, "w").write(dg)

    print(f"validation ({len(rep.errors)} errors, {len(rep.warns)} warnings)")
    print(rep.render())
    print()
    print(f"{'artifact':<28}{'chars':>9}{'~tokens':>10}   who pays")
    print("-" * 68)
    print(f"{'spine.json (LLM #1 writes)':<28}{len(raw):>9}{approx_tokens(raw):>10}   model")
    print(f"{'schema.jsonld (compiled)':<28}{len(jl):>9}{approx_tokens(jl):>10}   free")
    print(f"{'digest.json (LLM #2 reads)':<28}{len(dg):>9}{approx_tokens(dg):>10}   free")
    print("-" * 68)
    print(f"model output saved vs emitting JSON-LD directly: "
          f"{approx_tokens(jl) - approx_tokens(raw)} tokens "
          f"({100 - round(100 * len(raw) / len(jl))}% smaller)")
    print(f"\nnodes: {len(doc['@graph'])}  "
          f"(E={len(ents)} S={sum(len(e.get('s', [])) for e in ents.values())} "
          f"O={len(ops)} Ev={sum(1 for n in doc['@graph'] if n.get('type') == 'Event')})  "
          f"decisionPoints={len(decisions)}")

    if a.strict and not rep.ok():
        sys.exit(1)


if __name__ == "__main__":
    main()
