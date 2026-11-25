"""Application-owned live contract and safe, provider-independent provenance."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import JsonValue

from proofline.config import GROQ_CITATION_IDS_PROFILE, Settings
from proofline.domain.assembly import AssemblyOutput
from proofline.domain.inputs import AnalysisInput, json_digest, validate_storage_json
from proofline.domain.required_events import RequiredEvent
from proofline.providers import live_prompt_v33
from proofline.providers.citation_ids import (
    BANK_OPERATIONS_GUIDANCE,
    CANCELLATION_NOTICE_GUIDANCE,
    LOOKUP_EXAMPLE,
    LOOKUP_RECEIPT_GUIDANCE,
    REFUND_EVIDENCE_GUIDANCE,
    TIMELINE_EXAMPLE,
)
from proofline.providers.citation_ids import INSTRUCTION as CITATION_IDS_INSTRUCTION
from proofline.providers.generation import GenerationResult
from proofline.providers.live_profile import (
    INSTRUCTIONS,
    POLICY,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from proofline.providers.model_choices import INSTRUCTION as CHOICES_INSTRUCTION


@dataclass(frozen=True, slots=True)
class PreparedLive:
    request: dict[str, JsonValue]
    generation_profile: dict[str, JsonValue]
    decode_output: Callable[[str], AssemblyOutput] | None = None


@dataclass(frozen=True, slots=True)
class LivePrompt:
    prompt_version: str
    policy_version: str
    policy: str
    instructions: str


_DEFAULT_PROMPT = LivePrompt(PROMPT_VERSION, POLICY_VERSION, POLICY, INSTRUCTIONS)
_GROQ_TEST_PROMPT = LivePrompt(
    live_prompt_v33.PROMPT_VERSION,
    live_prompt_v33.POLICY_VERSION,
    live_prompt_v33.POLICY,
    INSTRUCTIONS.removesuffix(POLICY) + live_prompt_v33.POLICY,
)
_GROQ_CHOICES_PROMPT = LivePrompt(
    live_prompt_v33.PROMPT_VERSION + "-choices-v1",
    live_prompt_v33.POLICY_VERSION,
    live_prompt_v33.POLICY,
    _GROQ_TEST_PROMPT.instructions + CHOICES_INSTRUCTION,
)
_GROQ_CONFIRMATIONS_PROMPT = replace(
    _GROQ_CHOICES_PROMPT,
    prompt_version=_GROQ_CHOICES_PROMPT.prompt_version + "-confirmations-example-v1",
    instructions=_GROQ_CHOICES_PROMPT.instructions
    + live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE,
)
_GROQ_CITATION_IDS_PROMPT = replace(
    _GROQ_CONFIRMATIONS_PROMPT,
    prompt_version=_GROQ_CONFIRMATIONS_PROMPT.prompt_version + "-citation-ids-v1",
    instructions=_GROQ_CONFIRMATIONS_PROMPT.instructions + CITATION_IDS_INSTRUCTION,
)
_GROQ_REVIEWED_TIMELINE_PROMPT = replace(
    _GROQ_CITATION_IDS_PROMPT,
    prompt_version=_GROQ_CITATION_IDS_PROMPT.prompt_version + "-timeline-example-v1",
    instructions=_GROQ_CITATION_IDS_PROMPT.instructions.replace(
        live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE, ""
    )
    + TIMELINE_EXAMPLE,
)


def selected_live_prompt(
    settings: Settings,
    *,
    has_lookup: bool = False,
    required_events: tuple[RequiredEvent, ...] = (),
    has_reviewed_refund: bool = False,
    has_reviewed_cancellation: bool = False,
) -> LivePrompt:
    if settings.llm_provider == "groq":
        if settings.groq_prompt_profile == GROQ_CITATION_IDS_PROFILE:
            prompt = (
                _GROQ_REVIEWED_TIMELINE_PROMPT
                if required_events and not has_lookup
                else _GROQ_CITATION_IDS_PROMPT
            )
            if has_lookup:
                documented_events = bool(required_events)
                prompt = replace(
                    prompt,
                    prompt_version=(
                        prompt.prompt_version
                        + (
                            "-lookup-example-receipt-v1"
                            if documented_events
                            else "-lookup-example-v1"
                        )
                    ),
                    instructions=(
                        prompt.instructions
                        + LOOKUP_EXAMPLE
                        + (LOOKUP_RECEIPT_GUIDANCE if documented_events else "")
                    ),
                )
            if any(mark.event_type == "authorization_released" for mark in required_events):
                prompt = replace(
                    prompt,
                    prompt_version=prompt.prompt_version + "-bank-operations-v1",
                    instructions=prompt.instructions + BANK_OPERATIONS_GUIDANCE,
                )
            if has_reviewed_refund:
                prompt = replace(
                    prompt,
                    prompt_version=prompt.prompt_version + "-refund-evidence-v1",
                    instructions=prompt.instructions + REFUND_EVIDENCE_GUIDANCE,
                )
            if has_reviewed_cancellation:
                prompt = replace(
                    prompt,
                    prompt_version=prompt.prompt_version + "-cancellation-notice-v2",
                    instructions=prompt.instructions + CANCELLATION_NOTICE_GUIDANCE,
                )
            return prompt
        if settings.groq_prompt_profile == "v3.3-confirmations-test":
            return _GROQ_CONFIRMATIONS_PROMPT
        if settings.groq_prompt_profile == "v3.3-choices-test":
            return _GROQ_CHOICES_PROMPT
        if settings.groq_prompt_profile == "v3.3-test":
            return _GROQ_TEST_PROMPT
    return _DEFAULT_PROMPT


class LiveProvider(Protocol):
    def prepare(self, input_data: AnalysisInput) -> PreparedLive: ...
    def generate(self, prepared: PreparedLive) -> GenerationResult: ...


def prepare_live(
    settings: Settings,
    request: dict[str, JsonValue],
    schema: object,
    *,
    api: str,
    endpoint: str,
    prompt: LivePrompt | None = None,
) -> PreparedLive:
    prompt = prompt or selected_live_prompt(settings)
    profile: dict[str, JsonValue] = {
        "mode": "live",
        "provider": settings.llm_provider,
        "api": api,
        "endpoint": endpoint,
        "request_profile": f"{settings.llm_provider}-{api}-v1",
        "new_model_call": True,
        "usage_origin": "provider",
        "model": settings.selected_model,
        "requested_model": settings.selected_model,
        "prompt_version": prompt.prompt_version,
        "prompt_sha256": hashlib.sha256(prompt.instructions.encode("utf-8")).hexdigest(),
        "policy_version": prompt.policy_version,
        "policy_sha256": hashlib.sha256(prompt.policy.encode("utf-8")).hexdigest(),
        "schema_version": SCHEMA_VERSION,
        "schema_sha256": json_digest(schema),
        "request_sha256": json_digest(request),
        "max_output_tokens": settings.model_max_output_tokens,
        "timeout_seconds": settings.model_timeout_seconds,
        "max_retries": 0,
        "provider_request_id": None,
        "provider_response_id": None,
        "monetary_cost": None,
        "pricing": None,
    }
    return PreparedLive(request, profile)


def safe_error(code: str) -> dict[str, JsonValue]:
    return {"code": code, "message": "Live analysis failed; no automatic retry was made."}


def storage_safe_result(
    raw: str | None,
    profile: dict[str, JsonValue],
    usage: dict[str, JsonValue] | None,
    error: dict[str, JsonValue] | None,
) -> GenerationResult:
    """Retain unsafe provider metadata as escaped text, never as invalid JSONB."""
    try:
        validate_storage_json(profile)
        validate_storage_json(usage)
    except ValueError:
        diagnostic = json.dumps(
            {"raw_output": raw, "generation_profile": profile, "usage": usage, "error": error},
            ensure_ascii=True,
        )
        safe_profile: dict[str, JsonValue] = {}
        for key, value in profile.items():
            try:
                validate_storage_json(value)
            except ValueError:
                safe_profile[key] = None
            else:
                safe_profile[key] = value
        safe_profile["raw_output_format"] = "provider-diagnostic"
        try:
            validate_storage_json(usage)
        except ValueError:
            usage = None
        return GenerationResult(diagnostic, safe_profile, usage, safe_error("INVALID_MODEL_OUTPUT"))
    return GenerationResult(raw, profile, usage, error)


def http_error_code(status: int) -> str:
    if status in {401, 403}:
        return "PROVIDER_ACCESS_DENIED"
    if status == 402:
        return "PROVIDER_PAYMENT_REQUIRED"
    if status == 429:
        return "PROVIDER_RATE_LIMITED"
    if status in {400, 404, 413, 422}:
        return "PROVIDER_REQUEST_REJECTED"
    return "PROVIDER_ERROR"
