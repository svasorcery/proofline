"""The model-facing wire format; semantic checks live in validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type FactKey = Literal[
    "alternative_acceptance", "payment_outcome", "withdrawal_timing", "cancellation_status"
]
type FactValue = Literal[
    "confirmed",
    "conditional",
    "not_accepted",
    "unconfirmed",
    "refund_settled",
    "refund_pending",
    "authorization_release",
    "before_supplier_cancel",
    "after_supplier_cancel",
]
type EventType = Literal[
    "alternative_offered",
    "customer_replied",
    "customer_message_received",
    "alternative_accepted",
    "customer_withdrawal",
    "supplier_cancelled",
    "payment_captured",
    "refund_initiated",
    "refund_settled",
    "authorization_released",
]
type TimeRole = Literal["sent", "received", "occurred"]
type Readiness = Literal["ready_for_rules", "request_evidence", "human_review"]
type FollowUpAction = Literal["none", "ask_user", "read_source", "query_provider", "query_payment"]
type RequirementKey = Literal[
    "original_customer_message",
    "delivery_receipt",
    "quiet_room",
    "courtyard_windows",
    "original_acceptance_reply",
    "bank_settlement_document",
]


class _OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class Evidence(_OutputModel):
    source_id: str
    fragments: list[str] = Field(min_length=1)


class Fact(_OutputModel):
    key: FactKey
    value: FactValue
    evidence: list[Evidence]


class EventTime(_OutputModel):
    # Keep the source spelling and offset. Parsing must not rewrite the wire value.
    value: str
    role: TimeRole


class TimelineEvent(_OutputModel):
    event_type: EventType
    time: EventTime
    evidence: list[Evidence]


class FollowUp(_OutputModel):
    action: FollowUpAction
    locator: str | None
    requirements: list[RequirementKey]


class AssemblyOutput(_OutputModel):
    case_id: str
    facts: list[Fact]
    timeline: list[TimelineEvent]
    readiness: Readiness
    follow_up: FollowUp
