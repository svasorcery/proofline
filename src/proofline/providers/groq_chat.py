"""One Groq Chat Completions request with strict JSON Schema and no retries."""

import asyncio
from dataclasses import replace
from time import perf_counter
from typing import cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletion
from openai.types.chat.completion_create_params import CompletionCreateParamsNonStreaming
from pydantic import JsonValue

from proofline.config import (
    GROQ_CHOICES_PROFILES,
    GROQ_CITATION_IDS_PROFILE,
    GROQ_CONFIRMATION_PROFILES,
    Settings,
)
from proofline.domain.case_evidence import (
    WITHDRAWAL_PROOF_REQUIREMENTS,
    reviewed_withdrawal_proof_missing,
    scoped_requirements,
)
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.domain.required_events import RequiredEvent
from proofline.providers import citation_ids, live_prompt_v33
from proofline.providers.citation_rule_bindings import (
    guest_v06_binding,
    reviewed_authorization_release,
    reviewed_citation_rules,
    reviewed_source_events,
    reviewed_withdrawal_fact,
    reviewed_withdrawal_timeline,
)
from proofline.providers.compatible_sdk import CompatibleAsyncOpenAI
from proofline.providers.generation import GenerationResult
from proofline.providers.live import (
    PreparedLive,
    prepare_live,
    safe_error,
    selected_live_prompt,
    storage_safe_result,
)
from proofline.providers.model_choices import (
    PROVIDER_SCHEMA_VERSION,
    REQUEST_PROFILE,
    compact_choices_schema,
    model_choices,
    restrict_reviewed_offer,
)
from proofline.providers.response_errors import classify_response_error, rejected_output_fields
from proofline.providers.reviewed_citations import ReviewedCitationRules
from proofline.schema import generated_schema

BASE_URL = "https://api.groq.com/openai/v1"


class GroqChatCompletionsProvider:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        key = settings.selected_api_key
        if (
            settings.llm_provider != "groq"
            or settings.live_unavailable_reason is not None
            or key is None
        ):
            raise ValueError("Explicit Groq live configuration is required")
        self._settings = settings
        self._api_key = key
        self._transport = transport
        self.timeout_seconds = settings.model_timeout_seconds

    def prepare(self, input_data: AnalysisInput) -> PreparedLive:
        settings = self._settings
        reviewed_input = input_data
        guest_required: tuple[RequiredEvent, ...] = ()
        if settings.guest_mode and settings.groq_prompt_profile == "v3.2":
            guest_binding = guest_v06_binding(input_data)
            if guest_binding is not None:
                reviewed_input, guest_required = guest_binding
                settings = settings.model_copy(
                    update={"groq_prompt_profile": GROQ_CITATION_IDS_PROFILE}
                )
        assembly_schema = generated_schema()
        choices = None
        reply = None
        prompt_profile = settings.groq_prompt_profile
        use_citation_ids = prompt_profile == GROQ_CITATION_IDS_PROFILE
        restrict_requirements = bool(guest_required) or settings.groq_test_case_evidence is not None
        required = guest_required or settings.required_events(input_data)
        reviewed_cancellation_times = (
            tuple(
                dict.fromkeys(
                    mark.time.value
                    for mark in required
                    if mark.event_type == "supplier_cancelled" and mark.time.role == "occurred"
                )
            )
            if input_data.case_id == "V05" and restrict_requirements
            else ()
        )
        refund = (settings.groq_test_case_evidence or {}).get(input_data.case_id)
        schema_name = "assembly_output"
        if prompt_profile in GROQ_CHOICES_PROFILES:
            approved = (settings.groq_test_lookup_sources or {}).get(input_data.case_id, ())
            if prompt_profile in GROQ_CONFIRMATION_PROFILES:
                reply = (settings.groq_test_acceptance_replies or {}).get(input_data.case_id)
            reply_already_included = reply is not None and reply.matches(input_data)
            choices = model_choices(
                input_data,
                approved,
                reply_already_included=reply_already_included,
                use_citation_ids=use_citation_ids,
                reviewed_cancellation_times=reviewed_cancellation_times,
                allowed_requirements=(
                    scoped_requirements(
                        input_data.requested_fact_keys,
                        reply_already_included=reply_already_included,
                    )
                    if restrict_requirements
                    else None
                ),
            )
            schema_name = "assembly_citation_ids" if use_citation_ids else "assembly_choices"
        schema = choices.schema if choices else assembly_schema
        has_reviewed_refund = (
            refund is not None
            and "payment_outcome" in input_data.requested_fact_keys
            and refund.selected_sources(input_data.to_validation_context().sources) is not None
        )
        if (
            choices is not None
            and use_citation_ids
            and input_data.case_id == "V03"
            and has_reviewed_refund
        ):
            # The provider must return three entries; the workflow checks which events they prove.
            choices.schema["properties"]["timeline"]["minItems"] = 3
        offer_quote = None
        compact = False
        source_event_types: tuple[str, ...] = ()
        assemble_evidence = bool(guest_required) or settings.groq_test_assemble_reviewed_evidence
        if (
            choices is not None
            and choices.citations is not None
            and (restrict_requirements or assemble_evidence)
        ):
            rules = (
                reviewed_citation_rules(
                    input_data, choices.citations, required, refund, choices.lookup_source_ids
                )
                if restrict_requirements
                else ReviewedCitationRules()
            )
            if assemble_evidence:
                release = reviewed_authorization_release(
                    reviewed_input, choices.citations, required
                )
                if release is not None:
                    if guest_required:
                        release["fill_missing_release"] = True
                    rules["authorization_release"] = release
                if reviewed_withdrawal_proof_missing(input_data):
                    rules["withdrawal_requirements"] = list(WITHDRAWAL_PROOF_REQUIREMENTS)
                withdrawal = reviewed_withdrawal_timeline(input_data, rules.get("event_roles", []))
                if withdrawal:
                    rules["withdrawal_events"] = withdrawal
                    proof = reviewed_withdrawal_fact(choices.citations, required, withdrawal)
                    if proof is not None:
                        rules["withdrawal_fact_evidence"] = proof
                events = reviewed_source_events(
                    input_data,
                    choices.citations,
                    required,
                    refund,
                    rules.get("refund_trace"),
                    reply,
                )
                if events:
                    rules["source_events"] = events
                    source_event_types = tuple(
                        dict.fromkeys(event["event_type"] for event in events)
                    )
                    compact = input_data.case_id == "V04"
                    if compact:
                        rules["compact_case_id"] = input_data.case_id
                        compact_choices_schema(choices.schema)
                        schema_name = "assembly_citation_ids_compact"
                    else:
                        event_enum = choices.schema["$defs"]["EventType"]
                        event_enum["enum"] = [
                            kind for kind in event_enum["enum"] if kind not in source_event_types
                        ]
                        choices.schema["properties"]["timeline"].pop("minItems", None)
            if rules:
                if "offer_quote" in rules:
                    offer_quote = rules["offer_quote"]
                    choices.schema["properties"]["timeline"]["minItems"] = 1
                    restrict_reviewed_offer(choices.schema, offer_quote)
                elif "event_roles" in rules:
                    # The reviewed receipt and cancellation both need a timeline entry.
                    choices.schema["properties"]["timeline"]["minItems"] = 2
                choices = replace(choices, reviewed_citations=rules)
        prompt = selected_live_prompt(
            settings,
            has_lookup=bool(choices and choices.lookup_source_ids),
            required_events=required,
            has_reviewed_refund=has_reviewed_refund,
            has_reviewed_cancellation=bool(reviewed_cancellation_times),
        )
        if input_data.case_id == "V02" and use_citation_ids and restrict_requirements:
            sent = [mark for mark in required if mark.identity[:2] == ("customer_replied", "sent")]
            received = [
                mark
                for mark in required
                if mark.identity[:2] == ("customer_message_received", "received")
            ]
            if len(sent) == len(received) == 1 and sent[0].source_id == received[0].source_id:
                guidance = (
                    citation_ids.REVIEWED_SUPPORT_FORM_FOLLOW_UP
                    if source_event_types
                    else citation_ids.REVIEWED_SUPPORT_FORM_GUIDANCE.format(
                        sent_at=sent[0].time.value, received_at=received[0].time.value
                    )
                )
                prompt = replace(
                    prompt,
                    prompt_version=prompt.prompt_version + "-support-form-timing-v2",
                    instructions=prompt.instructions + guidance,
                )
        elif input_data.case_id == "V03" and use_citation_ids and has_reviewed_refund:
            prompt = replace(
                prompt,
                prompt_version=prompt.prompt_version.removesuffix("-refund-evidence-v1")
                + "-refund-evidence-v3",
                instructions=prompt.instructions.replace(
                    live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE, ""
                ).replace(live_prompt_v33.REPLY_PAIR_EXAMPLE, ""),
            )
        if offer_quote is not None:
            prompt = replace(
                prompt,
                prompt_version=prompt.prompt_version + "-reviewed-offer-v1",
                instructions=prompt.instructions
                + citation_ids.REVIEWED_OFFER_GUIDANCE.format(**offer_quote),
            )
        if source_event_types:
            instructions = (
                prompt.instructions.replace(live_prompt_v33.REPLY_PAIR_EXAMPLE, "")
                .replace(live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE, "")
                .replace(citation_ids.TIMELINE_EXAMPLE, "")
                .replace(
                    citation_ids.REFUND_EVIDENCE_GUIDANCE,
                    citation_ids.REVIEWED_REFUND_FACT_GUIDANCE,
                )
            )
            if compact:
                before, _, timeline = instructions.partition("\nTIMELINE\n")
                _, section, after = timeline.partition("\nREADINESS AND NEXT STEP\n")
                instructions = (before + section + after).replace("Copy case_id exactly. ", "")
            prompt = replace(
                prompt,
                prompt_version=prompt.prompt_version
                + ("-compact-reply-v1" if compact else "-reviewed-evidence-v1"),
                instructions=instructions
                + (
                    citation_ids.COMPACT_REPLY_GUIDANCE
                    if compact
                    else citation_ids.REVIEWED_SOURCE_EVENT_GUIDANCE.format(
                        event_types=", ".join(source_event_types)
                    )
                ),
            )
        user_input = (
            choices.input_json if choices else canonical_json(input_data.model_dump(mode="json"))
        )
        request: dict[str, JsonValue] = {
            "model": settings.selected_model,
            "messages": [
                {"role": "system", "content": prompt.instructions},
                {"role": "user", "content": user_input},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_completion_tokens": settings.model_max_output_tokens,
            "n": 1,
            "stream": False,
            "tools": [],
            "tool_choice": "none",
        }
        effort = settings.groq_reasoning_effort
        if effort is not None:
            request["reasoning_effort"] = effort
        prepared = prepare_live(
            settings,
            request,
            schema,
            api="chat_completions",
            endpoint=f"{BASE_URL}/chat/completions",
            prompt=prompt,
        )
        prepared.generation_profile["reasoning_effort"] = effort or "provider-default"
        if required:
            prepared.generation_profile["reviewed_timeline_events"] = len(required)
        if choices:
            prepared.generation_profile.update(
                request_profile=REQUEST_PROFILE,
                provider_schema_version=PROVIDER_SCHEMA_VERSION,
                assembly_schema_sha256=json_digest(assembly_schema),
                provider_lookup_source_ids=list(choices.lookup_source_ids),
            )
            if prompt_profile in GROQ_CONFIRMATION_PROFILES:
                prepared.generation_profile.update(
                    request_profile="groq-chat_completions-v33-confirmations-test-v1",
                    provider_covered_requirements=(
                        ["original_acceptance_reply"] if choices.reply_already_included else []
                    ),
                )
                if choices.reply_already_included and reply is not None:
                    prepared.generation_profile["provider_reply_evidence"] = reply.model_dump(
                        mode="json", exclude={"fragment", "event_evidence"}
                    )
            if choices.citations is not None:
                prepared.generation_profile.update(
                    request_profile=(
                        citation_ids.REQUEST_PROFILE + "-case-evidence-v1"
                        if restrict_requirements
                        else citation_ids.REQUEST_PROFILE
                    ),
                    provider_schema_version=(
                        citation_ids.COMPACT_PROVIDER_SCHEMA_VERSION
                        if compact
                        else citation_ids.PROVIDER_SCHEMA_VERSION
                    ),
                    provider_citation_catalogue_sha256=json_digest(choices.citations.lines),
                    provider_citation_count=len(choices.citations.spans),
                    canonical_timeline_order=citation_ids.CANONICAL_TIMELINE_ORDER,
                )
                if choices.reviewed_citations:
                    prepared.generation_profile["reviewed_citation_rules_v1"] = cast(
                        JsonValue, choices.reviewed_citations
                    )
            return replace(prepared, decode_output=choices.decode)
        if prompt_profile == "v3.3-test":
            prepared.generation_profile["request_profile"] = "groq-chat_completions-v33-test-v1"
        elif effort is not None:
            prepared.generation_profile["request_profile"] = "groq-chat_completions-effort-v1"
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
            async with asyncio.timeout(self.timeout_seconds):
                async with CompatibleAsyncOpenAI(
                    api_key=self._api_key.get_secret_value(),
                    base_url=BASE_URL,
                    max_retries=0,
                    timeout=self.timeout_seconds,
                    http_client=httpx.AsyncClient(
                        transport=self._transport,
                        follow_redirects=False,
                        timeout=self.timeout_seconds,
                    ),
                ) as client:
                    received = await client.chat.completions.with_raw_response.create(
                        **cast(CompletionCreateParamsNonStreaming, prepared.request)
                    )
            # Keep raw HTTP diagnostics even when the permissive SDK cannot parse them.
            raw = received.text
            profile["provider_request_id"] = received.headers.get("x-request-id")
            profile["raw_output_format"] = "chat-completion-envelope"
            try:
                response = received.parse()
                if isinstance(response, ChatCompletion):
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
                "groq",
                exc.status_code,
                code=exc.code,
                error_type=exc.type,
                param=exc.param,
            )
            profile.update(diagnostics)
            if diagnostics["provider_error_stage"] == "output_validation":
                fields = rejected_output_fields(exc.body, prepared.request.get("response_format"))
                if fields is not None:
                    profile["provider_output_field_diagnostics"] = fields
            error = safe_error(code)
        except APIConnectionError:
            error = safe_error("PROVIDER_ERROR")
        profile["latency_seconds"] = round(perf_counter() - started, 6)
        return storage_safe_result(raw, profile, usage, error)

    @staticmethod
    def _response(
        response: ChatCompletion, profile: dict[str, JsonValue]
    ) -> tuple[str, dict[str, JsonValue] | None, dict[str, JsonValue] | None]:
        # SDK annotations do not validate provider JSON. Inspect plain data first.
        envelope: dict[str, JsonValue] = response.model_dump(
            mode="json", exclude_unset=True, warnings=False
        )
        profile.update(provider_response_id=envelope.get("id"), model=envelope.get("model"))
        reported_usage = envelope.get("usage")
        usage = reported_usage if isinstance(reported_usage, dict) else None
        diagnostic = canonical_json(envelope)
        invalid = (diagnostic, usage, safe_error("INVALID_MODEL_OUTPUT"))
        choices = envelope.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            return invalid
        choice = choices[0]
        if (
            not isinstance(choice, dict)
            or type(choice.get("index")) is not int
            or choice["index"] != 0
        ):
            return invalid
        finish = choice.get("finish_reason")
        profile["finish_reason"] = finish
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return invalid
        refusal = message.get("refusal")
        if refusal is not None and not isinstance(refusal, str):
            return invalid
        if refusal or finish == "content_filter":
            return diagnostic, usage, safe_error("MODEL_REFUSAL")
        if finish == "length":
            return diagnostic, usage, safe_error("MODEL_OUTPUT_INCOMPLETE")
        if finish != "stop" or message.get("function_call") is not None:
            return invalid
        tool_calls = message.get("tool_calls")
        if tool_calls is not None and (not isinstance(tool_calls, list) or tool_calls):
            return invalid
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            return invalid
        profile["raw_output_format"] = "output-text"
        # Provider reasoning fields are diagnostic metadata, never part of the card.
        return text, usage, None
