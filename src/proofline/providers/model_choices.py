"""Case-specific request and output conversion for the isolated Groq test."""

import json
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import JsonSchemaValue

from proofline.domain.assembly import (
    AssemblyOutput,
    Evidence,
    FactKey,
    FactValue,
    RequirementKey,
    TimelineEvent,
)
from proofline.domain.inputs import AnalysisInput, canonical_json
from proofline.providers.citation_ids import CitationCatalogue
from proofline.providers.reviewed_citations import (
    COMPACT_RESPONSE_FIELDS,
    OfferQuoteRule,
    ReviewedCitationRules,
    apply_reviewed_citation_rules,
    expand_compact_response,
)
from proofline.validation.timestamps import parse_instant

PROVIDER_SCHEMA_VERSION = "assembly-choices-v1"
REQUEST_PROFILE = "groq-chat_completions-v33-choices-test-v1"
INSTRUCTION = (
    "\nFor this test, facts is an object keyed by requested_fact_keys. "
    "Each property contains value and evidence. Use a follow-up action and locator "
    "pair allowed by the output schema. Only included source text can serve as evidence."
)
_LOOKUP_ACTION = {
    "document": "read_source",
    "provider_log": "query_provider",
    "payment_ledger": "query_payment",
}


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _FactAnswer(_ClosedModel):
    value: FactValue
    evidence: list[Evidence]


class _NoFollowUp(_ClosedModel):
    action: Literal["none"]
    locator: None
    requirements: Annotated[list[RequirementKey], Field(max_length=0)]


class _AskUser(_ClosedModel):
    action: Literal["ask_user"]
    locator: None
    requirements: Annotated[list[RequirementKey], Field(min_length=1)]


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate key in model output")
    return result


@dataclass(frozen=True, slots=True)
class ModelChoices:
    model: type[AssemblyOutput]
    schema: JsonSchemaValue
    input_json: str
    lookup_source_ids: tuple[str, ...]
    fact_keys: tuple[FactKey, ...]
    reply_already_included: bool
    citations: CitationCatalogue | None
    reviewed_citations: ReviewedCitationRules | None = None

    def decode(self, raw_output: str) -> AssemblyOutput:
        parsed = json.loads(raw_output, object_pairs_hook=_without_duplicate_keys)
        if self.reviewed_citations and "compact_case_id" in self.reviewed_citations:
            parsed = expand_compact_response(parsed, self.reviewed_citations["compact_case_id"])
        body = self.model.model_validate(parsed).model_dump(mode="json")
        if self.reviewed_citations:
            if self.citations is not None:
                for field in [*body["facts"].values(), *body["timeline"]]:
                    self.citations.validate_ids(field["evidence"])
            apply_reviewed_citation_rules(body, self.reviewed_citations)
        if (
            self.reply_already_included
            and "original_acceptance_reply" in body["follow_up"]["requirements"]
        ):
            raise ValueError("The requested reply is already included")
        facts = body["facts"]
        body["facts"] = [{"key": key, **facts[key]} for key in self.fact_keys]
        if self.citations is not None:
            for field in [*body["facts"], *body["timeline"]]:
                field["evidence"] = self.citations.resolve(field["evidence"])
            # Keep the original positions for the validator's timestamp diagnostics.
            with suppress(ValueError, OverflowError):
                body["timeline"] = sorted(
                    body["timeline"], key=lambda event: parse_instant(event["time"]["value"])
                )
        return AssemblyOutput.model_validate(body)


def compact_choices_schema(schema: JsonSchemaValue) -> None:
    properties = schema["properties"]
    schema["required"] = list(COMPACT_RESPONSE_FIELDS)
    schema["properties"] = {key: properties[key] for key in COMPACT_RESPONSE_FIELDS}
    for name in ("EventType", "EventTime", "TimeRole", "TimelineEventWithCitationIds"):
        schema["$defs"].pop(name, None)


def _reviewed_timeline_variant(
    definitions: JsonSchemaValue,
    template: JsonSchemaValue,
    event_type: str,
    role: str,
    times: tuple[str, ...] = (),
) -> JsonSchemaValue:
    event = deepcopy(template)
    event["properties"]["event_type"] = {"type": "string", "enum": [event_type]}
    time = deepcopy(definitions["EventTime"])
    time["properties"]["role"] = {"type": "string", "enum": [role]}
    if times:
        time["properties"]["value"] = {"type": "string", "enum": list(times)}
    event["properties"]["time"] = time
    return event


def _restrict_supplier_cancellation_times(
    schema: JsonSchemaValue, reviewed_times: tuple[str, ...]
) -> None:
    definitions = schema["$defs"]
    ordinary = definitions["TimelineEventWithCitationIds"]
    ordinary["properties"]["event_type"] = {
        "type": "string",
        "enum": [kind for kind in definitions["EventType"]["enum"] if kind != "supplier_cancelled"],
    }
    definitions["ReviewedSupplierCancellation"] = _reviewed_timeline_variant(
        definitions, ordinary, "supplier_cancelled", "occurred", reviewed_times
    )
    schema["properties"]["timeline"]["items"] = {
        "anyOf": [
            {"$ref": "#/$defs/TimelineEventWithCitationIds"},
            {"$ref": "#/$defs/ReviewedSupplierCancellation"},
        ]
    }


def restrict_reviewed_offer(schema: JsonSchemaValue, offer: OfferQuoteRule) -> None:
    definitions = schema["$defs"]
    ordinary = definitions["TimelineEventWithCitationIds"]
    reviewed = _reviewed_timeline_variant(
        definitions, ordinary, "alternative_offered", "sent", (offer["sent_at"],)
    )
    reviewed["properties"]["evidence"].update(
        minItems=2,
        maxItems=2,
        items={"type": "string", "enum": [offer["sent_citation"], offer["offer_citation"]]},
    )
    ordinary["properties"]["event_type"] = {
        "type": "string",
        "enum": [
            kind for kind in definitions["EventType"]["enum"] if kind != "alternative_offered"
        ],
    }
    definitions["ReviewedV01Offer"] = reviewed
    schema["properties"]["timeline"]["items"] = {
        "anyOf": [
            {"$ref": "#/$defs/TimelineEventWithCitationIds"},
            {"$ref": "#/$defs/ReviewedV01Offer"},
        ]
    }


def model_choices(
    snapshot: AnalysisInput,
    approved_source_ids: tuple[str, ...],
    *,
    reply_already_included: bool = False,
    use_citation_ids: bool = False,
    allowed_requirements: tuple[RequirementKey, ...] | None = None,
    reviewed_cancellation_times: tuple[str, ...] = (),
) -> ModelChoices:
    # The per-case allowlist comes from backend settings. Catalogue text cannot add to it.
    approved = set(approved_source_ids)
    eligible = tuple(
        source
        for source in snapshot.catalog
        if not source.included and source.source_id in approved
    )
    locators_by_action: dict[str, list[str]] = {}
    for source in eligible:
        locators_by_action.setdefault(_LOOKUP_ACTION[source.kind], []).append(source.locator)

    # Literal members are values derived from the immutable snapshot.
    literal = cast(Any, Literal)
    follow_up: Any = _NoFollowUp | _AskUser
    for action, locators in locators_by_action.items():
        lookup = create_model(
            action.title().replace("_", ""),
            __base__=_ClosedModel,
            action=(literal[action], ...),
            locator=(literal[tuple(locators)], ...),
            requirements=(list[RequirementKey], Field(min_length=1)),
        )
        follow_up |= lookup
    output_fields: dict[str, Any] = {"follow_up": (follow_up, ...)}
    fact_answer: type[BaseModel] = _FactAnswer
    citations = CitationCatalogue.from_snapshot(snapshot) if use_citation_ids else None
    if citations is not None:
        evidence_field = Field(max_length=0)
        if citations.spans:
            evidence_field = Field(
                json_schema_extra={"items": {"type": "string", "enum": list(citations.spans)}}
            )
        ids: Any = Annotated[list[str], evidence_field]
        fact_answer = create_model("FactWithCitationIds", __base__=_FactAnswer, evidence=(ids, ...))
        timeline_event = create_model(
            "TimelineEventWithCitationIds", __base__=TimelineEvent, evidence=(ids, ...)
        )
        output_fields["timeline"] = (list[timeline_event], ...)
    fact_fields: dict[str, Any] = {key: (fact_answer, ...) for key in snapshot.requested_fact_keys}
    facts = create_model("RequestedFacts", __base__=_ClosedModel, **fact_fields)
    output = create_model(
        "AssemblyWithChoices",
        __base__=AssemblyOutput,
        facts=(facts, ...),
        **output_fields,
    )
    wire_schema = output.model_json_schema()
    if reply_already_included:
        wire_schema["$defs"]["RequirementKey"]["enum"].remove("original_acceptance_reply")
    if allowed_requirements is not None:
        requirement_schema = wire_schema["$defs"]["RequirementKey"]
        permitted = [key for key in requirement_schema["enum"] if key in allowed_requirements]
        if permitted:
            requirement_schema["enum"] = permitted
        else:
            wire_schema["properties"]["follow_up"] = {"$ref": "#/$defs/_NoFollowUp"}
    # Express singleton choices in the same enum form as multiple choices.
    for definition in wire_schema["$defs"].values():
        for property_schema in definition.get("properties", {}).values():
            if "const" in property_schema:
                property_schema["enum"] = [property_schema.pop("const")]
    if citations is not None and reviewed_cancellation_times:
        _restrict_supplier_cancellation_times(wire_schema, reviewed_cancellation_times)

    lookup_source_ids = tuple(source.source_id for source in eligible)
    visible = set(lookup_source_ids)
    filtered = snapshot.model_dump(mode="json")
    filtered["catalog"] = [
        entry for entry in filtered["catalog"] if entry["included"] or entry["source_id"] in visible
    ]
    if citations is not None:
        for entry in filtered["catalog"]:
            if entry["included"]:
                del entry["text"]
                entry["citation_lines"] = citations.lines[entry["source_id"]]
    return ModelChoices(
        output,
        wire_schema,
        canonical_json(filtered),
        lookup_source_ids,
        snapshot.requested_fact_keys,
        reply_already_included,
        citations,
    )
