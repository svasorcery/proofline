"""Check questions and reviewed refund evidence in the isolated Groq test."""

from dataclasses import replace

from proofline.domain.assembly import AssemblyOutput, EventTime, EventType
from proofline.domain.case_evidence import (
    WITHDRAWAL_PROOF_REQUIREMENTS,
    ReviewedRefund,
    original_withdrawal_receipt,
    scoped_requirements,
)
from proofline.domain.required_events import RequiredEvent
from proofline.domain.sources import SourceSnapshot, ValidationContext
from proofline.validation.required_events import check_required_events
from proofline.validation.result import FieldPath, ValidationIssue, ValidationResult


def _unique_offset(text: str, fragment: str) -> int:
    start = text.find(fragment)
    if start < 0 or text.find(fragment, start + 1) >= 0:
        return -1
    return start


def _covers(
    result: ValidationResult, path: FieldPath, source: SourceSnapshot, fragment: str
) -> bool:
    start = _unique_offset(source.text, fragment)
    return start >= 0 and any(
        span.path[: len(path)] == path
        and span.source_id == source.source_id
        and span.source_version_id == source.version_id
        and span.source_sha256 == source.text_sha256
        and span.start_cp <= start
        and span.end_cp >= start + len(fragment)
        for span in result.fragments
    )


def _check_refund(
    output: AssemblyOutput,
    context: ValidationContext,
    result: ValidationResult,
    review: ReviewedRefund,
    *,
    assemble_reviewed_evidence: bool,
) -> ValidationResult:
    issues = list(result.issues)
    settled = [
        index
        for index, fact in enumerate(output.facts)
        if fact.key == "payment_outcome" and fact.value == "refund_settled"
    ]
    reviewed_sources = review.selected_sources(context.sources)
    if reviewed_sources is None:
        issues.extend(
            ValidationIssue("reviewed_refund_sources_unavailable", ("facts", index, "evidence"))
            for index in settled
        )
        issues.extend(
            ValidationIssue("reviewed_refund_sources_unavailable", ("timeline", index, "evidence"))
            for index, event in enumerate(output.timeline)
            if event.event_type == "refund_settled"
        )
        return replace(result, issues=tuple(issues))

    ledger, bank = reviewed_sources
    for index in settled:
        fact_path = ("facts", index, "evidence")
        credited = _covers(result, fact_path, bank, review.credit_fragment)
        same_trace = _covers(result, fact_path, ledger, review.initiation.fragment) and _covers(
            result, fact_path, bank, review.bank_trace_fragment
        )
        same_document = bank.locator == review.bank_locator and _covers(
            result, fact_path, ledger, review.document_pointer
        )
        if not credited or not (same_trace or same_document):
            issues.append(ValidationIssue("refund_settlement_link_missing", fact_path))

    operations: tuple[tuple[EventType, SourceSnapshot, str, str], ...] = (
        ("payment_captured", ledger, review.capture.at, review.capture.fragment),
        ("refund_initiated", ledger, review.initiation.at, review.initiation.fragment),
        ("refund_settled", bank, review.settlement.at, review.settlement.fragment),
    )
    marks = tuple(
        RequiredEvent(
            event_type=kind,
            time=EventTime(value=at, role="occurred"),
            source_id=source.source_id,
            source_version_id=source.version_id,
            text_sha256=source.text_sha256,
            fragment=fragment,
        )
        for kind, source, at, fragment in operations
    )
    by_kind = {mark.event_type: mark for mark in marks}
    sources = {source.source_id: source for source in reviewed_sources}
    settlement_confirmed = False
    for index, event in enumerate(output.timeline):
        mark = by_kind.get(event.event_type)
        if mark is None:
            continue
        event_path = ("timeline", index, "evidence")
        has_operation = event.time == mark.time and _covers(
            result, event_path, sources[mark.source_id], mark.fragment
        )
        if not has_operation or (
            event.event_type == "refund_settled"
            and not _covers(result, event_path, bank, review.credit_fragment)
        ):
            issues.append(ValidationIssue("refund_event_evidence_missing", event_path))
        elif event.event_type == "refund_settled":
            settlement_confirmed = True
    if assemble_reviewed_evidence and settlement_confirmed:
        issues.extend(
            ValidationIssue("refund_fact_event_conflict", ("facts", index, "value"))
            for index, fact in enumerate(output.facts)
            if fact.key == "payment_outcome"
            and fact.value in {"refund_pending", "authorization_release"}
        )
    result = replace(result, issues=tuple(issues))
    if any(issue.code == "required_timeline_event_missing" for issue in issues):
        return result
    return check_required_events(output, context, result, marks)


def check_case_evidence(
    output: AssemblyOutput,
    context: ValidationContext,
    result: ValidationResult,
    refund: ReviewedRefund | None,
    *,
    reply_already_included: bool = False,
    assemble_reviewed_evidence: bool = False,
) -> ValidationResult:
    allowed = scoped_requirements(
        context.requested_fact_keys, reply_already_included=reply_already_included
    )
    issues = tuple(
        ValidationIssue(
            "follow_up_already_covered"
            if reply_already_included and requirement == "original_acceptance_reply"
            else "follow_up_out_of_scope",
            ("follow_up", "requirements", index),
        )
        for index, requirement in enumerate(output.follow_up.requirements)
        if requirement not in allowed
    )
    result = replace(result, issues=result.issues + issues)
    if refund is not None and "payment_outcome" in context.requested_fact_keys:
        result = _check_refund(
            output, context, result, refund, assemble_reviewed_evidence=assemble_reviewed_evidence
        )
    return result


def check_missing_withdrawal_proof(
    output: AssemblyOutput, result: ValidationResult
) -> ValidationResult:
    issues = list(result.issues)
    issues.extend(
        ValidationIssue("withdrawal_timing_without_original", ("facts", index, "value"))
        for index, fact in enumerate(output.facts)
        if fact.key == "withdrawal_timing" and fact.value != "unconfirmed"
    )
    issues.extend(
        ValidationIssue("withdrawal_timing_without_original", ("timeline", index, "evidence"))
        for index, event in enumerate(output.timeline)
        if event.event_type == "customer_withdrawal"
    )
    if output.follow_up.action != "ask_user" or not set(WITHDRAWAL_PROOF_REQUIREMENTS).issubset(
        output.follow_up.requirements
    ):
        issues.append(ValidationIssue("withdrawal_proof_request_incomplete", ("follow_up",)))
    return replace(result, issues=tuple(issues))


def check_withdrawal_refusal_text(
    output: AssemblyOutput,
    context: ValidationContext,
    result: ValidationResult,
    required: tuple[RequiredEvent, ...],
) -> ValidationResult:
    original = original_withdrawal_receipt(required)
    if original is None:
        return result
    mark, _, refusal = original
    letter = next(source for source in context.sources if source.source_id == mark.source_id)
    missing = tuple(
        ValidationIssue("withdrawal_refusal_text_missing", ("facts", index, "evidence"))
        for index, fact in enumerate(output.facts)
        if fact.key == "withdrawal_timing"
        and fact.value == "before_supplier_cancel"
        and not _covers(result, ("facts", index, "evidence"), letter, refusal)
    )
    return replace(result, issues=result.issues + missing)
