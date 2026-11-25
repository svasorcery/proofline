"""Check follow-up scope and a reviewed refund on existing cards, without live access."""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from proofline.domain.assembly import AssemblyOutput, FactKey, RequirementKey
from proofline.domain.inputs import AnalysisInput
from proofline.domain.sources import SourceSnapshot
from proofline.validation.assembly import validate_assembly
from proofline.validation.result import ResolvedFragment

ARCHIVES = Path(__file__).resolve().parents[1] / "evals" / "live"
ACCEPTED = {
    "V01": (18, "ablation-V01"),
    "V02": (15, "full-V02"),
    "V03": (19, "full-V03"),
    "V04": (12, "full-V04"),
    "V05": (20, "full-V05"),
    "V06": (23, "full-V06"),
}
FOLLOW_UP_TOPICS: dict[RequirementKey, FactKey] = {
    "original_customer_message": "withdrawal_timing",
    "delivery_receipt": "withdrawal_timing",
    "quiet_room": "alternative_acceptance",
    "courtyard_windows": "alternative_acceptance",
    "original_acceptance_reply": "alternative_acceptance",
    "bank_settlement_document": "payment_outcome",
}
LEDGER = "a62eb31f18014f2e"
BANK = "649b9972ad454b5e"
BANK_LOCATOR = "3088e5c39f854cf6"
# Reviewed text hashes for the two V03 sources, independent of their database version IDs.
SOURCE_HASHES = {
    LEDGER: "cd209f9037d3de0715879b26bb1525b8d9ebd5ff3f74afca5170fb19b578a1bf",
    BANK: "13f4ae93c69e1fdfbdb95bd460911c8270f65a524c959333b90d6c5dd2a63428",
}
SUBMIT_TRACE = (LEDGER, "запрос передан банку; трасса 189a5d15e84a4adb")
BANK_TRACE = (BANK, "Трасса: 189a5d15e84a4adb")
DOCUMENT_LINK = (LEDGER, f"Для сверки банк приложил документ {BANK_LOCATOR}")
CREDIT = (BANK, "Кредитование карты получателя по указанной возвратной трассе завершено.")
CAPTURE_TIME = "2026-07-03T14:02:00+03:00"
INITIATION_TIME = "2026-07-05T10:15:00+03:00"
CREDIT_TIME = "2026-07-06T17:42:00+03:00"
REFUND_EVENTS = (
    ("payment_captured", CAPTURE_TIME, ((LEDGER, f"{CAPTURE_TIME} | capture |"),)),
    ("refund_initiated", INITIATION_TIME, ((LEDGER, f"{INITIATION_TIME} | return.submit |"),)),
    (
        "refund_settled",
        CREDIT_TIME,
        ((BANK, f"Дата проводки по карте получателя: {CREDIT_TIME}"), CREDIT),
    ),
)


@dataclass(frozen=True)
class ArchivedCard:
    snapshot: AnalysisInput
    body: dict


def archived(version: int, condition: str) -> ArchivedCard:
    (archive,) = ARCHIVES.glob(f"v{version}-*")
    folder = archive / condition
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    card = json.loads((folder / "proposal.json").read_text(encoding="utf-8"))
    assert run["snapshot"] == card["snapshot"]
    return ArchivedCard(
        AnalysisInput.model_validate_json(json.dumps(run["snapshot"])),
        card["body"],
    )


def covers(
    anchor: tuple[str, str],
    spans: list[ResolvedFragment],
    sources: dict[str, SourceSnapshot],
) -> bool:
    source_id, fragment = anchor
    source = sources[source_id]
    start = source.text.find(fragment)
    return (
        start >= 0
        and source.text.find(fragment, start + 1) < 0
        and any(
            span.source_id == source_id
            and span.source_version_id == source.version_id
            and span.source_sha256 == source.text_sha256
            and span.start_cp <= start
            and span.end_cp >= start + len(fragment)
            for span in spans
        )
    )


def check_card(snapshot: AnalysisInput, body: dict) -> tuple[list[str], list[str]]:
    output = AssemblyOutput.model_validate(body)
    context = snapshot.to_validation_context()
    validated = validate_assembly(output, context)
    base_issues = [issue.code for issue in validated.issues]
    issues = [
        f"follow_up_out_of_scope:{key}"
        for key in output.follow_up.requirements
        if FOLLOW_UP_TOPICS[key] not in context.requested_fact_keys
    ]
    if snapshot.case_id != "V03" or "payment_outcome" not in context.requested_fact_keys:
        return base_issues, issues

    settlement_rows = [
        index
        for index, fact in enumerate(output.facts)
        if (fact.key, fact.value) == ("payment_outcome", "refund_settled")
    ]
    sources = {source.source_id: source for source in context.sources}
    if any(
        source_id not in sources or sources[source_id].text_sha256 != expected
        for source_id, expected in SOURCE_HASHES.items()
    ):
        if settlement_rows:
            issues.append("reviewed_refund_sources_unavailable")
        return base_issues, issues

    def evidence_at(field: str, index: int) -> list[ResolvedFragment]:
        path = (field, index, "evidence")
        return [span for span in validated.fragments if span.path[:3] == path]

    for index in settlement_rows:
        spans = evidence_at("facts", index)
        credited = covers(CREDIT, spans, sources)
        shared_trace = covers(SUBMIT_TRACE, spans, sources) and covers(BANK_TRACE, spans, sources)
        document_pointer = sources[BANK].locator == BANK_LOCATOR and covers(
            DOCUMENT_LINK, spans, sources
        )
        if not credited or not (shared_trace or document_pointer):
            issues.append("refund_settlement_link_missing")

    timeline = {
        (event.event_type, event.time.role, event.time.value): index
        for index, event in enumerate(output.timeline)
    }
    for event_type, time, anchors in REFUND_EVENTS:
        index = timeline.get((event_type, "occurred", time))
        if index is None:
            issues.append(f"refund_event_missing:{event_type}")
        elif not all(covers(anchor, evidence_at("timeline", index), sources) for anchor in anchors):
            issues.append(f"refund_event_evidence_missing:{event_type}")
    return base_issues, issues


def main() -> None:
    results = {}

    def verify(label, snapshot, body, expected, *, base=()):
        base_issues, issues = check_card(snapshot, body)
        assert base_issues == list(base), (label, base_issues)
        assert issues == list(expected), (label, issues)
        results[label] = {"current_issues": base_issues, "prototype_issues": issues}

    accepted = {case: archived(*receipt) for case, receipt in ACCEPTED.items()}
    for case, card in accepted.items():
        verify(f"accepted-{case}", card.snapshot, card.body, ())

    v02 = archived(25, "full-V02")
    verify(
        "v25-V02",
        v02.snapshot,
        v02.body,
        ("follow_up_out_of_scope:quiet_room", "follow_up_out_of_scope:courtyard_windows"),
    )
    in_scope = deepcopy(v02.body)
    in_scope["follow_up"]["requirements"] = ["original_customer_message", "delivery_receipt"]
    verify("v25-V02-in-scope-questions", v02.snapshot, in_scope, ())
    v03 = archived(25, "full-V03")
    missing_timeline = (
        "refund_event_missing:payment_captured",
        "refund_event_missing:refund_initiated",
        "refund_event_missing:refund_settled",
    )
    verify("v25-V03", v03.snapshot, v03.body, ("refund_settlement_link_missing", *missing_timeline))

    with_trace = deepcopy(v03.body)
    bank_quotes = next(e for e in with_trace["facts"][0]["evidence"] if e["source_id"] == BANK)
    bank_quotes["fragments"].append(BANK_TRACE[1])
    verify("v25-V03-trace-only", v03.snapshot, with_trace, missing_timeline)

    with_timeline = deepcopy(v03.body)
    with_timeline["timeline"] = accepted["V03"].body["timeline"]
    verify(
        "v25-V03-timeline-only", v03.snapshot, with_timeline, ("refund_settlement_link_missing",)
    )
    with_trace["timeline"] = with_timeline["timeline"]
    verify("v25-V03-linked-and-complete", v03.snapshot, with_trace, ())

    with_document_link = deepcopy(accepted["V03"].body)
    bank_quotes = next(
        e for e in with_document_link["facts"][0]["evidence"] if e["source_id"] == BANK
    )
    bank_quotes["fragments"].remove(BANK_TRACE[1])
    verify("accepted-V03-with-document-link-only", v03.snapshot, with_document_link, ())
    wrong_locator = v03.snapshot.model_dump(mode="json")
    bank_source = next(s for s in wrong_locator["catalog"] if s["source_id"] == BANK)
    bank_source["locator"] = "prototype-different-document"
    verify(
        "V03-document-link-to-another-locator",
        AnalysisInput.model_validate_json(json.dumps(wrong_locator)),
        with_document_link,
        ("refund_settlement_link_missing",),
    )

    wrong_capture = deepcopy(accepted["V03"].body)
    wrong_capture["timeline"][0]["evidence"] = [
        {"source_id": "7fe04ac171704d90", "fragments": [f"Операция исполнена: {CAPTURE_TIME}"]}
    ]
    verify(
        "V03-capture-without-reviewed-operation",
        v03.snapshot,
        wrong_capture,
        ("refund_event_evidence_missing:payment_captured",),
    )

    changed = v03.snapshot.model_dump(mode="json")
    bank_source = next(s for s in changed["catalog"] if s["source_id"] == BANK)
    bank_source["text"] += "\nНовая версия."
    bank_source["version_id"] = "prototype-new-bank-version"
    bank_source["text_sha256"] = sha256(bank_source["text"].encode()).hexdigest()
    refreshed = AnalysisInput.model_validate_json(json.dumps(changed))
    verify(
        "V03-updated-bank-source",
        refreshed,
        accepted["V03"].body,
        ("reviewed_refund_sources_unavailable",),
    )

    omitted = v03.snapshot.model_dump(mode="json")
    bank_source = next(s for s in omitted["catalog"] if s["source_id"] == BANK)
    bank_source.update(included=False, text=None, version_id=None, text_sha256=None)
    partial_snapshot = AnalysisInput.model_validate_json(json.dumps(omitted))
    waiting_for_bank = {
        "case_id": "V03",
        "facts": [{"key": "payment_outcome", "value": "unconfirmed", "evidence": []}],
        "timeline": [],
        "readiness": "request_evidence",
        "follow_up": {
            "action": "ask_user",
            "locator": None,
            "requirements": ["bank_settlement_document"],
        },
    }
    verify("V03-bank-missing-and-confirmation-requested", partial_snapshot, waiting_for_bank, ())
    print(json.dumps({"new_model_calls": 0, "checks": results}, indent=2))


if __name__ == "__main__":
    main()
