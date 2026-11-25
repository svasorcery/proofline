"""Regression tests on model answers saved before the source checks were added."""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from proofline.application.state import record_validation
from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput
from proofline.persistence.models import ProposalRevision
from proofline.providers.citation_ids import CitationCatalogue
from proofline.providers.factory import create_live_provider
from proofline.validation import validate_assembly

ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def recording():
    def read(case_id, version=53, *, enabled=True):
        (archive,) = (ROOT / "evals/live").glob(f"v{version}-*")
        folder = archive / f"full-{case_id}"
        options = _read(archive / "preflight.json")["bound_settings"]
        options["groq_test_assemble_reviewed_evidence"] = enabled
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://offline/unused",
            live_enabled=True,
            llm_api_key="offline",
            **options,
        )
        return settings, _read(folder / "run.json"), _read(folder / "request.json")

    return read


def _validation(settings, snapshot, body):
    proposal = ProposalRevision(id=uuid4(), snapshot=snapshot, body=body)
    return record_validation(Mock(spec=Session), proposal, settings)


@pytest.mark.parametrize(
    ("case_id", "issue"),
    [("V02", "withdrawal_timing_without_original"), ("V06", "authorization_open_used_as_capture")],
)
def test_validation_blocks_unsupported_claims_even_without_the_decoder(recording, case_id, issue):
    settings, run, _ = recording(case_id)
    (archive,) = (ROOT / "evals/live").glob("v53-*")
    body = _read(archive / f"full-{case_id}/proposal.json")["body"]
    validation = _validation(settings, run["snapshot"], body)
    assert validation.result == "blocked_for_approval"
    assert issue in {row["code"] for row in validation.issues}
    disabled, _, _ = recording(case_id, enabled=False)
    assert _validation(disabled, run["snapshot"], body).issues == []


@pytest.mark.parametrize("case_id", ["V02", "V05", "V06"])
def test_reconstructs_the_v53_card_using_the_checked_sources(recording, case_id):
    settings, run, request = recording(case_id)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.request == request
    assert prepared.decode_output is not None
    card = prepared.decode_output(run["raw_output"])
    facts = {fact.key: fact for fact in card.facts}
    event_types = [event.event_type for event in card.timeline]

    if case_id == "V02":
        assert facts["withdrawal_timing"].value == "unconfirmed"
        assert not facts["withdrawal_timing"].evidence
        assert card.readiness == "request_evidence"
        assert card.follow_up.action == "ask_user"
        assert card.follow_up.requirements == ["original_customer_message", "delivery_receipt"]
    elif case_id == "V05":
        assert facts["withdrawal_timing"].value == "before_supplier_cancel"
        assert event_types == ["customer_replied", "customer_withdrawal", "supplier_cancelled"]
        sent, received, _ = card.timeline
        assert sent.time.role == "sent" and received.time.role == "received"
        assert sent.evidence[0].fragments[0] == "Date: 2026-10-04T09:26:00+03:00"
        assert (
            received.evidence[0].fragments[0] == "Received by supplier: 2026-10-04T09:30:00+03:00"
        )
    else:
        assert event_types == ["supplier_cancelled", "authorization_released"]
        (evidence,) = facts["payment_outcome"].evidence
        ledger = next(source for source in snapshot.catalog if source.kind == "payment_ledger")
        assert ledger.text is not None and evidence.fragments == ledger.text.splitlines()[1:]
        assert card.follow_up.action == "none" and card.readiness == "ready_for_rules"
    assert not validate_assembly(card, snapshot.to_validation_context()).issues
    assert _validation(settings, run["snapshot"], card.model_dump(mode="json")).issues == []


@pytest.mark.parametrize("case_id", ["V02", "V05"])
def test_existing_proven_v51_cards_are_preserved(recording, case_id):
    settings, run, _ = recording(case_id, 51)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.decode_output is not None
    (archive,) = (ROOT / "evals/live").glob("v51-*")
    old = _read(archive / f"full-{case_id}/proposal.json")
    assert prepared.decode_output(run["raw_output"]).model_dump(mode="json") == old["body"]


def test_v54_v05_uses_the_same_letter_to_complete_the_fact(recording):
    settings, run, request = recording("V05", 54)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.request == request
    assert prepared.decode_output is not None
    (archive,) = (ROOT / "evals/live").glob("v54-*")
    expected = _read(archive / "full-V05/proposal.json")["body"]
    receipt = next(
        event for event in expected["timeline"] if event["event_type"] == "customer_withdrawal"
    )
    (letter,) = receipt["evidence"]
    fact = next(row for row in expected["facts"] if row["key"] == "withdrawal_timing")
    fact_evidence = next(row for row in fact["evidence"] if row["source_id"] == letter["source_id"])
    receipt_line, refusal = letter["fragments"]
    assert receipt_line in fact_evidence["fragments"] and refusal not in fact_evidence["fragments"]
    fact_evidence["fragments"].append(refusal)

    card = prepared.decode_output(run["raw_output"]).model_dump(mode="json")
    assert card == expected
    assert _validation(settings, run["snapshot"], card).issues == []


def test_v54_v05_blocks_the_fact_when_only_the_timeline_contains_the_refusal(recording):
    settings, run, _ = recording("V05", 54)
    (archive,) = (ROOT / "evals/live").glob("v54-*")
    card = _read(archive / "full-V05/proposal.json")["body"]

    checked = _validation(settings, run["snapshot"], card)
    assert checked.result == "blocked_for_approval"
    assert [issue["code"] for issue in checked.issues] == ["withdrawal_refusal_text_missing"]

    disabled, _, _ = recording("V05", 54, enabled=False)
    assert _validation(disabled, run["snapshot"], card).issues == []


@pytest.mark.parametrize(
    ("case_id", "message"), [("V02", "Unknown citation ID"), ("V05", "Duplicate citation ID")]
)
def test_rejects_invalid_citations_before_replacing_reviewed_claims(recording, case_id, message):
    settings, run, _ = recording(case_id, 54)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.decode_output is not None
    raw = json.loads(run["raw_output"])
    if case_id == "V02":
        raw["facts"]["withdrawal_timing"]["evidence"] = ["offline-unknown-citation"]
    else:
        sent = next(event for event in raw["timeline"] if event["event_type"] == "customer_replied")
        sent["evidence"] += sent["evidence"]

    with pytest.raises(ValueError, match=message):
        prepared.decode_output(json.dumps(raw))


def _with_source(snapshot_data, source_id, text):
    original = next(
        source for source in snapshot_data["catalog"] if source["source_id"] == source_id
    )
    original.update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
    return AnalysisInput.model_validate_json(json.dumps(snapshot_data))


def _rebind(raw, before, after):
    new_ids = {
        (source, row["text"]): row["citation_id"]
        for source, rows in after.lines.items()
        for row in rows
    }
    anchored = {
        row["citation_id"]: (source, row["text"])
        for source, rows in before.lines.items()
        for row in rows
        if row["citation_id"] is not None
    }
    for item in (*raw["facts"].values(), *raw["timeline"]):
        item["evidence"] = [new_ids[anchored[value]] for value in item["evidence"]]


def test_real_charge_is_not_removed_from_a_changed_ledger(recording):
    settings, run, _ = recording("V06")
    original = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    ledger = next(source for source in original.catalog if source.kind == "payment_ledger")
    assert ledger.text is not None
    opening, release, _ = ledger.text.splitlines()
    charge = "2026-06-22T15:55:00+03:00 | capture | банк провёл списание"
    snapshot = _with_source(
        run["snapshot"], ledger.source_id, "\n".join((opening, charge, release))
    )
    raw = json.loads(run["raw_output"])
    before = CitationCatalogue.from_snapshot(original)
    after = CitationCatalogue.from_snapshot(snapshot)
    _rebind(raw, before, after)
    charge_id = next(
        row["citation_id"] for row in after.lines[ledger.source_id] if row["text"] == charge
    )
    event = next(event for event in raw["timeline"] if event["event_type"] == "payment_captured")
    event["time"]["value"] = "2026-06-22T15:55:00+03:00"
    event["evidence"] = [charge_id]
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    (capture,) = [event for event in card.timeline if event.event_type == "payment_captured"]
    assert capture.evidence[0].fragments == [charge]


def test_original_refusal_and_receipt_are_not_ignored_when_available(recording):
    settings, run, _ = recording("V02")
    original = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    source = run["snapshot"]["catalog"][0]
    sent = "Date: 2026-09-18T08:00:00+03:00"
    receipt = "Received by supplier: 2026-09-18T08:01:00+03:00"
    message = "Я отказываюсь от бронирования."
    text = "\n".join((sent, receipt, message))
    original_source_id = "received_original"
    run["snapshot"]["catalog"].append(
        {
            **source,
            "source_id": original_source_id,
            "locator": original_source_id,
            "title": "Оригинал отказа и квитанция",
            "position": len(run["snapshot"]["catalog"]),
            "version_id": str(uuid4()),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    )
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    raw = json.loads(run["raw_output"])
    after = CitationCatalogue.from_snapshot(snapshot)
    _rebind(raw, CitationCatalogue.from_snapshot(original), after)
    evidence = [
        row["citation_id"]
        for row in after.lines[original_source_id]
        if row["text"] in {receipt, message}
    ]
    raw["facts"]["withdrawal_timing"] = {"value": "before_supplier_cancel", "evidence": evidence}
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    (withdrawal,) = [fact for fact in card.facts if fact.key == "withdrawal_timing"]
    assert withdrawal.value == "before_supplier_cancel"
    assert withdrawal.evidence[0].fragments == [receipt, message]
    assert not card.follow_up.requirements
