"""Check event citations against the reviewed reply in the isolated test profile."""

from proofline.domain.assembly import AssemblyOutput, EventType
from proofline.domain.inputs import AnalysisInput
from proofline.domain.present_confirmations import IncludedAcceptanceReply
from proofline.validation.result import IssueCode, ValidationIssue, ValidationResult


def check_reply_events(
    output: AssemblyOutput,
    snapshot: AnalysisInput,
    result: ValidationResult,
    binding: IncludedAcceptanceReply,
) -> ValidationResult:
    marks = binding.event_evidence
    if marks is None or not binding.matches(snapshot):
        return result
    source = next(s for s in snapshot.catalog if s.source_id == binding.source_id)
    source_text = source.text
    if source_text is None:
        return result
    for anchor in (marks.sent_header, marks.reply_text, marks.receipt_record):
        start = source_text.find(anchor)
        if start < 0 or source_text.find(anchor, start + 1) >= 0:
            return result

    required: dict[EventType, tuple[tuple[str, IssueCode], ...]] = {
        "customer_replied": (
            (marks.sent_header, "send_header_missing_from_send"),
            (marks.reply_text, "reply_text_missing_from_send"),
        ),
        "customer_message_received": (
            (marks.receipt_record, "receipt_record_missing_from_receipt"),
        ),
    }
    issues = list(result.issues)
    for index, event in enumerate(output.timeline):
        anchors = required.get(event.event_type)
        if anchors is None:
            continue
        path = ("timeline", index, "evidence")
        fragments = [
            span.fragment
            for span in result.fragments
            if span.path[:3] == path and span.source_id == binding.source_id
        ]
        for anchor, code in anchors:
            if not any(anchor in fragment for fragment in fragments):
                issues.append(ValidationIssue(code, path))
    return ValidationResult(issues=tuple(issues), fragments=result.fragments)
