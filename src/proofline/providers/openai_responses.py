"""A single bounded Responses request using the official SDK, without retries."""

import asyncio
import hashlib
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming
from pydantic import JsonValue

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.providers.generation import GenerationResult
from proofline.providers.live_profile import (
    INSTRUCTIONS,
    POLICY,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from proofline.schema import generated_schema


@dataclass(frozen=True, slots=True)
class PreparedLive:
    request: ResponseCreateParamsNonStreaming
    generation_profile: dict[str, JsonValue]


class LiveProvider(Protocol):
    def prepare(self, input_data: AnalysisInput) -> PreparedLive: ...
    def generate(self, prepared: PreparedLive) -> GenerationResult: ...


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(code: str) -> dict[str, JsonValue]:
    return {"code": code, "message": "Live analysis failed; no automatic retry was made."}


class OpenAIResponsesProvider:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.live_unavailable_reason is not None or settings.openai_api_key is None:
            raise ValueError("Explicit live configuration is required")
        self._api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.timeout_seconds: float = settings.model_timeout_seconds
        self.max_output_tokens = settings.model_max_output_tokens
        self._transport = transport

    def prepare(self, input_data: AnalysisInput) -> PreparedLive:
        schema = generated_schema()
        request: ResponseCreateParamsNonStreaming = {
            "model": self.model,
            "instructions": INSTRUCTIONS,
            "input": canonical_json(input_data.model_dump(mode="json")),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "assembly_output",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": self.max_output_tokens,
            "tools": [],
            "tool_choice": "none",
            "store": False,
            "background": False,
            "truncation": "disabled",
        }
        profile: dict[str, JsonValue] = {
            "mode": "live",
            "provider": "openai",
            "api": "responses",
            "new_model_call": True,
            "usage_origin": "provider",
            "model": self.model,
            "requested_model": self.model,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": _sha(INSTRUCTIONS),
            "policy_version": POLICY_VERSION,
            "policy_sha256": _sha(POLICY),
            "schema_version": SCHEMA_VERSION,
            "schema_sha256": json_digest(schema),
            "request_sha256": json_digest(request),
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": 0,
            "provider_request_id": None,
            "provider_response_id": None,
            "monetary_cost": None,
            "pricing": None,
        }
        return PreparedLive(request, profile)

    def generate(self, prepared: PreparedLive) -> GenerationResult:
        # RunService is synchronous and is called in FastAPI's worker thread.
        # A private event loop makes cancellation bound the whole call, not just
        # individual HTTP connect/read phases. Nothing runs on a background thread.
        return asyncio.run(self._generate(prepared))

    async def _generate(self, prepared: PreparedLive) -> GenerationResult:
        started = perf_counter()
        profile = prepared.generation_profile.copy()
        raw: str | None = None
        usage: dict[str, JsonValue] | None = None
        error = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with AsyncOpenAI(
                    api_key=self._api_key.get_secret_value(),
                    base_url="https://api.openai.com/v1",
                    max_retries=0,
                    timeout=self.timeout_seconds,
                    http_client=httpx.AsyncClient(
                        transport=self._transport,
                        follow_redirects=False,
                        timeout=self.timeout_seconds,
                    ),
                ) as client:
                    response = await client.responses.create(**prepared.request)
            raw, usage, error = self._response(response, profile)
        except (TimeoutError, APITimeoutError):
            error = _error("MODEL_TIMEOUT")
        except APIStatusError as exc:
            profile["provider_request_id"] = exc.request_id
            profile["http_status"] = exc.status_code
            if exc.status_code in {401, 403}:
                code = "PROVIDER_ACCESS_DENIED"
            elif exc.status_code == 429:
                code = "PROVIDER_RATE_LIMITED"
            elif exc.status_code in {400, 404, 422}:
                code = "PROVIDER_REQUEST_REJECTED"
            else:
                code = "PROVIDER_ERROR"
            error = _error(code)
        except APIConnectionError:
            error = _error("PROVIDER_ERROR")
        # Never retain exception strings: SDK errors may contain request data.
        profile["latency_seconds"] = round(perf_counter() - started, 6)
        return GenerationResult(raw, profile, usage, error)

    @staticmethod
    def _response(
        response: Response, profile: dict[str, JsonValue]
    ) -> tuple[str | None, dict[str, JsonValue] | None, dict[str, JsonValue] | None]:
        # SDK response construction is intentionally permissive. Inspect JSON
        # before using convenience properties that assume non-null content/text.
        envelope: dict[str, JsonValue] = response.model_dump(
            mode="json", exclude_unset=True, warnings=False
        )
        incomplete = envelope.get("incomplete_details")
        profile.update(
            {
                "provider_request_id": response._request_id,
                "provider_response_id": envelope.get("id"),
                "model": envelope.get("model"),
                "response_status": envelope.get("status"),
                "incomplete_reason": incomplete.get("reason")
                if isinstance(incomplete, dict)
                else None,
            }
        )
        reported_usage = envelope.get("usage")
        usage = reported_usage if isinstance(reported_usage, dict) else None
        profile["raw_output_format"] = "response-envelope"
        diagnostic = canonical_json(envelope)
        status = envelope.get("status")
        if not isinstance(status, str):
            return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
        output = envelope.get("output")
        if not isinstance(output, list):
            return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
        texts: list[str] = []
        refused = False
        for item in output:
            if not isinstance(item, dict):
                return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
            if item.get("type") == "reasoning":
                continue
            content = item.get("content")
            if item.get("type") != "message" or not isinstance(content, list):
                return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
            for part in content:
                if not isinstance(part, dict):
                    return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
                if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refused = True
                elif part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text = part["text"]
                    assert isinstance(text, str)
                    texts.append(text)
                else:
                    return diagnostic, usage, _error("INVALID_MODEL_OUTPUT")
        raw = "".join(texts) if texts else canonical_json(output)
        profile["raw_output_format"] = "output-text" if texts else "output-items"
        if refused:
            return raw, usage, _error("MODEL_REFUSAL")
        if status in {"incomplete", "in_progress", "queued"}:
            return raw, usage, _error("MODEL_OUTPUT_INCOMPLETE")
        if status != "completed" or envelope.get("error") is not None:
            return raw, usage, _error("PROVIDER_ERROR")
        if not texts or not raw:
            return raw, usage, _error("INVALID_MODEL_OUTPUT")
        return raw, usage, None
