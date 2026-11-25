"""Responses diagnostic contract exercised at the real SDK HTTP boundary."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from proofline.config import Settings
from proofline.providers.factory import create_live_provider
from proofline.providers.replay import ReplayCatalog


@pytest.fixture
def snapshot():
    return ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures").recordings[0].input


def response(provider, snapshot, error, status=400):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"x-request-id": "req_diagnostic", "content-type": "application/json"},
            content=json.dumps({"error": error}, ensure_ascii=True).encode(),
        )

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        llm_provider=provider,
        llm_model="offline-model",
        llm_api_key=SecretStr("offline-private-key"),
    )
    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    return live.generate(live.prepare(snapshot)), requests


def test_groq_output_validation_has_safe_diagnostics_and_never_retries(snapshot):
    result, requests = response(
        "groq",
        snapshot,
        {
            "code": "json_validate_failed",
            "type": "invalid_request_error",
            "param": "response_format.json_schema.schema",
            "message": "offline-private-key confidential input",
            "failed_generation": "confidential answer",
        },
    )
    assert result.error["code"] == "INVALID_MODEL_OUTPUT"
    assert len(requests) == 1 and result.raw_output is None and result.usage is None
    profile = result.generation_profile
    assert profile["http_status"] == 400 and profile["provider_request_id"] == "req_diagnostic"
    assert profile["provider_error_code"] == "json_validate_failed"
    assert profile["provider_error_type"] == "invalid_request_error"
    assert profile["provider_error_param"] == "response_format.json_schema.schema"
    assert profile["provider_error_stage"] == "output_validation"
    assert "provider_output_field_diagnostics" not in profile
    assert "confidential" not in repr(result) and "offline-private-key" not in repr(result)


def test_groq_rejected_json_records_only_declared_missing_fields_and_extra_count(snapshot):
    result, requests = response(
        "groq",
        snapshot,
        {
            "code": "json_validate_failed",
            "type": "invalid_request_error",
            "message": "offline-private-key",
            "failed_generation": json.dumps(
                {
                    "case_id": "private case",
                    "facts": ["private evidence"],
                    "timeline": [],
                    "private-unexpected-key": "offline-private-key",
                }
            ),
        },
    )
    assert len(requests) == 1 and result.raw_output is None and result.usage is None
    assert result.generation_profile["provider_output_field_diagnostics"] == {
        "missing_required_fields": ["follow_up", "readiness"],
        "unexpected_field_count": 1,
    }
    assert "private" not in repr(result)


def test_groq_effort_rejection_records_only_safe_diagnostics(snapshot, monkeypatch):
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "high")
    result, requests = response(
        "groq",
        snapshot,
        {
            "param": "reasoning_effort",
            "type": "invalid_request_error",
            "message": "offline-private-key confidential input",
        },
    )
    assert len(requests) == 1 and result.error["code"] == "PROVIDER_REQUEST_REJECTED"
    assert result.raw_output is None and result.usage is None
    assert result.generation_profile["provider_error_param"] == "reasoning_effort"
    assert result.generation_profile["reasoning_effort"] == "high"
    assert "confidential" not in repr(result) and "offline-private-key" not in repr(result)


@pytest.mark.parametrize(
    "provider,status", [("openai", 400), ("deepseek", 400), ("groq", 500), ("groq", 429)]
)
def test_output_reclassification_is_scoped_to_groq_400(snapshot, provider, status):
    result, requests = response(provider, snapshot, {"code": "json_validate_failed"}, status)
    expected = {
        400: "PROVIDER_REQUEST_REJECTED",
        500: "PROVIDER_ERROR",
        429: "PROVIDER_RATE_LIMITED",
    }[status]
    assert result.error["code"] == expected and len(requests) == 1
    assert result.generation_profile["provider_error_stage"] is None


@pytest.mark.parametrize("field", ["code", "type", "param"])
@pytest.mark.parametrize(
    "bad", [None, 42, [], {}, "offline-private-key", "bad\x00value", "bad\ud800value"]
)
def test_error_identifiers_are_closed_allowlists_not_untrusted_text(snapshot, field, bad):
    error = {
        "code": "unrecognised_error",
        "type": "invalid_request_error",
        "param": "model",
        "message": "offline-private-key",
        "failed_generation": "offline-private-key",
    }
    error[field] = bad
    result, requests = response("groq", snapshot, error)
    assert result.error["code"] == "PROVIDER_REQUEST_REJECTED" and len(requests) == 1
    assert result.generation_profile["provider_error_" + field] is None
    assert result.generation_profile["provider_error_stage"] is None
    encoded = json.dumps(result.generation_profile)
    assert "offline-private-key" not in encoded and "bad" not in encoded


def test_v32_approved_instruction_body_is_wired_with_distinct_provenance(snapshot):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        llm_provider="groq",
        llm_model="offline-model",
        llm_api_key=SecretStr("offline-key"),
    )
    prepared = create_live_provider(settings).prepare(snapshot)
    approved = (
        Path(__file__).resolve().parents[2]
        / "docs/proposals/2026-11-16-live-policy-v3.2-candidate.txt"
    ).read_text(encoding="utf-8")
    body = (
        approved.split("--- BEGIN MODEL INSTRUCTIONS ---", 1)[1]
        .split("--- END MODEL INSTRUCTIONS ---", 1)[0]
        .strip()
        + "\n"
    )
    assert (
        prepared.request["messages"][0]["content"]
        == "Proofline assembly task. Apply the following policy to the JSON snapshot.\n" + body
    )
    assert prepared.generation_profile["policy_version"] == "proofline-policy-v3.2-candidate-1"
    assert (
        prepared.generation_profile["prompt_version"] == "proofline-responses-prompt-v2-candidate-1"
    )
    assert prepared.generation_profile["schema_version"] == "assembly-output-v1"
    assert prepared.request["response_format"]["json_schema"]["strict"] is True
