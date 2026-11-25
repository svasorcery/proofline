"""Provider selection and real request construction over offline HTTP transports."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from proofline.config import Settings
from proofline.domain.inputs import json_digest
from proofline.providers.anthropic_messages import AnthropicMessagesProvider
from proofline.providers.replay import ReplayCatalog


@pytest.fixture
def snapshot():
    return ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures").recordings[0].input


def configured(provider):
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        llm_provider=provider,
        llm_model="selected-test-model",
        llm_api_key=SecretStr("selected-test-secret"),
    )


def make_provider(provider, handle, **settings_updates):
    from proofline.providers.factory import create_live_provider

    settings = configured(provider).model_copy(update=settings_updates)
    return create_live_provider(settings, transport=httpx.MockTransport(handle))


def test_anthropic_adapter_rejects_legacy_openai_settings():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        llm_provider="openai",
        openai_model="legacy-model",
        openai_api_key=SecretStr("offline-legacy-key"),
    )
    with pytest.raises(ValueError, match="Anthropic"):
        AnthropicMessagesProvider(settings)


def envelope(provider, raw="{}", *, stop="end_turn", usage=None):
    if provider == "groq":
        return {
            "id": "chatcmpl_offline",
            "object": "chat.completion",
            "created": 1,
            "model": "returned-test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": raw,
                        "refusal": None,
                    },
                }
            ],
            "usage": usage,
        }
    if provider == "anthropic":
        return {
            "id": "msg_offline",
            "type": "message",
            "role": "assistant",
            "model": "returned-test-model",
            "stop_reason": stop,
            "stop_sequence": None,
            "content": [{"type": "text", "text": raw}],
            "usage": usage,
        }
    return {
        "id": "resp_offline",
        "model": "returned-test-model",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": raw, "annotations": []}],
            }
        ],
        "usage": usage,
    }


@pytest.mark.parametrize("provider", ["groq", "deepseek"])
def test_compatible_provider_uses_only_its_own_headers(provider, snapshot, monkeypatch):
    monkeypatch.setenv("OPENAI_ORG_ID", "other-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "other-project")
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        "Authorization: Bearer other-secret\nX-Extra-Token: other-token\n"
        "Content-Type: text/plain\nHost: other.invalid",
    )
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=envelope(provider))

    backend = make_provider(provider, handle)
    assert backend.generate(backend.prepare(snapshot)).error is None
    (request,) = requests
    assert request.headers["authorization"] == "Bearer selected-test-secret"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["host"] == request.url.host
    assert not {"openai-organization", "openai-project", "x-extra-token"}.intersection(
        request.headers
    )


@pytest.mark.parametrize(
    "provider,url",
    [
        ("openai", "https://api.openai.com/v1/responses"),
        ("groq", "https://api.groq.com/openai/v1/chat/completions"),
        ("deepseek", "https://api.deepseek.com/responses"),
        ("anthropic", "https://api.anthropic.com/v1/messages"),
    ],
)
def test_selected_provider_sends_one_bounded_structured_request(
    provider, url, snapshot, monkeypatch
):
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "low")
    calls = []
    usage = {"input_tokens": 12, "output_tokens": 3}

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_offline", "request-id": "req_offline"},
            json=envelope(provider, usage=usage),
        )

    live = make_provider(provider, handle)
    prepared = live.prepare(snapshot)
    assert not calls
    result = live.generate(prepared)
    assert result.error is None and result.raw_output == "{}" and result.usage == usage
    assert len(calls) == 1 and str(calls[0].url) == url
    payload = json.loads(calls[0].content)
    if provider == "groq":
        assert payload["reasoning_effort"] == "low"
    else:
        assert "reasoning_effort" not in payload
    assert payload["model"] == "selected-test-model" and payload.get("tools", []) == []
    if provider == "anthropic":
        assert calls[0].headers["anthropic-version"] == "2026-10-21"
        assert calls[0].headers["authorization"] == "Bearer selected-test-secret"
        assert payload["max_tokens"] == 4096
        assert json.loads(payload["messages"][0]["content"]) == snapshot.model_dump(mode="json")
        schema = payload["output_config"]["format"]["schema"]
        assert payload["system"].startswith("Proofline assembly task.")
    elif provider == "groq":
        assert payload["max_completion_tokens"] == 4096
        assert payload["tool_choice"] == "none"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert json.loads(payload["messages"][1]["content"]) == snapshot.model_dump(mode="json")
        assert payload["messages"][0]["role"] == "system"
        schema = payload["response_format"]["json_schema"]["schema"]
        assert not {"store", "truncation", "background", "text", "input"}.intersection(payload)
    else:
        assert payload["max_output_tokens"] == 4096
        assert payload["tool_choice"] == "none"
        assert payload["text"]["format"]["strict"] is True
        assert json.loads(payload["input"]) == snapshot.model_dump(mode="json")
        schema = payload["text"]["format"]["schema"]
        if provider != "openai":
            assert "store" not in payload and "truncation" not in payload
        if provider == "deepseek":
            assert "background" not in payload
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Evidence"]["properties"]["fragments"]["minItems"] == 1
    profile = result.generation_profile
    assert profile["provider"] == provider and profile["endpoint"] == url
    assert profile["requested_model"] == "selected-test-model"
    assert profile["model"] == "returned-test-model"
    assert profile["provider_request_id"] == "req_offline"
    assert profile["request_sha256"] == json_digest(payload)
    assert profile["schema_sha256"] == json_digest(schema)
    assert profile["monetary_cost"] is None
    assert "selected-test-secret" not in json.dumps(profile)


@pytest.mark.parametrize("provider", ["groq", "deepseek", "anthropic"])
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "PROVIDER_ACCESS_DENIED"),
        (403, "PROVIDER_ACCESS_DENIED"),
        (402, "PROVIDER_PAYMENT_REQUIRED"),
        (429, "PROVIDER_RATE_LIMITED"),
        (400, "PROVIDER_REQUEST_REJECTED"),
        (500, "PROVIDER_ERROR"),
        (307, "PROVIDER_ERROR"),
    ],
)
def test_provider_http_errors_are_safe_and_never_retried(provider, status, code, snapshot):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={
                "location": "https://example.test/leak",
                "x-request-id": "req_error",
                "request-id": "req_error",
            },
            json={"error": {"message": "selected-test-secret private input"}},
        )

    live = make_provider(provider, handle)
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == code and len(calls) == 1
    assert result.usage is None and result.raw_output is None
    assert result.generation_profile["provider_request_id"] == "req_error"
    assert "selected-test-secret" not in repr(result) and "private input" not in repr(result)


@pytest.mark.parametrize(
    "stop,code",
    [
        ("max_tokens", "MODEL_OUTPUT_INCOMPLETE"),
        ("refusal", "MODEL_REFUSAL"),
        ("pause_turn", "MODEL_OUTPUT_INCOMPLETE"),
        ("tool_use", "INVALID_MODEL_OUTPUT"),
        (None, "INVALID_MODEL_OUTPUT"),
    ],
)
def test_anthropic_stop_reason_never_becomes_a_success(stop, code, snapshot):
    live = make_provider(
        "anthropic", lambda _: httpx.Response(200, json=envelope("anthropic", stop=stop))
    )
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == code


@pytest.mark.parametrize(
    "change", ["content_null", "text_null", "invalid_json", "refusal_details", "stop_object"]
)
def test_anthropic_malformed_output_and_refusal_details(snapshot, change):
    body = envelope("anthropic", usage={"input_tokens": 12})
    if change == "content_null":
        body["content"] = None
    elif change == "text_null":
        body["content"][0]["text"] = None
    elif change == "refusal_details":
        body["stop_details"] = {"type": "refusal", "category": "test"}
    elif change == "stop_object":
        body["stop_reason"] = {"unexpected": "shape"}
    live = make_provider(
        "anthropic",
        lambda _: httpx.Response(
            200,
            content=b"not json" if change == "invalid_json" else json.dumps(body).encode(),
            headers={"request-id": "req_malformed"},
        ),
    )
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == (
        "MODEL_REFUSAL" if change == "refusal_details" else "INVALID_MODEL_OUTPUT"
    )
    assert result.generation_profile["provider_request_id"] == "req_malformed"
    if change != "invalid_json":
        assert result.usage == {"input_tokens": 12}
        assert result.generation_profile["provider_response_id"] == "msg_offline"


@pytest.mark.parametrize("provider", ["groq", "deepseek", "anthropic"])
def test_provider_deadline_bounds_the_whole_call(provider, snapshot):
    async def handle(request):
        await asyncio.sleep(5)
        raise AssertionError("Deadline must cancel the request")

    live = make_provider(provider, handle, model_timeout_seconds=0.02)
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == "MODEL_TIMEOUT" and result.usage is None


@pytest.mark.parametrize("provider", ["openai", "groq", "deepseek"])
@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null"])
def test_malformed_compatible_envelope_retains_http_diagnostics(provider, raw, snapshot):
    live = make_provider(
        provider,
        lambda _: httpx.Response(
            200,
            content=raw,
            headers={"content-type": "application/json", "x-request-id": "req_known"},
        ),
    )
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == "INVALID_MODEL_OUTPUT"
    assert result.raw_output == raw.decode()
    assert result.generation_profile["provider_request_id"] == "req_known"
    assert result.generation_profile["raw_output_format"] == (
        "chat-completion-envelope" if provider == "groq" else "response-envelope"
    )
    assert result.generation_profile["latency_seconds"] >= 0
    assert result.usage is None


@pytest.mark.parametrize("provider", ["groq", "anthropic"])
@pytest.mark.parametrize("unsafe", ["bad\x00value", "bad\ud800value"])
def test_unsafe_provider_metadata_is_preserved_in_reversible_diagnostics(
    provider, unsafe, snapshot
):
    from proofline.domain.inputs import validate_storage_json

    body = envelope(provider, usage={"input_tokens": 12, "extra": unsafe})
    body["model"] = unsafe
    live = make_provider(
        provider,
        lambda _: httpx.Response(
            200,
            content=json.dumps(body, ensure_ascii=True).encode(),
            headers={"content-type": "application/json"},
        ),
    )
    result = live.generate(live.prepare(snapshot))
    assert result.error["code"] == "INVALID_MODEL_OUTPUT"
    validate_storage_json(result.generation_profile)
    validate_storage_json(result.usage)
    validate_storage_json(result.raw_output)
    diagnostic = json.loads(result.raw_output)
    assert diagnostic["generation_profile"]["model"] == unsafe
    assert diagnostic["usage"]["extra"] == unsafe
    assert result.generation_profile["model"] is None
    assert result.usage is None
