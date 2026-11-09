"""Reference-based evaluation is distinct from runtime structural validation."""
from contract import covers, instant, validate_runtime


def assess(pack, reference, case, sources):
    runtime = validate_runtime(pack, case, sources)
    facts = {fact["key"]: fact for fact in pack["facts"]}
    fact_rows = []
    for key, expected in reference["facts"].items():
        actual = facts.get(key)
        fact_rows.append({"key": key, "value_ok": bool(actual and actual["value"] == expected["value"]),
                          "evidence_ok": bool(actual and covers(actual["evidence"], expected["evidence_sets"], sources))})

    def same_event(actual, expected):
        try:
            return (actual["event_type"] == expected["event_type"] and actual["time"]["role"] == expected["role"]
                    and instant(actual["time"]["value"]) == instant(expected["at"]))
        except (ValueError, TypeError):
            return False

    required = reference["timeline"]
    allowed = required + reference.get("optional_timeline", [])
    missing = [expected["event_type"] for expected in required if not any(
        same_event(actual, expected) and covers(actual["evidence"], expected["evidence_sets"], sources)
        for actual in pack["timeline"])]
    unsupported = [i for i, actual in enumerate(pack["timeline"]) if not any(
        same_event(actual, expected) and covers(actual["evidence"], expected["evidence_sets"], sources)
        for expected in allowed)]
    expected_follow = reference["follow_up"]
    actual_follow = pack["follow_up"]
    missing_requirements = sorted(set(expected_follow["requirements"]) - set(actual_follow["requirements"]))
    extra_requirements = sorted(set(actual_follow["requirements"]) - set(expected_follow["requirements"]))
    followup_ok = (actual_follow["action"] == expected_follow["action"] and actual_follow["locator"] == expected_follow["locator"]
                   and not missing_requirements and not extra_requirements)
    values_ok = set(facts) == set(reference["facts"]) and len(facts) == len(pack["facts"]) and all(row["value_ok"] for row in fact_rows)
    evidence_ok = all(row["evidence_ok"] for row in fact_rows)
    timeline_ok = not missing and not unsupported
    readiness_ok = pack["readiness"] == reference["readiness"]
    return {"case_id": pack["case_id"], "runtime_valid": runtime["valid"], "runtime_issues": runtime["issues"],
            "facts_exact": values_ok, "evidence_complete": evidence_ok, "timeline_complete": timeline_ok,
            "followup_complete": followup_ok, "readiness_ok": readiness_ok,
            "pack_pass": runtime["valid"] and values_ok and evidence_ok and timeline_ok and followup_ok and readiness_ok,
            "facts": fact_rows, "missing_events": missing, "unsupported_event_indices": unsupported,
            "missing_requirements": missing_requirements, "extra_requirements": extra_requirements}
