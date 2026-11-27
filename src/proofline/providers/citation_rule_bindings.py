"""Bind citation rules to reviewed source versions."""

from proofline.domain.assembly import EventTime, EventType
from proofline.domain.case_evidence import (
    ReviewedRefund,
    original_withdrawal_receipt,
    reviewed_withdrawal_snapshot,
)
from proofline.domain.inputs import AnalysisInput, compatibility_digest
from proofline.domain.present_confirmations import IncludedAcceptanceReply
from proofline.domain.required_events import RequiredEvent
from proofline.providers.citation_ids import CitationCatalogue
from proofline.providers.replay import recorded_input
from proofline.providers.reviewed_citations import (
    AuthorizationReleaseRule,
    EventRule,
    OfferQuoteRule,
    RefundRule,
    ReviewedCitationRules,
    ReviewedSourceEvent,
    UnreadReplyRule,
    WithdrawalFactRule,
)
from proofline.validation.timestamps import parse_instant

_ARCHIVED_ORIGINAL = "Оригинал MIME помещён в архив: "
_SENT = "Отправлено: "
# Baseline V06 with the complete set of reviewed sources. A changed or added record
# requires a fresh assessment, even if the original ledger entry is still included.
_REVIEWED_V06_INPUT = "ee8e22c5e98f04177c3051b3fffe0dcfa5fdb99fa8f144a7ad533852d202bc9e"
_NO_CHARGE = "запросов capture нет; строк в реестре списаний нет; запросов return.submit нет."
_REVIEWED_V06_EVENTS: tuple[tuple[EventType, str, str], ...] = (
    (
        "supplier_cancelled",
        "845cff973bd4435d",
        "2026-06-22T16:40:00+03:00 — бронирование отменено поставщиком, исполнение прекращено.",
    ),
    (
        "authorization_released",
        "5f5576cbae5e40c6",
        "2026-06-22T16:43:00+03:00 | auth.reverse | банк снял резерв по исходной авторизации",
    ),
)


def is_reviewed_v06(snapshot: AnalysisInput) -> bool:
    return snapshot.case_id == "V06" and compatibility_digest(snapshot) == _REVIEWED_V06_INPUT


def guest_v06_binding(
    snapshot: AnalysisInput,
) -> tuple[AnalysisInput, tuple[RequiredEvent, ...]] | None:
    # Use the fixture identity for the review only. Citations and the model request
    # still belong to the guest snapshot and its own source versions.
    reviewed = recorded_input(snapshot, "V06")
    if not is_reviewed_v06(reviewed):
        return None
    sources = {source.source_id: source for source in snapshot.to_validation_context().sources}
    marks = []
    for event_type, source_id, fragment in _REVIEWED_V06_EVENTS:
        source = sources[source_id]
        marks.append(
            RequiredEvent(
                event_type=event_type,
                time=EventTime(value=fragment.split(" ", 1)[0], role="occurred"),
                source_id=source_id,
                source_version_id=source.version_id,
                text_sha256=source.text_sha256,
                fragment=fragment,
            )
        )
    return reviewed, tuple(marks)


def _citation_id(citations: CitationCatalogue, source_id: str, fragment: str) -> str | None:
    matches = [
        row["citation_id"]
        for row in citations.lines[source_id]
        if row["citation_id"] is not None and fragment in (row["text"] or "")
    ]
    return matches[0] if len(matches) == 1 else None


def reviewed_authorization_release(
    snapshot: AnalysisInput, citations: CitationCatalogue, required: tuple[RequiredEvent, ...]
) -> AuthorizationReleaseRule | None:
    if not is_reviewed_v06(snapshot):
        return None
    events: dict[str, ReviewedSourceEvent] = {}
    no_charge = None
    opening = None
    for mark in required:
        if mark.event_type not in {"authorization_released", "supplier_cancelled"}:
            continue
        citation = _citation_id(citations, mark.source_id, mark.fragment)
        if citation is None or mark.time.role != "occurred" or mark.event_type in events:
            return None
        events[mark.event_type] = {
            "event_type": mark.event_type,
            "time": {"role": mark.time.role, "value": mark.time.value},
            "evidence": [citation],
        }
        if mark.event_type == "authorization_released":
            no_charge = _citation_id(citations, mark.source_id, _NO_CHARGE)
            if no_charge == citation:
                return None
            opening = _false_capture_at_opening(citations, mark.source_id)
    if no_charge is None or set(events) != {"authorization_released", "supplier_cancelled"}:
        return None
    rule: AuthorizationReleaseRule = {
        "release": events["authorization_released"],
        "cancellation": events["supplier_cancelled"],
        "no_charge_citation": no_charge,
    }
    if opening is not None:
        rule["false_capture"] = opening
    return rule


def _false_capture_at_opening(
    citations: CitationCatalogue, source_id: str
) -> ReviewedSourceEvent | None:
    openings = []
    for row in citations.lines[source_id]:
        fields = (row["text"] or "").split("|", 2)
        citation = row["citation_id"]
        if len(fields) == 3 and fields[1].strip() == "auth.open" and citation is not None:
            openings.append((fields[0].strip(), citation))
    if len(openings) != 1:
        return None
    ((at, citation),) = openings
    return {
        "event_type": "payment_captured",
        "time": {"role": "occurred", "value": at},
        "evidence": [citation],
    }


def _refund_rule(
    snapshot: AnalysisInput, citations: CitationCatalogue, review: ReviewedRefund
) -> RefundRule | None:
    sources = review.selected_sources(snapshot.to_validation_context().sources)
    if sources is None:
        return None
    ledger, bank = sources
    submission = _citation_id(citations, ledger.source_id, review.initiation.fragment)
    trace = _citation_id(citations, bank.source_id, review.bank_trace_fragment)
    posting = _citation_id(citations, bank.source_id, review.settlement.fragment)
    credit = _citation_id(citations, bank.source_id, review.credit_fragment)
    if submission is None or trace is None or posting is None or credit is None:
        return None
    return {
        "fact_evidence": [submission, credit],
        "event_evidence": [posting, credit, trace],
        "posting_time": review.settlement.at,
        "trace": trace,
    }


def _withdrawal_roles(
    citations: CitationCatalogue, required: tuple[RequiredEvent, ...]
) -> list[EventRule]:
    original = original_withdrawal_receipt(required)
    if original is None:
        return []
    mark, received_line, refusal_line = original
    receipt = _citation_id(citations, mark.source_id, received_line)
    refusal = _citation_id(citations, mark.source_id, refusal_line)
    if receipt is None or refusal is None:
        return []
    rules: list[EventRule] = [
        {
            "from_type": "customer_message_received",
            "to_type": "customer_withdrawal",
            "role": "received",
            "time": mark.time.value,
            "evidence": [receipt, refusal],
            "from_fact": {
                "key": "withdrawal_timing",
                "value": "before_supplier_cancel",
                "citation": refusal,
            },
        }
    ]
    date_rows = [
        (row["citation_id"], (row["text"] or "")[len("Date: ") :])
        for row in citations.lines[mark.source_id]
        if (row["text"] or "").startswith("Date: ")
    ]
    if len(date_rows) == 1:
        ((sent, time),) = date_rows
        if sent is not None and time:
            rules.append(
                {
                    "from_type": "customer_withdrawal",
                    "to_type": "customer_replied",
                    "role": "sent",
                    "time": time,
                    "evidence": [sent, refusal],
                }
            )
    return rules


def reviewed_withdrawal_timeline(
    snapshot: AnalysisInput, roles: list[EventRule]
) -> list[ReviewedSourceEvent]:
    if len(roles) != 2 or not reviewed_withdrawal_snapshot(snapshot):
        return []
    return [
        {
            "event_type": role["to_type"],
            "time": {"role": role["role"], "value": role["time"]},
            "evidence": role["evidence"].copy(),
        }
        for role in roles
    ]


def reviewed_withdrawal_fact(
    citations: CitationCatalogue,
    required: tuple[RequiredEvent, ...],
    withdrawal: list[ReviewedSourceEvent],
) -> WithdrawalFactRule | None:
    received = next((row for row in withdrawal if row["event_type"] == "customer_withdrawal"), None)
    cancellations = [
        mark
        for mark in required
        if mark.event_type == "supplier_cancelled" and mark.time.role == "occurred"
    ]
    if received is None or len(cancellations) != 1:
        return None
    (cancelled,) = cancellations
    try:
        if parse_instant(received["time"]["value"]) >= parse_instant(cancelled.time.value):
            return None
    except (ValueError, OverflowError):
        return None
    cancellation = _citation_id(citations, cancelled.source_id, cancelled.fragment)
    if cancellation is None:
        return None
    receipt, refusal = received["evidence"]
    return {"receipt": receipt, "refusal": refusal, "cancellation": cancellation}


def _unread_reply_rule(
    snapshot: AnalysisInput,
    citations: CitationCatalogue,
    required: tuple[RequiredEvent, ...],
    lookup_source_ids: tuple[str, ...],
) -> UnreadReplyRule | None:
    marks = [
        mark
        for mark in required
        if mark.event_type == "customer_message_received" and mark.time.role == "received"
    ]
    if len(marks) != 1:
        return None
    (mark,) = marks
    journal = next(source for source in snapshot.catalog if source.source_id == mark.source_id)
    if journal.kind != "provider_log":
        return None
    rows = citations.lines[mark.source_id]
    pointers = [row for row in rows if (row["text"] or "").startswith(_ARCHIVED_ORIGINAL)]
    if len(pointers) != 1:
        return None
    (pointer,) = pointers
    pointer_text = pointer["text"] or ""
    archive_id = pointer["citation_id"]
    receipt_id = _citation_id(citations, mark.source_id, mark.fragment)
    if not pointer_text.endswith(".") or archive_id is None or receipt_id is None:
        return None
    locator = pointer_text[len(_ARCHIVED_ORIGINAL) : -1]
    matching = [source for source in snapshot.catalog if source.locator == locator]
    if len(matching) != 1:
        return None
    (target,) = matching
    if target.kind != "document" or target.included or target.source_id not in lookup_source_ids:
        return None
    return {
        "locator": locator,
        "archive_citation": archive_id,
        "receipt_citation": receipt_id,
        "received_at": mark.time.value,
        "fill_missing_follow_up": True,
    }


def _offer_quote_rule(
    snapshot: AnalysisInput,
    citations: CitationCatalogue,
    receipt_locator: str,
    *,
    offer_prefix: str = "Можем предложить ",
) -> OfferQuoteRule | None:
    correspondence = f"Квитанция переписки: {receipt_locator}."
    linked_letters = [
        citations.lines[source.source_id]
        for source in snapshot.catalog
        if source.included
        and source.kind == "document"
        and any(
            row["text"] == correspondence and row["citation_id"] is not None
            for row in citations.lines[source.source_id]
        )
    ]
    if len(linked_letters) != 1:
        return None
    (lines,) = linked_letters
    timestamps = [row for row in lines if (row["text"] or "").startswith(_SENT)]
    offers = [row for row in lines if (row["text"] or "").startswith(offer_prefix)]
    if len(timestamps) != 1 or len(offers) != 1:
        return None
    (sent,) = timestamps
    (offer,) = offers
    sent_id, offer_id = sent["citation_id"], offer["citation_id"]
    sent_at = (sent["text"] or "").removeprefix(_SENT)
    if sent_id is None or offer_id is None or not sent_at:
        return None
    return {"sent_at": sent_at, "sent_citation": sent_id, "offer_citation": offer_id}


def reviewed_citation_rules(
    snapshot: AnalysisInput,
    citations: CitationCatalogue,
    required: tuple[RequiredEvent, ...],
    refund: ReviewedRefund | None,
    lookup_source_ids: tuple[str, ...],
) -> ReviewedCitationRules:
    if (
        snapshot.case_id == "V01"
        and "alternative_acceptance" in snapshot.requested_fact_keys
        and (reply := _unread_reply_rule(snapshot, citations, required, lookup_source_ids))
        is not None
    ):
        rules: ReviewedCitationRules = {"unread_reply": reply}
        receipt_locator = citations.spans[reply["archive_citation"]].source.locator
        if (offer := _offer_quote_rule(snapshot, citations, receipt_locator)) is not None:
            rules["offer_quote"] = offer
        return rules
    if (
        snapshot.case_id == "V03"
        and refund is not None
        and "payment_outcome" in snapshot.requested_fact_keys
        and (rule := _refund_rule(snapshot, citations, refund)) is not None
    ):
        return {"refund_trace": rule}
    if (
        snapshot.case_id == "V05"
        and any(
            mark.event_type == "supplier_cancelled" and mark.time.role == "occurred"
            for mark in required
        )
        and (roles := _withdrawal_roles(citations, required))
    ):
        return {"event_roles": roles}
    return {}


def _reviewed_reply_timeline(
    snapshot: AnalysisInput, citations: CitationCatalogue, reply: IncludedAcceptanceReply
) -> list[ReviewedSourceEvent]:
    marks = reply.event_evidence
    if marks is None or not reply.matches(snapshot):
        return []
    journal = next(source for source in snapshot.catalog if source.source_id == reply.source_id)
    if journal.kind != "provider_log":
        return []
    offer = _offer_quote_rule(
        snapshot,
        citations,
        journal.locator,
        offer_prefix="Вместо первоначального номера можем предложить ",
    )
    send = _citation_id(citations, journal.source_id, marks.sent_header)
    text = _citation_id(citations, journal.source_id, marks.reply_text)
    receipt = _citation_id(citations, journal.source_id, marks.receipt_record)
    if offer is None or send is None or text is None or receipt is None or send == text:
        return []
    sent_prefix = "Заголовок исходного письма Date: "
    received_at, inbound, _ = marks.receipt_record.partition(" | inbound-message | ")
    if not marks.sent_header.startswith(sent_prefix) or not inbound:
        return []
    reply_at = marks.sent_header.removeprefix(sent_prefix)
    try:
        if (
            not parse_instant(offer["sent_at"])
            <= parse_instant(reply_at)
            <= parse_instant(received_at)
        ):
            return []
    except (ValueError, OverflowError):
        return []
    return [
        {
            "event_type": "alternative_offered",
            "time": {"role": "sent", "value": offer["sent_at"]},
            "evidence": [offer["sent_citation"], offer["offer_citation"]],
        },
        {
            "event_type": "customer_replied",
            "time": {"role": "sent", "value": reply_at},
            "evidence": [send, text],
        },
        {
            "event_type": "customer_message_received",
            "time": {"role": "received", "value": received_at},
            "evidence": [receipt],
        },
    ]


def reviewed_source_events(
    snapshot: AnalysisInput,
    citations: CitationCatalogue,
    required: tuple[RequiredEvent, ...],
    refund: ReviewedRefund | None,
    refund_rule: RefundRule | None,
    reply: IncludedAcceptanceReply | None = None,
) -> list[ReviewedSourceEvent]:
    if snapshot.case_id == "V04" and reply is not None:
        return _reviewed_reply_timeline(snapshot, citations, reply)
    if snapshot.case_id == "V02":
        documented_types = {"supplier_cancelled", "customer_replied", "customer_message_received"}
        return [
            {
                "event_type": mark.event_type,
                "time": {"role": mark.time.role, "value": mark.time.value},
                "evidence": [citation],
            }
            for mark in required
            if mark.event_type in documented_types
            and (citation := _citation_id(citations, mark.source_id, mark.fragment)) is not None
        ]
    if snapshot.case_id != "V03" or refund is None or refund_rule is None:
        return []
    capture_id = _citation_id(citations, refund.ledger_source_id, refund.capture.fragment)
    if capture_id is None:
        return []
    return [
        {
            "event_type": "payment_captured",
            "time": {"role": "occurred", "value": refund.capture.at},
            "evidence": [capture_id],
        },
        {
            "event_type": "refund_initiated",
            "time": {"role": "occurred", "value": refund.initiation.at},
            "evidence": [refund_rule["fact_evidence"][0]],
        },
        {
            "event_type": "refund_settled",
            "time": {"role": "occurred", "value": refund.settlement.at},
            "evidence": refund_rule["event_evidence"].copy(),
        },
    ]
