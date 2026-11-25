"""Resolve only IDs issued for literal evidence in an immutable input snapshot."""

from dataclasses import dataclass

from proofline.domain.assembly import Evidence
from proofline.domain.inputs import AnalysisInput, json_digest
from proofline.domain.sources import SourceSnapshot

PROVIDER_SCHEMA_VERSION = "assembly-citation-ids-v1"
COMPACT_PROVIDER_SCHEMA_VERSION = "assembly-citation-ids-compact-v1"
CANONICAL_TIMELINE_ORDER = "utc-v1"
REQUEST_PROFILE = "groq-chat_completions-v33-citation-ids-test-v1"
INSTRUCTION = (
    "\n\nThe examples show which text supports each fact or event. For the actual answer, "
    "each evidence array contains citation ID strings. Choose the IDs from the current "
    "case's citation_lines. A line with citation_id=null is context only. Use enough "
    "lines to support the fact or event and its time."
)
TIMELINE_EXAMPLE = """

TIMELINE WITH CITATION IDS
Build the timeline from explicit event records in the included sources. Include
the provider's completed cancellation when documented. Include the sending
and receipt of a support form when its records give both times. The facts may
leave the timing of a separate earlier message unconfirmed.

Illustrative lines with fictional IDs and times
q1a61752db468e230 2032-04-11T10:05:00+02:00 — provider cancelled the reservation.
q1196c7b8c38140a2 Form sent: 2032-04-11T11:01:00+02:00.
qcf5fd2e8724086d1 Form received: 2032-04-11T11:03:00+02:00.

The timeline for those illustrative lines is
[
  {"event_type":"supplier_cancelled","time":{"role":"occurred","value":"2032-04-11T10:05:00+02:00"},
   "evidence":["q1a61752db468e230"]},
  {"event_type":"customer_replied","time":{"role":"sent","value":"2032-04-11T11:01:00+02:00"},
   "evidence":["q1196c7b8c38140a2"]},
  {"event_type":"customer_message_received","time":{"role":"received","value":"2032-04-11T11:03:00+02:00"},
   "evidence":["qcf5fd2e8724086d1"]}
]
For the actual case, check the timeline from the real source lines before
returning the whole JSON object. Include a separate entry for each supported
event even when a fact already cites its line.
"""
LOOKUP_EXAMPLE = """

REQUESTING AN UNREAD ORIGINAL
If the included journal records receipt and an eligible unread document is
the matching original reply, keep acceptance unconfirmed until that reply
is read. Evidence for the receipt uses citation IDs from the included journal.
Ask to open the original with the exact locator from the eligible entry.
The following example uses a fictional locator
{"action":"read_source","locator":"example-letter-locator","requirements":["original_acceptance_reply"]}
Use the real case locator and only the actual citation IDs supplied in its
included sources. Build supported timeline events from the included records.
"""
LOOKUP_RECEIPT_GUIDANCE = (
    "\nA gateway receipt supports customer_message_received. Include customer_replied only "
    "when an included record shows the sending time of that message. When the original "
    "is unread and only the receipt is documented, show receipt without a sending entry."
)
REVIEWED_OFFER_GUIDANCE = (
    "\n\nFor the verified supplier offer, include an alternative_offered event at {sent_at} "
    "with role=sent. Use exactly two distinct evidence IDs in that event: "
    "{sent_citation} for the sending line and {offer_citation} for the offer text. "
    "The supplier's sending event is separate from receipt of the unread customer reply."
)
REVIEWED_SUPPORT_FORM_FOLLOW_UP = (
    " When both the original message and its delivery receipt are missing, use "
    "withdrawal_timing=unconfirmed and readiness=request_evidence. Use action=ask_user, "
    "locator=null, and exactly these requirements: original_customer_message and delivery_receipt."
)
REVIEWED_SUPPORT_FORM_GUIDANCE = (
    "\n\nFor the documented support form, include a separate customer_replied event at "
    "{sent_at} with role=sent and cite the sending line. Record customer_message_received at "
    "{received_at} with role=received and cite the receipt line. The form leaves the timing "
    "of the separate original message unconfirmed." + REVIEWED_SUPPORT_FORM_FOLLOW_UP
)
BANK_OPERATIONS_GUIDANCE = (
    "\nIn a payment ledger, auth.open places a hold and is background context. "
    "Include authorization_released once per documented auth.reverse, using that "
    "record's own timestamp and citation ID. Include other case events with their "
    "own source evidence."
)
CANCELLATION_NOTICE_GUIDANCE = (
    "\n\nCANCELLATION NOTICE\n"
    "For supplier_cancelled, use the completed cancellation operation from the supplier log. "
    "Use that operation's timestamp and citation ID. A later notice may report the same "
    "cancellation. Its sending time belongs to the notice. Keep one timeline event for "
    "each documented completed cancellation.\n"
    "Include each citation ID at most once per evidence list. A citation may appear "
    "separately in a fact and a timeline entry. For withdrawal_timing=before_supplier_cancel, "
    "include citation IDs for the refusal text, the Received by supplier line and the completed "
    "cancellation operation together in that fact's own evidence.\n"
)
REFUND_EVIDENCE_GUIDANCE = (
    "\n\nREFUND WITH CITATION IDS\n"
    "For a documented refund, include payment_captured, refund_initiated and refund_settled "
    "as three separate timeline entries with role=occurred. Cite the capture line, the "
    "return.submit line, and the bank posting date with completed credit, respectively. "
    "For payment_outcome=refund_settled, cite the return.submit trace, the matching trace "
    "in the bank statement, and its completed credit line. Use the bank posting time for "
    "settlement. The time the bank created the statement is metadata.\n"
)
REVIEWED_REFUND_FACT_GUIDANCE = (
    "\n\nClassify payment_outcome from the included payment records. Use refund_settled "
    "only when the included statement records completed credit for the same refund. The "
    "application attaches its checked submission, bank trace and credit citations."
)
REVIEWED_SOURCE_EVENT_GUIDANCE = (
    "\n\nThe application adds these events from reviewed source records: {event_types}. "
    "Leave those event types out of your timeline. Return any other supported events "
    "with citation IDs. Return an empty timeline if there are no other supported events."
)
COMPACT_REPLY_GUIDANCE = (
    "\n\nReturn exactly facts, readiness and follow_up. The application supplies the case ID "
    "and reconstructs the correspondence timeline from checked records. Classify the reply "
    "from its actual text and request any confirmations it still needs."
)


@dataclass(frozen=True, slots=True)
class CitationSpan:
    source: SourceSnapshot
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class CitationCatalogue:
    lines: dict[str, list[dict[str, str | None]]]
    spans: dict[str, CitationSpan]

    @classmethod
    def from_snapshot(cls, snapshot: AnalysisInput) -> "CitationCatalogue":
        scope = json_digest(snapshot.model_dump(mode="json"))
        lines: dict[str, list[dict[str, str | None]]] = {}
        spans: dict[str, CitationSpan] = {}
        for source in snapshot.to_validation_context().sources:
            rows: list[dict[str, str | None]] = []
            start = 0
            for line in source.text.split("\n"):
                end = start + len(line)
                citation_id = None
                if (
                    line.strip()
                    and source.text.find(line) == start
                    and source.text.find(line, start + 1) < 0
                ):
                    citation_id = "q" + json_digest([scope, source.source_id, start, end])[:16]
                    if citation_id in spans:
                        raise ValueError("Duplicate citation ID")
                    spans[citation_id] = CitationSpan(source, start, end)
                rows.append({"citation_id": citation_id, "text": line})
                start = end + 1
            lines[source.source_id] = rows
        return cls(lines, spans)

    def validate_ids(self, ids: list[str]) -> None:
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate citation ID in evidence")
        if any(citation_id not in self.spans for citation_id in ids):
            raise ValueError("Unknown citation ID")

    def resolve(self, ids: list[str]) -> list[Evidence]:
        self.validate_ids(ids)
        by_source: dict[str, list[str]] = {}
        for citation_id in ids:
            span = self.spans[citation_id]
            source = span.source
            by_source.setdefault(source.source_id, []).append(source.text[span.start : span.end])
        return [
            Evidence(source_id=source_id, fragments=fragments)
            for source_id, fragments in by_source.items()
        ]
