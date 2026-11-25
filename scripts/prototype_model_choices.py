"""Exercise case-specific model choices with the SDK's in-memory HTTP transport."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import httpx
from pydantic import SecretStr

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.providers.factory import create_live_provider
from proofline.providers.replay import ReplayCatalog
from proofline.validation.assembly import validate_assembly

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "evals/live/v6-groq-qwen-v33-2026-11-17"
LOOKUP_ACTION = {
    "document": "read_source",
    "provider_log": "query_provider",
    "payment_ledger": "query_payment",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def archived_case(case: str):
    run = load(ARCHIVE / f"full-{case}/run.json")
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    return snapshot, json.loads(run["raw_output"])


def to_candidate(body: dict):
    candidate = deepcopy(body)
    candidate["facts"] = {
        fact["key"]: {key: value for key, value in fact.items() if key != "key"}
        for fact in body["facts"]
    }
    assert len(candidate["facts"]) == len(body["facts"]), "Duplicate fact in test input"
    return candidate


def capture(snapshot: AnalysisInput, approved: set[str], synthetic_response: dict):
    """Capture exactly one SDK serialization. Return a synthetic response."""
    received = []

    def handle(http_request):
        received.append(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline-example",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen/qwen3.8-27b",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": canonical_json(synthetic_response),
                        },
                    }
                ],
            },
        )

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        groq_prompt_profile="v3.3-choices-test",
        groq_test_lookup_sources={snapshot.case_id: tuple(sorted(approved))},
        llm_api_key=SecretStr("offline-example"),
    )
    provider = create_live_provider(settings, transport=httpx.MockTransport(handle))
    prepared = provider.prepare(snapshot)
    result = provider.generate(prepared)
    assert received == [prepared.request] and result.error is None
    assert result.raw_output == canonical_json(synthetic_response) and prepared.decode_output
    return received[0], prepared.decode_output(result.raw_output)


def check_wire_schema(schema: dict, samples: dict[str, tuple[dict, bool]]):
    """Run the actual serialized JSON Schema through the local frontend's Ajv."""
    js = """
const Ajv = require(require.resolve('ajv', {paths: [process.cwd()]}));
const {schema, samples} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const validate = new Ajv().compile(schema);
const result = {};
for (const [label, [value, expected]] of Object.entries(samples)) {
  const actual = validate(value);
  if (actual !== expected) throw Error(label + ': ' + JSON.stringify(validate.errors));
  result[label] = actual ? 'accepted' : 'rejected';
}
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", js],
        input=json.dumps({"schema": schema, "samples": samples}),
        cwd=ROOT / "frontend",
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def v02():
    snapshot, recorded = archived_case("V02")
    original = to_candidate(recorded)
    corrected = deepcopy(original)
    corrected["follow_up"].update(action="ask_user", locator=None)
    missing_fact = deepcopy(corrected)
    del missing_fact["facts"]["cancellation_status"]
    # Even a shortlisted locator is ineligible once its source is included.
    approved = {
        source.source_id
        for source in snapshot.catalog
        if source.locator == original["follow_up"]["locator"]
    }
    assert len(approved) == 1
    return (
        snapshot,
        approved,
        {
            "archived_repeated_lookup": (original, False),
            "missing_requested_fact": (missing_fact, False),
            "ask_for_message_and_receipt": (corrected, True),
        },
    )


def v04():
    snapshot, recorded = archived_case("V04")
    original = to_candidate(recorded)
    corrected = deepcopy(original)
    corrected["facts"] = {"alternative_acceptance": corrected["facts"]["alternative_acceptance"]}
    irrelevant_requirements = deepcopy(corrected)
    corrected["follow_up"]["requirements"] = ["quiet_room", "courtyard_windows"]
    return (
        snapshot,
        set(),
        {
            "archived_extra_facts": (original, False),
            "only_requested_fact_and_two_confirmations": (corrected, True),
            "limitations_excess_requirements_still_fit": (irrelevant_requirements, True),
        },
    )


def v01_with_unread_reply():
    catalog = ReplayCatalog(ROOT / "fixtures")
    record = next(r for r in catalog.recordings if r.option.replay_id == "v3-ablation-V01")
    accepted = to_candidate(json.loads(record.prepared.raw_output))
    target = next(s for s in record.input.catalog if s.locator == accepted["follow_up"]["locator"])
    assert not target.included and target.text is None
    snapshot_data = record.input.model_dump(mode="json")
    # An unrelated unread document of the same kind must not become eligible.
    unrelated = {
        **target.model_dump(mode="json"),
        "source_id": "unrelated-unread",
        "locator": "unrelated-payment-document",
        "title": "Unrelated payment document",
        "position": len(snapshot_data["catalog"]),
    }
    snapshot_data["catalog"].append(unrelated)
    snapshot = AnalysisInput.model_validate_json(json.dumps(snapshot_data))
    wrong_kind, wrong_target = deepcopy(accepted), deepcopy(accepted)
    wrong_kind["follow_up"]["action"] = "query_provider"
    wrong_target["follow_up"]["locator"] = unrelated["locator"]
    return (
        snapshot,
        {target.source_id},
        {
            "eligible_original_reply": (accepted, True),
            "wrong_kind_at_eligible_locator": (wrong_kind, False),
            "irrelevant_unread_document": (wrong_target, False),
        },
    )


def main():
    result = {"provider_requests": 0, "in_memory_sdk_requests": 0, "checks": {}}
    for scenario in (v02, v04, v01_with_unread_reply):
        snapshot, approved, samples = scenario()
        accepted = next(payload for payload, expected in samples.values() if expected)
        wire, assembled = capture(snapshot, approved, accepted)
        schema = wire["response_format"]["json_schema"]["schema"]
        assert wire["response_format"]["json_schema"]["strict"] is True
        checks = check_wire_schema(schema, samples)
        assert not validate_assembly(assembled, snapshot.to_validation_context()).issues
        sent_catalogue = json.loads(wire["messages"][1]["content"])["catalog"]
        assert [entry["source_id"] for entry in sent_catalogue if entry["included"]] == [
            source.source_id for source in snapshot.catalog if source.included
        ]
        assert all(
            source["included"] or source["source_id"] in approved for source in sent_catalogue
        )
        assert all(source["included"] or source["text"] is None for source in sent_catalogue)
        assert all(source["source_id"] != "unrelated-unread" for source in sent_catalogue)
        choices = {}
        for entry in sent_catalogue:
            if not entry["included"]:
                choices.setdefault(LOOKUP_ACTION[entry["kind"]], []).append(entry["locator"])
        result["checks"][snapshot.case_id] = {
            "required_fact_keys": list(snapshot.requested_fact_keys),
            "eligible_lookup_choices": choices,
            "schema_checks": checks,
            "canonical_validation": "pass",
            "timeline_events_in_example": len(assembled.timeline),
            "request_sha256": json_digest(wire),
        }
        result["in_memory_sdk_requests"] += 1
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
