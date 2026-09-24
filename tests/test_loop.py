"""Offline end-to-end test: no API key, no network. Proves the repair loop."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from builder.spine_builder import build_spine

good = json.load(open("examples/claims.spine.json"))

# attempt 1: three planted defects the JSON Schema cannot catch
bad = json.loads(json.dumps(good))
bad["E"][2]["s"].append("Escalated")                                  # unreachable + deadlock
bad["O"].append({"n": "Reopen", "e": "Claim", "f": "Closed",          # terminal has outgoing
                 "t": "UnderReview", "d": "bad", "by": "System", "sys": ["ClaimDB"]})
bad["E"][3]["r"] = [["Ghost", "1-1", "x"]]                            # dangling target

llm = FakeListChatModel(responses=[
    "```json\n" + json.dumps(bad) + "\n```",       # fenced + invalid
    json.dumps(good),                              # repaired
])
llm._sab_provider, llm._sab_model = "mock", "fake"

events = []
res = build_spine("a health insurer processes claims", llm, max_repairs=3,
                  on_event=lambda k, m: events.append((k, m)))

assert res.attempts == 2, res.attempts
assert res.ok, res.errors
assert len(res.jsonld["@graph"]) == 65, len(res.jsonld["@graph"])
assert res.digest["counts"]["decisionPoints"] == 5

caught = [m for k, m in events if k == "detail"]
print("repair loop: rejected attempt 1, accepted attempt 2")
print("errors fed back to the model:")
for c in caught:
    print("   ", c.strip())
print(f"\nfinal: {len(res.jsonld['@graph'])} nodes, "
      f"{res.digest['counts']['decisionPoints']} decision points, "
      f"attempts={res.attempts}")

# fenced-JSON and truncation handling
from builder.spine_builder import extract_json
assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
assert extract_json('here you go: {"a":{"b":2}} done') == {"a": {"b": 2}}
try:
    extract_json('{"a": {"b": 1}')
    raise AssertionError("should have raised")
except ValueError as e:
    assert "truncated" in str(e)
print("extract_json: fenced / prose-wrapped / truncated all handled")
print("\nALL TESTS PASSED")
