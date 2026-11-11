"""Validate evidence and consistency without inferring facts or approving a case."""

from datetime import datetime

from proofline.domain.assembly import (
    AssemblyOutput,
    EventType,
    FactKey,
    FactValue,
    FollowUp,
    FollowUpAction,
    TimeRole,
)
from proofline.domain.sources import SourceKind, ValidationContext
from proofline.validation.evidence import resolve_evidence
from proofline.validation.result import ResolvedFragment, ValidationIssue, ValidationResult
from proofline.validation.timestamps import has_literal_timestamp, parse_instant

_FACT_VALUES: dict[FactKey, frozenset[FactValue]] = {
    "alternative_acceptance": frozenset(
        {"confirmed", "conditional", "not_accepted", "unconfirmed"}
    ),
    "payment_outcome": frozenset(
        {"refund_settled", "refund_pending", "authorization_release", "unconfirmed"}
    ),
    "withdrawal_timing": frozenset(
        {"before_supplier_cancel", "after_supplier_cancel", "unconfirmed"}
    ),
    "cancellation_status": frozenset({"confirmed", "unconfirmed"}),
}
_EVENT_ROLES: dict[EventType, TimeRole] = {
    "alternative_offered": "sent",
    "customer_replied": "sent",
    "customer_message_received": "received",
    "alternative_accepted": "received",
    "customer_withdrawal": "received",
    "supplier_cancelled": "occurred",
    "payment_captured": "occurred",
    "refund_initiated": "occurred",
    "refund_settled": "occurred",
    "authorization_released": "occurred",
}
_LOOKUP_KINDS: dict[FollowUpAction, SourceKind] = {
    "read_source": "document",
    "query_provider": "provider_log",
    "query_payment": "payment_ledger",
}


def _follow_up_issues(follow_up: FollowUp, context: ValidationContext) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(set(follow_up.requirements)) != len(follow_up.requirements):
        issues.append(ValidationIssue("duplicate_requirements", ("follow_up", "requirements")))
    if follow_up.action == "none":
        if follow_up.locator is not None or follow_up.requirements:
            issues.append(ValidationIssue("unexpected_followup_payload", ("follow_up",)))
        return issues
    if not follow_up.requirements:
        issues.append(ValidationIssue("empty_requirements", ("follow_up", "requirements")))
    if follow_up.action == "ask_user":
        if follow_up.locator is not None:
            issues.append(ValidationIssue("ask_user_has_locator", ("follow_up", "locator")))
        return issues
    reference = next((s for s in context.catalog if s.locator == follow_up.locator), None)
    if reference is None:
        issues.append(ValidationIssue("locator_not_authorized", ("follow_up", "locator")))
    elif reference.kind != _LOOKUP_KINDS[follow_up.action]:
        issues.append(ValidationIssue("wrong_lookup_kind", ("follow_up", "action")))
    if any(source.locator == follow_up.locator for source in context.sources):
        issues.append(ValidationIssue("repeated_snapshot_lookup", ("follow_up", "locator")))
    return issues


def validate_assembly(output: AssemblyOutput, context: ValidationContext) -> ValidationResult:
    """Return blocking issues and literal spans, leaving semantic review to the operator."""
    output = AssemblyOutput.model_validate(output)
    context = ValidationContext.model_validate(context)
    issues: list[ValidationIssue] = []
    fragments: list[ResolvedFragment] = []
    sources = {source.source_id: source for source in context.sources}
    if output.case_id != context.case_id:
        issues.append(ValidationIssue("case_id_mismatch", ("case_id",)))
    keys = [fact.key for fact in output.facts]
    if len(keys) != len(set(keys)) or set(keys) != set(context.requested_fact_keys):
        issues.append(ValidationIssue("fact_keys_mismatch", ("facts",)))
    for index, fact in enumerate(output.facts):
        if fact.value not in _FACT_VALUES[fact.key]:
            issues.append(ValidationIssue("invalid_fact_value", ("facts", index, "value")))
        if fact.value != "unconfirmed" and not fact.evidence:
            issues.append(
                ValidationIssue("known_fact_without_evidence", ("facts", index, "evidence"))
            )
        resolved = resolve_evidence(fact.evidence, sources, ("facts", index, "evidence"))
        issues.extend(resolved.issues)
        fragments.extend(resolved.fragments)

    moments: list[datetime] = []
    event_keys: set[tuple[EventType, datetime, TimeRole]] = set()
    for index, event in enumerate(output.timeline):
        path = ("timeline", index)
        if event.time.role != _EVENT_ROLES[event.event_type]:
            issues.append(ValidationIssue("wrong_time_role", (*path, "time", "role")))
        resolved = resolve_evidence(event.evidence, sources, (*path, "evidence"))
        issues.extend(resolved.issues)
        fragments.extend(resolved.fragments)
        if not has_literal_timestamp(event.time.value, resolved.fragments, sources):
            issues.append(ValidationIssue("time_not_in_fragments", (*path, "time", "value")))
        try:
            moment = parse_instant(event.time.value)
        except (ValueError, OverflowError):
            issues.append(ValidationIssue("invalid_or_naive_time", (*path, "time", "value")))
            continue
        if moments and moment < moments[-1]:
            issues.append(ValidationIssue("timeline_not_sorted", (*path, "time", "value")))
        moments.append(moment)
        key = (event.event_type, moment, event.time.role)
        if key in event_keys:
            issues.append(ValidationIssue("duplicate_events", path))
        event_keys.add(key)

    acceptance = next((f.value for f in output.facts if f.key == "alternative_acceptance"), None)
    if acceptance != "confirmed":
        for index, event in enumerate(output.timeline):
            if event.event_type == "alternative_accepted":
                issues.append(
                    ValidationIssue("acceptance_fact_event_conflict", ("timeline", index))
                )
    issues.extend(_follow_up_issues(output.follow_up, context))
    if output.readiness == "ready_for_rules" and (
        output.follow_up.action != "none"
        or any(fact.value in {"unconfirmed", "conditional"} for fact in output.facts)
    ):
        issues.append(ValidationIssue("premature_readiness", ("readiness",)))
    if output.readiness == "request_evidence" and output.follow_up.action == "none":
        issues.append(ValidationIssue("missing_followup", ("follow_up", "action")))
    return ValidationResult(issues=tuple(issues), fragments=tuple(fragments))
