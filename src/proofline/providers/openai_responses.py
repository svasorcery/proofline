"""A single bounded Responses request using the official SDK, without retries."""

import asyncio
from time import perf_counter
from typing import cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming
from pydantic import JsonValue

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, canonical_json
from proofline.providers.compatible_sdk import CompatibleAsyncOpenAI
from proofline.providers.generation import GenerationResult
from proofline.providers.live import (
    PreparedLive,
    prepare_live,
    safe_error,
    storage_safe_result,
)
from proofline.providers.live_profile import INSTRUCTIONS
from proofline.providers.response_errors import classify_response_error
from proofline.schema import generated_schema

ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
}


class OpenAIResponsesProvider:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        key = settings.selected_api_key
        if (
            settings.llm_provider not in ENDPOINTS
            or settings.live_unavailable_reason is not None
            or key is None
        ):
            raise ValueError("Explicit live configuration is required")
        self._settings = settings
        self._base_url = ENDPOINTS[settings.llm_provider]
        self._api_key = key
        self.model = settings.selected_model
        self.timeout_seconds: float = settings.model_timeout_seconds
        self.max_output_tokens = settings.model_max_output_tokens
        self._transport = transport

    def prepare(self, input_data: AnalysisInput) -> PreparedLive:
        schema = generated_schema()
        request: dict[str, JsonValue] = {
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
        }
        # Explicit protocol profiles: unsupported flags are not sent to other APIs.
        if self._settings.llm_provider == "openai":
            request.update(store=False, background=False, truncation="disabled")
        return prepare_live(
            self._settings, request, schema, api="responses", endpoint=f"{self._base_url}/responses"
        )

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
        sdk = AsyncOpenAI if self._settings.llm_provider == "openai" else CompatibleAsyncOpenAI
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with sdk(
                    api_key=self._api_key.get_secret_value(),
                    base_url=self._base_url,
                    max_retries=0,
                    timeout=self.timeout_seconds,
                    http_client=httpx.AsyncClient(
                        transport=self._transport,
                        follow_redirects=False,
                        timeout=self.timeout_seconds,
                    ),
                ) as client:
                    received = await client.responses.with_raw_response.create(
                        **cast(ResponseCreateParamsNonStreaming, prepared.request)
                    )
            # Keep transport diagnostics before permissive SDK parsing can fail.
            raw = received.text
            profile["provider_request_id"] = received.headers.get("x-request-id")
            profile["raw_output_format"] = "response-envelope"
            try:
                response = received.parse()
                if isinstance(response, Response):
                    raw, usage, error = self._response(response, profile)
                else:
                    error = safe_error("INVALID_MODEL_OUTPUT")
            except (ValueError, TypeError, AttributeError):
                error = safe_error("INVALID_MODEL_OUTPUT")
        except (TimeoutError, APITimeoutError):
            error = safe_error("MODEL_TIMEOUT")
        except APIStatusError as exc:
            profile["provider_request_id"] = exc.request_id
            profile["http_status"] = exc.status_code
            code, diagnostics = classify_response_error(
                self._settings.llm_provider,
                exc.status_code,
                code=exc.code,
                error_type=exc.type,
                param=exc.param,
            )
            profile.update(diagnostics)
            error = safe_error(code)
        except APIConnectionError:
            error = safe_error("PROVIDER_ERROR")
        # Never retain exception strings: SDK errors may contain request data.
        profile["latency_seconds"] = round(perf_counter() - started, 6)
        return storage_safe_result(raw, profile, usage, error)

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
            return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
        output = envelope.get("output")
        if not isinstance(output, list):
            return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
        texts: list[str] = []
        refused = False
        for item in output:
            if not isinstance(item, dict):
                return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
            if item.get("type") == "reasoning":
                continue
            content = item.get("content")
            if item.get("type") != "message" or not isinstance(content, list):
                return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
            for part in content:
                if not isinstance(part, dict):
                    return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
                if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refused = True
                elif part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text = part["text"]
                    assert isinstance(text, str)
                    texts.append(text)
                else:
                    return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
        raw = "".join(texts) if texts else canonical_json(output)
        profile["raw_output_format"] = "output-text" if texts else "output-items"
        if refused:
            return raw, usage, safe_error("MODEL_REFUSAL")
        if status in {"incomplete", "in_progress", "queued"}:
            return raw, usage, safe_error("MODEL_OUTPUT_INCOMPLETE")
        if status != "completed" or envelope.get("error") is not None:
            return raw, usage, safe_error("PROVIDER_ERROR")
        if not texts or not raw:
            return raw, usage, safe_error("INVALID_MODEL_OUTPUT")
        return raw, usage, None
