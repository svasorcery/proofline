"""One native Anthropic Messages request over HTTPX; no tools or retries."""

import asyncio
from time import perf_counter

import httpx
from pydantic import JsonValue

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, canonical_json
from proofline.providers.generation import GenerationResult
from proofline.providers.live import (
    PreparedLive,
    http_error_code,
    prepare_live,
    safe_error,
    storage_safe_result,
)
from proofline.providers.live_profile import INSTRUCTIONS
from proofline.schema import generated_schema

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"


class AnthropicMessagesProvider:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        key = settings.selected_api_key if settings.llm_provider == "anthropic" else None
        if key is None or settings.live_unavailable_reason is not None:
            raise ValueError("Explicit Anthropic live configuration is required")
        self._settings = settings
        self._api_key = key
        self._transport = transport

    def prepare(self, input_data: AnalysisInput) -> PreparedLive:
        schema = generated_schema()
        # The current contract is supported directly, including minItems=1.
        # Never remove constraints from AssemblyOutput to accommodate a provider.
        request: dict[str, JsonValue] = {
            "model": self._settings.selected_model,
            "system": INSTRUCTIONS,
            "messages": [
                {"role": "user", "content": canonical_json(input_data.model_dump(mode="json"))}
            ],
            "max_tokens": self._settings.model_max_output_tokens,
            "stream": False,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        prepared = prepare_live(self._settings, request, schema, api="messages", endpoint=ENDPOINT)
        prepared.generation_profile["api_version"] = API_VERSION
        return prepared

    def generate(self, prepared: PreparedLive) -> GenerationResult:
        return asyncio.run(self._generate(prepared))

    async def _generate(self, prepared: PreparedLive) -> GenerationResult:
        started = perf_counter()
        profile = prepared.generation_profile.copy()
        raw: str | None = None
        usage: dict[str, JsonValue] | None = None
        error = None
        try:
            async with asyncio.timeout(self._settings.model_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    follow_redirects=False,
                    timeout=self._settings.model_timeout_seconds,
                ) as client:
                    response = await client.post(
                        ENDPOINT,
                        json=prepared.request,
                        headers={
                            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                            "anthropic-version": API_VERSION,
                        },
                    )
            profile["provider_request_id"] = response.headers.get(
                "request-id"
            ) or response.headers.get("x-request-id")
            if response.status_code != 200:
                profile["http_status"] = response.status_code
                error = safe_error(http_error_code(response.status_code))
            else:
                raw = response.text
                try:
                    body = response.json()
                    # Reject non-finite numbers before diagnostics reach JSONB.
                    canonical_json(body)
                except ValueError:
                    error = safe_error("INVALID_MODEL_OUTPUT")
                else:
                    raw, usage, error = self._response(body, profile)
        except (TimeoutError, httpx.TimeoutException):
            error = safe_error("MODEL_TIMEOUT")
        except httpx.RequestError:
            error = safe_error("PROVIDER_ERROR")
        profile["latency_seconds"] = round(perf_counter() - started, 6)
        return storage_safe_result(raw, profile, usage, error)

    @staticmethod
    def _response(
        body: JsonValue, profile: dict[str, JsonValue]
    ) -> tuple[str, dict[str, JsonValue] | None, dict[str, JsonValue] | None]:
        diagnostic = canonical_json(body)
        profile["raw_output_format"] = "response-envelope"
        if not isinstance(body, dict):
            return diagnostic, None, safe_error("INVALID_MODEL_OUTPUT")
        profile.update(
            {
                "provider_response_id": body.get("id"),
                "model": body.get("model"),
                "response_status": body.get("stop_reason"),
            }
        )
        reported = body.get("usage")
        usage = reported if isinstance(reported, dict) else None
        content = body.get("content")
        if not isinstance(content, list):
            return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
        texts: list[str] = []
        for part in content:
            if (
                not isinstance(part, dict)
                or part.get("type") != "text"
                or not isinstance(part.get("text"), str)
            ):
                return diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT")
            text = part["text"]
            assert isinstance(text, str)
            texts.append(text)
        raw = "".join(texts) if texts else diagnostic
        profile["raw_output_format"] = "output-text" if texts else "response-envelope"
        stop = body.get("stop_reason")
        details = body.get("stop_details")
        if stop == "refusal" or (isinstance(details, dict) and details.get("type") == "refusal"):
            return raw, usage, safe_error("MODEL_REFUSAL")
        if isinstance(stop, str) and stop in {
            "max_tokens",
            "pause_turn",
            "model_context_window_exceeded",
        }:
            return raw, usage, safe_error("MODEL_OUTPUT_INCOMPLETE")
        if (
            stop != "end_turn"
            or body.get("type") != "message"
            or body.get("role") != "assistant"
            or not texts
            or not raw
        ):
            return raw, usage, safe_error("INVALID_MODEL_OUTPUT")
        return raw, usage, None
