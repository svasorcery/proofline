"""Groq Chat contracts at the SDK wire boundary, with no provider inference."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.providers.factory import create_live_provider
from proofline.providers.replay import ReplayCatalog


@pytest.fixture
def snapshot():
    return ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures").recordings[0].input


def chat_body(raw="{}", *, finish="stop", usage=None):
    return {
        "id": "chatcmpl_offline",
        "object": "chat.completion",
        "created": 1,
        "model": "resolved-offline-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": raw, "refusal": None},
            }
        ],
        "usage": usage,
    }


def generate(snapshot, body, *, raw_http=None):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_chat", "content-type": "application/json"},
            content=raw_http if raw_http is not None else json.dumps(body).encode(),
        )

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline_test",
        live_enabled=True,
        llm_provider="groq",
        llm_model="openai/gpt-oss-120b",
        llm_api_key=SecretStr("offline-chat-secret"),
    )
    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    prepared = live.prepare(snapshot)
    assert calls == []
    return live.generate(prepared), calls


def test_groq_chat_preserves_v32_and_schema_and_sends_exactly_one_request(snapshot, valid_payload):
    raw = canonical_json(valid_payload)
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
        "completion_tokens_details": {"reasoning_tokens": 5},
    }
    result, calls = generate(snapshot, chat_body(raw, usage=usage))
    assert result.error is None and result.raw_output == raw and result.usage == usage
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[0].headers["authorization"] == "Bearer offline-chat-secret"
    payload = json.loads(calls[0].content)
    assert payload["max_completion_tokens"] == 4096
    assert payload["n"] == 1 and payload["stream"] is False
    assert payload["tools"] == [] and payload["tool_choice"] == "none"
    assert set(payload) == {
        "model",
        "messages",
        "response_format",
        "max_completion_tokens",
        "n",
        "stream",
        "tools",
        "tool_choice",
    }
    assert payload["model"] == "openai/gpt-oss-120b"
    messages = payload["messages"]
    assert len(messages) == 2 and messages[0]["role"] == "system"
    assert messages[1] == {
        "role": "user",
        "content": canonical_json(snapshot.model_dump(mode="json")),
    }
    schema_format = payload["response_format"]
    assert schema_format["type"] == "json_schema"
    assert schema_format["json_schema"]["strict"] is True
    assert schema_format["json_schema"]["name"] == "assembly_output"
    schema = schema_format["json_schema"]["schema"]
    assert schema["$defs"]["Evidence"]["properties"]["fragments"]["minItems"] == 1
    profile = result.generation_profile
    assert profile["api"] == "chat_completions"
    assert profile["request_profile"] == "groq-chat_completions-v1"
    assert profile["reasoning_effort"] == "provider-default"
    assert profile["endpoint"] == "https://api.groq.com/openai/v1/chat/completions"
    assert (
        profile["prompt_sha256"]
        == "6519091abb6f9669b4ed7d530b1867a42cee27d30baacd5064a0528ff84e562d"
    )
    assert (
        profile["policy_sha256"]
        == "f1490205098564aec23a1a4a5289d5e053fcb3f8611e09348690e1782846fa14"
    )
    assert (
        profile["schema_sha256"]
        == "9d031d5c4bb85493f85fc50e97990dc7b0a34e6853154aac59636b9275b54eaa"
    )
    assert profile["request_sha256"] == json_digest(payload)
    assert profile["provider_request_id"] == "req_chat"
    assert profile["provider_response_id"] == "chatcmpl_offline"
    assert profile["model"] == "resolved-offline-model"
    assert profile["max_retries"] == 0 and profile["monetary_cost"] is None
    assert "offline-chat-secret" not in repr(result)


def test_groq_explicit_effort_changes_only_one_wire_field_and_is_recorded(snapshot, monkeypatch):
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "")
    default, default_calls = generate(snapshot, chat_body())
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "medium")
    explicit, explicit_calls = generate(snapshot, chat_body())

    assert explicit.error is None
    assert len(default_calls) == len(explicit_calls) == 1
    default_body = json.loads(default_calls[0].content)
    explicit_body = json.loads(explicit_calls[0].content)
    assert explicit_body == {**default_body, "reasoning_effort": "medium"}
    profile = explicit.generation_profile
    assert profile["reasoning_effort"] == "medium"
    assert profile["request_profile"] == "groq-chat_completions-effort-v1"
    assert profile["request_sha256"] == json_digest(explicit_body)
    assert profile["request_sha256"] != default.generation_profile["request_sha256"]
    for field in ("policy_sha256", "prompt_sha256", "schema_sha256"):
        assert profile[field] == default.generation_profile[field]


@pytest.mark.parametrize("case", ["V02", "V04"])
def test_v33_test_wire_differs_from_archived_qwen_medium_only_in_instruction(case, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    archive = root / "evals/live/v5-groq-qwen-medium-2026-11-17"
    historical = json.loads((archive / f"full-{case}/run.json").read_text(encoding="utf-8"))
    snapshot = AnalysisInput.model_validate_json(json.dumps(historical["snapshot"]))
    expected = json.loads((archive / "request-template.json").read_text(encoding="utf-8"))
    expected["messages"][1]["content"] = canonical_json(historical["snapshot"])
    assert json_digest(expected) == historical["generation_profile"]["request_sha256"]

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=chat_body())

    monkeypatch.delenv("GROQ_PROMPT_PROFILE", raising=False)
    options = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://localhost/proofline_test",
        "live_enabled": True,
        "llm_provider": "groq",
        "llm_model": "qwen/qwen3.8-27b",
        "groq_reasoning_effort": "medium",
        "llm_api_key": SecretStr("offline-key"),
        "model_timeout_seconds": 90,
        "model_max_output_tokens": 4096,
    }
    default = create_live_provider(Settings(**options), transport=httpx.MockTransport(handle))
    default_result = default.generate(default.prepare(snapshot))
    assert json.loads(calls[0].content) == expected
    historical_profile = historical["generation_profile"]
    assert (
        default_result.generation_profile["request_profile"]
        == historical_profile["request_profile"]
    )
    assert default_result.generation_profile["policy_sha256"] == historical_profile["policy_sha256"]

    monkeypatch.setenv("GROQ_PROMPT_PROFILE", "v3.3-test")
    candidate = create_live_provider(Settings(**options), transport=httpx.MockTransport(handle))
    result = candidate.generate(candidate.prepare(snapshot))
    assert result.error is None and len(calls) == 2
    assert calls[1].url == calls[0].url
    actual = json.loads(calls[1].content)
    approved = (root / "docs/proposals/2026-11-17-live-policy-v3.3-candidate.txt").read_text(
        encoding="utf-8"
    )
    text = (
        approved.split("--- BEGIN MODEL INSTRUCTIONS ---", 1)[1]
        .split("--- END MODEL INSTRUCTIONS ---", 1)[0]
        .strip()
        + "\n"
    )
    expected["messages"][0]["content"] = (
        "Proofline assembly task. Apply the following policy to the JSON snapshot.\n" + text
    )
    assert actual == expected
    profile = result.generation_profile
    assert profile["prompt_version"] == "proofline-responses-prompt-v3-candidate-1"
    assert profile["policy_version"] == "proofline-policy-v3.3-candidate-1"
    assert profile["schema_sha256"] == historical_profile["schema_sha256"]
    assert profile["request_profile"] == "groq-chat_completions-v33-test-v1"
    assert profile["request_sha256"] == json_digest(actual)
    assert (
        profile["policy_sha256"]
        == "2d9b47d44aa0172a8c75ef6a5283009045695c088a174256ae9f4408abb691d3"
    )
    assert (
        profile["prompt_sha256"]
        == "4f1849841208622264bdc0d4c963626403bfc1f6da30343d459235fd23530269"
    )


@pytest.mark.parametrize(
    "finish,code",
    [
        ("length", "MODEL_OUTPUT_INCOMPLETE"),
        ("content_filter", "MODEL_REFUSAL"),
        ("tool_calls", "INVALID_MODEL_OUTPUT"),
        ("function_call", "INVALID_MODEL_OUTPUT"),
        ("unknown", "INVALID_MODEL_OUTPUT"),
        (None, "INVALID_MODEL_OUTPUT"),
        ({"bad": "shape"}, "INVALID_MODEL_OUTPUT"),
    ],
)
def test_groq_finish_reason_cannot_become_a_success(snapshot, finish, code):
    result, calls = generate(snapshot, chat_body(finish=finish, usage={"completion_tokens": 7}))
    assert result.error["code"] == code and len(calls) == 1
    assert result.usage == {"completion_tokens": 7}
    assert result.generation_profile["provider_request_id"] == "req_chat"


def test_groq_refusal_is_not_treated_as_json_and_reasoning_is_not_card_text(snapshot):
    body = chat_body()
    body["choices"][0]["message"].update(refusal="Cannot comply", reasoning="private reasoning")
    result, calls = generate(snapshot, body)
    assert result.error["code"] == "MODEL_REFUSAL" and len(calls) == 1
    body["choices"][0]["message"]["refusal"] = None
    result, _ = generate(snapshot, body)
    assert result.error is None and result.raw_output == "{}"


@pytest.mark.parametrize(
    "change",
    [
        "no_choices",
        "null_choices",
        "two_choices",
        "bad_choice",
        "index",
        "role",
        "null_message",
        "null_content",
        "content_array",
        "empty_content",
        "refusal_object",
        "tools_despite_stop",
        "function_despite_stop",
    ],
)
def test_groq_malformed_chat_envelope_is_preserved_as_diagnostic(snapshot, change):
    body = chat_body(usage={"prompt_tokens": 12})
    choice = body["choices"][0]
    message = choice["message"]
    if change == "no_choices":
        body["choices"] = []
    elif change == "null_choices":
        body["choices"] = None
    elif change == "two_choices":
        body["choices"] *= 2
    elif change == "bad_choice":
        body["choices"] = [None]
    elif change == "index":
        choice["index"] = 1
    elif change == "role":
        message["role"] = "user"
    elif change == "null_message":
        choice["message"] = None
    elif change == "null_content":
        message["content"] = None
    elif change == "content_array":
        message["content"] = [{"text": "{}"}]
    elif change == "empty_content":
        message["content"] = "  "
    elif change == "refusal_object":
        message["refusal"] = {"unexpected": "shape"}
    elif change == "tools_despite_stop":
        message["tool_calls"] = [{"id": "call_fake", "type": "function"}]
    else:
        message["function_call"] = {"name": "unsupported", "arguments": "{}"}
    result, calls = generate(snapshot, body)
    assert result.error["code"] == "INVALID_MODEL_OUTPUT" and len(calls) == 1
    assert result.generation_profile["raw_output_format"] == "chat-completion-envelope"
    assert result.generation_profile["provider_request_id"] == "req_chat"
    assert result.usage == {"prompt_tokens": 12}
    assert json.loads(result.raw_output)["choices"] == body["choices"]


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null"])
def test_groq_invalid_http_json_retains_raw_and_request_id(snapshot, raw):
    result, calls = generate(snapshot, None, raw_http=raw)
    assert result.error["code"] == "INVALID_MODEL_OUTPUT" and len(calls) == 1
    assert result.raw_output == raw.decode()
    assert result.generation_profile["provider_request_id"] == "req_chat"
    assert result.generation_profile["raw_output_format"] == "chat-completion-envelope"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("tool_calls", {}),
        ("tool_calls", 0),
        ("tool_calls", ""),
        ("tool_calls", False),
        ("function_call", {}),
        ("function_call", 0),
        ("function_call", ""),
        ("function_call", False),
        ("function_call", []),
    ],
)
def test_falsey_malformed_tool_fields_cannot_become_a_card(snapshot, field, bad):
    body = chat_body()
    body["choices"][0]["message"][field] = bad
    result, calls = generate(snapshot, body)
    assert result.error is not None and result.error["code"] == "INVALID_MODEL_OUTPUT"
    assert len(calls) == 1
    assert result.generation_profile["raw_output_format"] == "chat-completion-envelope"


@pytest.mark.parametrize("tools", [None, []])
def test_optional_empty_chat_tool_fields_are_accepted(snapshot, tools):
    body = chat_body()
    body["choices"][0]["message"].update(tool_calls=tools, function_call=None)
    result, _ = generate(snapshot, body)
    assert result.error is None and result.raw_output == "{}"
