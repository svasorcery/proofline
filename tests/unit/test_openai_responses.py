"""Exercise the official SDK over an in-memory HTTP transport; no API access."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from proofline.config import Settings
from proofline.domain.inputs import canonical_json, json_digest
from proofline.providers.openai_responses import OpenAIResponsesProvider
from proofline.providers.replay import ReplayCatalog


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        openai_api_key=SecretStr("offline-test-key"),
        openai_model="configured-test-model",
    )


@pytest.fixture
def snapshot():
    catalog = ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures")
    return catalog.recordings[0].input


def response_body(*, status="completed", content=None, usage=None):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "resolved-test-model",
        "status": status,
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": content or [],
            }
        ],
        "usage": usage,
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "tools": [],
    }


def test_one_strict_request_uses_only_snapshot_and_records_provenance(
    settings, snapshot, valid_payload
):
    requests = []
    usage = {
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 5},
    }
    raw = canonical_json(valid_payload)

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_test"},
            json=response_body(
                content=[{"type": "output_text", "text": raw, "annotations": []}], usage=usage
            ),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    prepared = provider.prepare(snapshot)
    result = provider.generate(prepared)
    assert result.error is None
    assert result.raw_output == raw and result.usage == usage
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    payload = json.loads(request.content)
    assert payload["model"] == "configured-test-model"
    assert payload["tools"] == [] and payload["tool_choice"] == "none"
    assert payload["store"] is False and payload["background"] is False
    assert payload["truncation"] == "disabled" and payload["max_output_tokens"] == 4096
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["type"] == "json_schema"
    assert json.loads(payload["input"]) == snapshot.model_dump(mode="json")
    profile = result.generation_profile
    assert profile["provider_request_id"] == "req_test"
    assert profile["provider_response_id"] == "resp_test"
    assert profile["model"] == "resolved-test-model"
    assert profile["requested_model"] == "configured-test-model"
    assert profile["new_model_call"] is True and profile["usage_origin"] == "provider"
    assert profile["monetary_cost"] is None
    assert profile["latency_seconds"] >= 0
    assert profile["request_sha256"] == json_digest(payload)
    assert profile["schema_sha256"] == json_digest(payload["text"]["format"]["schema"])
    assert "offline-test-key" not in canonical_json(profile)


@pytest.mark.parametrize(
    "status,content,code",
    [
        ("completed", [{"type": "refusal", "refusal": "Cannot comply"}], "MODEL_REFUSAL"),
        (
            "incomplete",
            [{"type": "output_text", "text": '{"partial":', "annotations": []}],
            "MODEL_OUTPUT_INCOMPLETE",
        ),
        ("completed", [], "INVALID_MODEL_OUTPUT"),
        ("failed", [], "PROVIDER_ERROR"),
        ("in_progress", [], "MODEL_OUTPUT_INCOMPLETE"),
    ],
)
def test_output_failures_preserve_known_response(settings, snapshot, status, content, code):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_failure"},
            json=response_body(status=status, content=content),
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    result = provider.generate(provider.prepare(snapshot))
    assert result.error["code"] == code
    assert len(requests) == 1 and result.usage is None
    assert result.generation_profile["provider_request_id"] == "req_failure"
    if content:
        assert result.raw_output is not None


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "PROVIDER_REQUEST_REJECTED"),
        (401, "PROVIDER_ACCESS_DENIED"),
        (403, "PROVIDER_ACCESS_DENIED"),
        (404, "PROVIDER_REQUEST_REJECTED"),
        (429, "PROVIDER_RATE_LIMITED"),
        (500, "PROVIDER_ERROR"),
        (503, "PROVIDER_ERROR"),
        (307, "PROVIDER_ERROR"),
    ],
)
def test_http_errors_never_retry_redirect_or_expose_provider_body(
    settings, snapshot, status, code, caplog
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"x-request-id": "req_error", "location": "https://other.test"},
            json={"error": {"message": "offline-test-key private provider details"}},
        )

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    result = provider.generate(provider.prepare(snapshot))
    assert result.error["code"] == code
    assert len(requests) == 1
    assert result.usage is None and result.raw_output is None
    assert result.generation_profile["provider_request_id"] == "req_error"
    assert result.generation_profile["monetary_cost"] is None
    assert "offline-test-key" not in repr(result) + caplog.text
    assert "private provider details" not in repr(result) + caplog.text


@pytest.mark.parametrize(
    "failure,code", [(httpx.ReadTimeout, "MODEL_TIMEOUT"), (httpx.ConnectError, "PROVIDER_ERROR")]
)
def test_network_failures_are_unknown_and_not_retried(settings, snapshot, failure, code):
    requests = []

    def handle(request):
        requests.append(request)
        raise failure("private diagnostic", request=request)

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    result = provider.generate(provider.prepare(snapshot))
    assert result.error["code"] == code and len(requests) == 1
    assert result.usage is None and result.raw_output is None
    assert result.generation_profile["provider_request_id"] is None


def test_overall_deadline_cancels_the_single_request(settings, snapshot):
    requests = []
    cancelled = []

    async def handle(request):
        requests.append(request)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    prepared = provider.prepare(snapshot)
    provider.timeout_seconds = 1
    result = provider.generate(prepared)
    assert result.error["code"] == "MODEL_TIMEOUT"
    assert len(requests) == 1 and cancelled == [True]
    assert result.usage is None and result.generation_profile["monetary_cost"] is None


@pytest.mark.parametrize("malformed", ["output", "content", "text", "text_number"])
def test_malformed_sdk_output_preserves_received_diagnostics(settings, snapshot, malformed):
    calls = []
    usage = {"input_tokens": 100, "output_tokens": 2, "total_tokens": 102}
    body = response_body(
        content=[{"type": "output_text", "text": "{}", "annotations": []}], usage=usage
    )
    if malformed == "output":
        body["output"] = None
    elif malformed == "content":
        body["output"][0]["content"] = None
    else:
        body["output"][0]["content"][0]["text"] = 42 if malformed == "text_number" else None

    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"x-request-id": "req_malformed"}, json=body)

    provider = OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle))
    result = provider.generate(provider.prepare(snapshot))
    assert result.error["code"] == "INVALID_MODEL_OUTPUT"
    assert len(calls) == 1 and result.usage == usage
    assert result.generation_profile["provider_request_id"] == "req_malformed"
    assert result.generation_profile["provider_response_id"] == "resp_test"
    assert result.generation_profile["latency_seconds"] >= 0
    assert result.generation_profile["raw_output_format"] == "response-envelope"
    assert json.loads(result.raw_output)["output"] == body["output"]
