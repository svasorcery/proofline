"""Read-only verification of recorded v3 data. No generation, network or writes."""
import hashlib
import json
from pathlib import Path
import sys

from approval_gate import check_for_operator
from contract import validate_runtime
from evaluate import assess
from reference_checks import job_list, sources_for, validate_data

PROJECT = Path(__file__).resolve().parents[3]
BASELINE = "evals/baselines/v3"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root=PROJECT):
    root = root.resolve()
    baseline = root / BASELINE
    manifest = read(baseline / "manifest.json")
    paths, origins = {}, {}
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        require(path.is_relative_to(root), "Manifest path escapes repository")
        require(path.is_file(), f"Missing baseline file: {item['path']}")
        require(sha256(path) == item["sha256"], f"SHA256 mismatch: {item['path']}")
        require(path.stat().st_size == item["bytes"], f"Size mismatch: {item['path']}")
        require(item["path"] not in paths, "Duplicate manifest path")
        paths[item["path"]] = path
        if "source_path" in item:
            require(item["source_path"] not in origins, "Duplicate provenance path")
            origins[item["source_path"]] = path

    original = read(baseline / "provenance/original-manifest.json")
    for old_path, digest in original["inputs_sha256"].items():
        source = "evidence/experiments/" + old_path.replace("\\", "/")
        require(source in origins, f"Missing measured input provenance: {source}")
        require(sha256(origins[source]) == digest, f"Measured input changed: {source}")

    summary = read(baseline / "recorded/summary.json")
    for name, digest in summary["offline_guard"]["artifact_sha256"].items():
        source = "evidence/experiments/scenario-fit-v3/" + name
        require(sha256(origins[source]) == digest, f"Post-run artifact changed: {name}")
    require(read(baseline / "policy_v3_1_unmeasured.json")["generation_tested"] is False,
            "The post-run policy must remain explicitly unmeasured")

    cases = read(root / "fixtures/cases/travel-cases.json")
    references = read(baseline / "reference_v3.json")
    policy = read(baseline / "policy_v3.json")
    jobs = job_list(cases, references)
    require(__debug__, "Run without -O: the original reference checks use assertions")
    validate_data(jobs)

    records = read(root / "fixtures/replay/v3/catalog.json")["records"]
    by_condition = {(r["mode"], r["case_id"]): r for r in records}
    expected_conditions = {(mode, case["id"]) for mode, case, _, _ in jobs}
    require(len(records) == len(by_condition) == 9 and set(by_condition) == expected_conditions,
            "Replay catalog must contain exactly the nine measured conditions")
    scores, gates, metas, fragments = [], [], [], 0
    for mode, case, reference, removed in jobs:
        record = by_condition[mode, case["id"]]
        require(record["removed_source_id"] == removed, "Wrong source-removal condition")
        require(record["cases_path"] == "fixtures/cases/travel-cases.json", "Wrong case dataset")
        require(record["generation_policy"] == policy["version"], "Wrong generation policy")
        for key in ("output_path", "raw_response_path", "prompt_path", "metadata_path"):
            require(record[key] in paths, f"Unmanifested replay artifact: {record[key]}")
        pack = read(paths[record["output_path"]])
        raw = read(paths[record["raw_response_path"]])
        require(raw["results"] == [pack], "Canonical output differs from the recorded response")
        require(sha256(paths[record["output_path"]]) == record["output_sha256"], "Output digest mismatch")
        prompt_path = paths[record["prompt_path"]]
        require(sha256(prompt_path) == record["prompt_sha256"], "Prompt digest mismatch")
        payload = json.loads(prompt_path.read_text(encoding="utf-8").rsplit("\n", 1)[-1])
        sources = sources_for(case, removed)
        expected_payload = {"case_id": case["id"], "user_text": case["user_text"],
                            "questions": case["questions"], "catalog": case["catalog"],
                            "sources": sources, "policy": policy}
        require(payload == expected_payload, "Recorded model input differs from replay condition")
        meta = read(paths[record["metadata_path"]])
        require(meta["exit_code"] == 0 and meta["own_tool_events"] == 0, "Unexpected recorded execution")
        require(meta["model"] == original["model"] and meta["reasoning_effort"] == original["effort"],
                "Generation metadata differs from the original manifest")
        metas.append(meta)
        score = {"mode": mode, **assess(pack, reference, case, sources)}
        scores.append(score)
        runtime = validate_runtime(pack, case, sources)
        fragments += sum(len(value["spans"]) for value in runtime["resolved"].values())
        gates.append({"mode": mode, "case_id": case["id"], **check_for_operator(pack, case, sources)})

    require(scores == read(baseline / "recorded/scores.json"), "Recomputed scores differ from measured v3")
    require(gates == summary["offline_guard"]["results"], "Post-run guard results changed")
    totals = {}
    for mode in ("full", "ablation"):
        rows = [r for r in scores if r["mode"] == mode]
        totals[mode] = {"n": len(rows), **{key: sum(r[key] for r in rows) for key in
                       ("pack_pass", "runtime_valid", "facts_exact", "evidence_complete",
                        "timeline_complete", "followup_complete", "readiness_ok")},
                       "fact_values_correct": sum(f["value_ok"] for r in rows for f in r["facts"])}
    require(totals == summary["scores"], "Recomputed totals differ from measured v3")
    require(fragments == summary["literal_fragments"] == 72, "Literal fragment count changed")
    require(len(metas) == summary["measured_calls"] == original["max_calls"], "Recorded call count changed")
    for field, usage_key in (("input_tokens", "input_tokens"), ("cached_input_tokens", "cached_input_tokens"),
                             ("output_tokens", "output_tokens"),
                             ("reasoning_output_tokens_reported", "reasoning_output_tokens")):
        require(sum(m["usage"].get(usage_key, 0) for m in metas) == summary[field], f"Usage mismatch: {field}")
    require(round(sum(m["elapsed_seconds"] for m in metas), 3) == summary["sum_call_seconds"],
            "Recorded latency total changed")
    blocked = [f"{g['mode']}/{g['case_id']}" for g in gates if g["state"] == "blocked_for_approval"]
    review_count = sum(g["state"] == "needs_operator_review" for g in gates)
    require(len(blocked) == summary["offline_guard"]["blocked"] and
            review_count == summary["offline_guard"]["needs_operator_review"], "Guard total mismatch")
    return {"artifact_hashes_verified": len(paths),
            "measured_input_hashes_verified": len(original["inputs_sha256"]),
            "post_run_artifact_hashes_verified": len(summary["offline_guard"]["artifact_sha256"]),
            "reference_conditions_validated": len(jobs), "recorded_conditions": len(records),
            "pack_pass": sum(r["pack_pass"] for r in scores),
            "failed_conditions": [f"{r['mode']}/{r['case_id']}" for r in scores if not r["pack_pass"]],
            "literal_fragments": fragments,
            "fact_values_correct": sum(r["fact_values_correct"] for r in totals.values()),
            "blocked_for_approval": blocked, "needs_operator_review": review_count,
            "policy_v3_1_measured": False, "new_model_calls": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, AssertionError) as error:
        print(f"Baseline verification failed: {error}", file=sys.stderr)
        sys.exit(1)
