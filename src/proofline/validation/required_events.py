"""Validate chronology against reviewed marks in the selected case snapshot."""

from dataclasses import replace

from proofline.domain.assembly import AssemblyOutput
from proofline.domain.required_events import RequiredEvent
from proofline.domain.sources import ValidationContext
from proofline.validation.result import IssueCode, ValidationIssue, ValidationResult
from proofline.validation.timestamps import literal_timestamp_starts

_NOTIFICATION_SENT = "Уведомление отправлено клиенту: "


def _notification_times(context: ValidationContext) -> dict[str, set[tuple[str, int]]]:
    times: dict[str, set[tuple[str, int]]] = {}
    for source in context.sources:
        if source.kind != "document":
            continue
        start = 0
        for line in source.text.split("\n"):
            if line.startswith(_NOTIFICATION_SENT):
                value = line[len(_NOTIFICATION_SENT) :].strip()
                if value:
                    offset = start + line.index(value, len(_NOTIFICATION_SENT))
                    times.setdefault(value, set()).add((source.source_id, offset))
            start += len(line) + 1
    return times


def check_required_events(
    output: AssemblyOutput,
    context: ValidationContext,
    result: ValidationResult,
    required: tuple[RequiredEvent, ...],
    *,
    check_capture: bool = False,
) -> ValidationResult:
    if not required:
        return result
    issues = list(result.issues)
    timeline = {(event.event_type, event.time.role, event.time.value) for event in output.timeline}
    if any(mark.identity not in timeline for mark in required):
        issues.append(ValidationIssue("required_timeline_event_missing", ("timeline",)))
    source_texts = {source.source_id: source.text for source in context.sources}
    cancellations = {
        mark.time.value
        for mark in required
        if mark.event_type == "supplier_cancelled" and mark.time.role == "occurred"
    }
    notifications = (
        _notification_times(context) if context.case_id == "V05" and cancellations else {}
    )
    receipts: dict[str, set[tuple[str, int]]] = {}
    for mark in required:
        if mark.time.role != "received":
            continue
        offset = source_texts[mark.source_id].index(mark.fragment) + mark.fragment.index(
            mark.time.value
        )
        receipts.setdefault(mark.time.value, set()).add((mark.source_id, offset))
    release_sources = {
        mark.source_id for mark in required if mark.event_type == "authorization_released"
    }
    openings: dict[str, set[tuple[str, int]]] = {}
    for source in context.sources:
        if source.kind != "payment_ledger" or source.source_id not in release_sources:
            continue
        start = 0
        for line in source.text.split("\n"):
            fields = line.split("|", 2)
            value = fields[0].strip()
            if len(fields) == 3 and value and fields[1].strip() == "auth.open":
                openings.setdefault(value, set()).add((source.source_id, start + line.index(value)))
            start += len(line) + 1
    for index, event in enumerate(output.timeline):
        code: IssueCode
        if event.event_type == "customer_replied" and event.time.role == "sent":
            unsupported = receipts.get(event.time.value)
            code = "receipt_time_used_as_sending"
        elif event.event_type == "authorization_released" and event.time.role == "occurred":
            unsupported = openings.get(event.time.value)
            code = "authorization_open_used_as_release"
        elif check_capture and event.event_type == "payment_captured":
            unsupported = openings.get(event.time.value)
            code = "authorization_open_used_as_capture"
        elif (
            event.event_type == "supplier_cancelled"
            and event.time.role == "occurred"
            and event.time.value not in cancellations
        ):
            unsupported = notifications.get(event.time.value)
            code = "notification_time_used_as_cancellation"
        else:
            continue
        if not unsupported:
            continue
        path = ("timeline", index, "evidence")
        cited_positions = {
            (span.source_id, offset)
            for span in result.fragments
            if span.path[:3] == path
            for offset in literal_timestamp_starts(
                event.time.value, span, source_texts[span.source_id]
            )
        }
        if cited_positions and cited_positions <= unsupported:
            issues.append(ValidationIssue(code, path))
    return replace(result, issues=tuple(issues))
