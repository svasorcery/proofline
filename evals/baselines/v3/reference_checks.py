"""Deterministic reference checks extracted from v3; no CLI or model transport."""
from contract import instant
from evaluate import assess
import json


def sources_for(case, removed=None):
    return [source for source in case["initial_sources"] + case["sources"] if source["id"] != removed]


def job_list(cases, references):
    refs = {r["case_id"]: r for r in references["cases"]}
    jobs = [("full", c, refs[c["id"]]["full"], None) for c in cases]
    jobs += [("ablation", c, refs[c["id"]]["ablation"]["reference"], refs[c["id"]]["ablation"]["removed_source_id"])
             for c in cases if refs[c["id"]]["ablation"] is not None]
    assert len(jobs) == 9
    return jobs


def evidence_from_set(option):
    grouped = {}
    for need in option:
        grouped.setdefault(need["source_id"], [])
        if need["anchor"] not in grouped[need["source_id"]]:
            grouped[need["source_id"]].append(need["anchor"])
    return [{"source_id": source, "fragments": fragments} for source, fragments in grouped.items()]


def reference_pack(case, reference):
    return {"case_id": case["id"],
            "facts": [{"key": key, "value": fact["value"], "evidence": evidence_from_set(fact["evidence_sets"][0]) if fact["evidence_sets"] else []}
                      for key, fact in reference["facts"].items()],
            "timeline": sorted([{"event_type": event["event_type"], "time": {"value": event["at"], "role": event["role"]},
                                  "evidence": evidence_from_set(event["evidence_sets"][0])} for event in reference["timeline"]],
                                 key=lambda event: instant(event["time"]["value"])),
            "readiness": reference["readiness"], "follow_up": reference["follow_up"]}


def validate_data(jobs):
    for mode, case, reference, removed in jobs:
        sources = sources_for(case, removed)
        by_id = {s["id"]: s for s in sources}
        assert set(case["questions"]) == set(reference["facts"])
        for item in list(reference["facts"].values()) + reference["timeline"] + reference.get("optional_timeline", []):
            for option in item["evidence_sets"]:
                assert option
                for need in option:
                    assert need["source_id"] in by_id and need["anchor"] in by_id[need["source_id"]]["text"], (mode, case["id"], need)
        for fact in reference["facts"].values():
            assert fact["evidence_sets"] or fact["value"] == "unconfirmed"
        oracle = reference_pack(case, reference)
        result = assess(oracle, reference, case, sources)
        assert result["pack_pass"], (mode, case["id"], result)
        for optional in reference.get("optional_timeline", []):
            variant = json.loads(json.dumps(oracle))
            variant["timeline"].append({"event_type": optional["event_type"], "time": {"value": optional["at"], "role": optional["role"]},
                                        "evidence": evidence_from_set(optional["evidence_sets"][0])})
            variant["timeline"].sort(key=lambda event: instant(event["time"]["value"]))
            assert assess(variant, reference, case, sources)["pack_pass"], (case["id"], "optional")
