"""Verify saved live evidence offline; never imports or invokes a model provider."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def main():
    manifest = read(HERE / "manifest.json")
    for name, expected in manifest["code_and_input_hashes"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    for name, expected in manifest.get("artifact_hashes", {}).items():
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == expected, name
    ledger = read(HERE / "ledger.json")
    assert len(ledger["attempts"]) <= ledger["max_requests"] == 10
    assert ledger["approved_budget_usd"] == 0
    template = read(HERE / "request-template.json")
    response_ids = set()
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}
    outcomes = []
    for attempt in ledger["attempts"]:
        folder = HERE / attempt["condition"]
        if not (folder / "run.json").exists():
            outcomes.append({"condition": attempt["condition"], "state": attempt["state"]})
            continue
        run = read(folder / "run.json")
        if "generation_profile" not in run:
            outcomes.append({"condition": attempt["condition"], "http_error": run.get("code")})
            continue
        profile = run["generation_profile"]
        assert profile["provider"] == "groq" and profile["max_retries"] == 0
        assert profile["requested_model"] == "openai/gpt-oss-120b"
        assert profile["monetary_cost"] is None and profile["pricing"] is None
        reconstructed = {**template, "input": json.dumps(run["snapshot"], ensure_ascii=False,
                                                       sort_keys=True, separators=(",", ":"), allow_nan=False)}
        assert digest(reconstructed) == profile["request_sha256"], attempt["condition"]
        assert digest(template["text"]["format"]["schema"]) == profile["schema_sha256"]
        response_id = profile.get("provider_response_id")
        if response_id is not None:
            assert response_id not in response_ids
            response_ids.add(response_id)
        usage = run.get("usage")
        if usage is not None:
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                totals[key] += usage[key]
            totals["reasoning_tokens"] += (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
        if run["status"] == "succeeded":
            proposal = read(folder / "proposal.json")
            assert json.loads(run["raw_output"]) == proposal["body"]
            assert run["snapshot"] == proposal["snapshot"]
            assert proposal["decision"] is None
        review = read(folder / "review.json") if (folder / "review.json").exists() else None
        outcomes.append({"condition": attempt["condition"], "run_status": run["status"],
                         "error": run.get("error"), "quality_pass": review["overall_pass"] if review else None})
    print(json.dumps({"reserved_attempts": len(ledger["attempts"]), "unique_provider_responses": len(response_ids),
                      "received_usage_totals": totals, "stopped": ledger["stopped"], "outcomes": outcomes}, indent=2))


if __name__ == "__main__":
    main()
