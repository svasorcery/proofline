"""Reviewed evidence for the isolated citation ID test."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proofline.domain.assembly import FactKey, RequirementKey
from proofline.domain.inputs import AnalysisInput, compatibility_digest
from proofline.domain.required_events import RequiredEvent
from proofline.domain.sources import SourceSnapshot

WITHDRAWAL_PROOF_REQUIREMENTS: tuple[RequirementKey, ...] = (
    "original_customer_message",
    "delivery_receipt",
)
_REVIEWED_V02_INPUT = "1c9b1c8d94b2a5db05678f1bb6cf3425e43f1c959ca2fd0d3596283236720dc9"
_REVIEWED_V05_INPUT = "c2643ef37244305d3004f85c678633bfa55147d7afb4087180fda30dc0a242d2"

_REQUIREMENT_TOPICS: dict[RequirementKey, FactKey] = {
    "original_customer_message": "withdrawal_timing",
    "delivery_receipt": "withdrawal_timing",
    "quiet_room": "alternative_acceptance",
    "courtyard_windows": "alternative_acceptance",
    "original_acceptance_reply": "alternative_acceptance",
    "bank_settlement_document": "payment_outcome",
}


def reviewed_withdrawal_proof_missing(snapshot: AnalysisInput) -> bool:
    # This reviewed support form does not include the original refusal or its receipt.
    return snapshot.case_id == "V02" and compatibility_digest(snapshot) == _REVIEWED_V02_INPUT


def reviewed_withdrawal_snapshot(snapshot: AnalysisInput) -> bool:
    return snapshot.case_id == "V05" and compatibility_digest(snapshot) == _REVIEWED_V05_INPUT


def original_withdrawal_receipt(
    required: tuple[RequiredEvent, ...],
) -> tuple[RequiredEvent, str, str] | None:
    marks = [
        mark
        for mark in required
        if mark.event_type == "customer_withdrawal" and mark.time.role == "received"
    ]
    if len(marks) != 1:
        return None
    (mark,) = marks
    receipt, separator, refusal = mark.fragment.partition("\n")
    if not separator or receipt != f"Received by supplier: {mark.time.value}" or not refusal:
        return None
    return mark, receipt, refusal


def scoped_requirements(
    facts: tuple[FactKey, ...], *, reply_already_included: bool = False
) -> tuple[RequirementKey, ...]:
    return tuple(
        key
        for key, fact in _REQUIREMENT_TOPICS.items()
        if fact in facts and not (reply_already_included and key == "original_acceptance_reply")
    )


class ReviewedOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    at: str = Field(min_length=1)
    fragment: str = Field(min_length=1)


class ReviewedRefund(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    ledger_source_id: str = Field(min_length=1)
    ledger_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bank_source_id: str = Field(min_length=1)
    bank_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bank_locator: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    bank_trace_fragment: str = Field(min_length=1)
    credit_fragment: str = Field(min_length=1)
    document_pointer: str = Field(min_length=1)
    capture: ReviewedOperation
    initiation: ReviewedOperation
    settlement: ReviewedOperation

    def selected_sources(
        self, sources: tuple[SourceSnapshot, ...]
    ) -> tuple[SourceSnapshot, SourceSnapshot] | None:
        by_id = {source.source_id: source for source in sources}
        ledger = by_id.get(self.ledger_source_id)
        bank = by_id.get(self.bank_source_id)
        if (
            ledger is None
            or ledger.kind != "payment_ledger"
            or ledger.text_sha256 != self.ledger_text_sha256
            or bank is None
            or bank.kind != "document"
            or bank.text_sha256 != self.bank_text_sha256
        ):
            return None
        return ledger, bank

    @model_validator(mode="after")
    def validate_marks(self) -> Self:
        if self.ledger_source_id == self.bank_source_id:
            raise ValueError("The ledger and bank statement must be distinct")
        if any(op.at not in op.fragment for op in (self.capture, self.initiation, self.settlement)):
            raise ValueError("Each reviewed operation needs a fragment with its time")
        if not self.trace_id.strip() or any(
            self.trace_id not in text
            for text in (self.initiation.fragment, self.bank_trace_fragment)
        ):
            raise ValueError("The trace must occur in both reviewed fragments")
        if not self.bank_locator.strip() or self.bank_locator not in self.document_pointer:
            raise ValueError("The reviewed document pointer must refer to the bank statement")
        return self
