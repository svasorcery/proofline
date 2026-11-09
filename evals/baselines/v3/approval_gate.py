"""Post-run approval guard; does not alter frozen v3 predictions or scores."""
from contract import LOOKUP_KINDS, validate_runtime


def check_for_operator(pack, case, sources):
    runtime = validate_runtime(pack, case, sources)
    issues = list(runtime["issues"])
    acceptance = next((f["value"] for f in pack["facts"] if f["key"] == "alternative_acceptance"), None)
    if acceptance != "confirmed" and any(e["event_type"] == "alternative_accepted" for e in pack["timeline"]):
        issues.append("acceptance_fact_event_conflict")
    followup = pack["follow_up"]
    if followup["action"] in LOOKUP_KINDS and any(s.get("locator") == followup["locator"] for s in sources):
        issues.append("repeated_snapshot_lookup")
    return {"state": "blocked_for_approval" if issues else "needs_operator_review", "issues": issues,
            "note": "No automatic approval; current prototype reads immutable snapshots and has no refresh action."}
