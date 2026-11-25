"""Assemble reviewed source records while preserving model claims and raw output."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput
from proofline.providers.citation_ids import CitationCatalogue
from proofline.providers.factory import create_live_provider
from proofline.validation.assembly import validate_assembly
from proofline.validation.case_evidence import check_case_evidence
from proofline.validation.reply_events import check_reply_events
from proofline.validation.required_events import check_required_events

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def recorded_case():
    (archive,) = (ROOT / "evals/live").glob("v47-*")
    preflight = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))

    def read(case_id):
        folder = archive / f"full-{case_id}"
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        options = {
            "_env_file": None,
            "database_url": "postgresql+psycopg://offline/unused",
            "live_enabled": True,
            "llm_api_key": "offline",
            **preflight["bound_settings"],
        }
        return options, run

    return read


@pytest.mark.parametrize(
    ("case_id", "owned"),
    [
        ("V02", ["supplier_cancelled", "customer_replied", "customer_message_received"]),
        ("V03", ["payment_captured", "refund_initiated", "refund_settled"]),
    ],
)
def test_reviewed_sources_assemble_events_excluded_from_the_model_request(
    recorded_case, case_id, owned
):
    options, run = recorded_case(case_id)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    settings = Settings(**options, groq_test_assemble_reviewed_evidence=True)
    prepared = create_live_provider(settings).prepare(snapshot)
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "minItems" not in schema["properties"]["timeline"]
    assert set(owned).isdisjoint(schema["$defs"]["EventType"]["enum"])
    binding = prepared.generation_profile["reviewed_citation_rules_v1"]["source_events"]
    assert [event["event_type"] for event in binding] == owned
    raw = json.loads(run["raw_output"])
    raw["timeline"] = []
    if case_id == "V03":
        raw["facts"]["payment_outcome"]["evidence"] = []
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    assert not raw["timeline"]
    assert [event.event_type for event in card.timeline] == owned
    assert {fact.key: fact.value for fact in card.facts} == {
        key: fact["value"] for key, fact in raw["facts"].items()
    }
    assert card.follow_up.model_dump(mode="json") == raw["follow_up"]
    context = snapshot.to_validation_context()
    checked = check_required_events(
        card, context, validate_assembly(card, context), settings.required_events(snapshot)
    )
    assert not check_case_evidence(
        card, context, checked, (settings.groq_test_case_evidence or {}).get(case_id)
    ).issues


def test_assembly_does_not_fill_an_event_conflicting_with_a_checked_time(recorded_case):
    options, run = recorded_case("V02")
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    settings = Settings(**options, groq_test_assemble_reviewed_evidence=True)
    prepared = create_live_provider(settings).prepare(snapshot)
    raw = json.loads(run["raw_output"])
    raw["timeline"].append(
        {
            "event_type": "supplier_cancelled",
            "time": {"role": "occurred", "value": "2026-09-18T10:21:00+03:00"},
            "evidence": raw["facts"]["cancellation_status"]["evidence"],
        }
    )
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    (cancellation,) = [event for event in card.timeline if event.event_type == "supplier_cancelled"]
    assert cancellation.time.value == "2026-09-18T10:21:00+03:00"


@pytest.mark.parametrize("change", ["edited", "excluded"])
def test_assembly_requires_the_reviewed_bank_statement(recorded_case, change):
    options, run = recorded_case("V03")
    statement = next(
        source
        for source in run["snapshot"]["catalog"]
        if source["source_id"] == options["groq_test_case_evidence"]["V03"]["bank_source_id"]
    )
    if change == "edited":
        statement["text"] += "\nEdited statement"
        statement["text_sha256"] = hashlib.sha256(statement["text"].encode()).hexdigest()
    else:
        statement.update(included=False, text=None, version_id=None, text_sha256=None)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    settings = Settings(**options, groq_test_assemble_reviewed_evidence=True)
    prepared = create_live_provider(settings).prepare(snapshot)
    assert "reviewed_citation_rules_v1" not in prepared.generation_profile
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "refund_settled" in schema["$defs"]["EventType"]["enum"]


def test_assembly_leaves_ambiguous_evidence_for_model_analysis(recorded_case):
    options, run = recorded_case("V02")
    mark = next(
        item
        for item in options["groq_test_required_events"]["V02"]
        if item["event_type"] == "supplier_cancelled"
    )
    source = next(
        item for item in run["snapshot"]["catalog"] if item["source_id"] == mark["source_id"]
    )
    source["text"] += "\n" + mark["fragment"]
    digest = hashlib.sha256(source["text"].encode()).hexdigest()
    source["text_sha256"] = digest
    mark["text_sha256"] = digest
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    settings = Settings(**options, groq_test_assemble_reviewed_evidence=True)
    prepared = create_live_provider(settings).prepare(snapshot)
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "supplier_cancelled" in schema["$defs"]["EventType"]["enum"]
    assert prepared.decode_output is not None
    raw = json.loads(run["raw_output"])
    raw["facts"]["cancellation_status"]["evidence"] = []
    raw["timeline"] = []
    card = prepared.decode_output(json.dumps(raw))
    assert all(event.event_type != "supplier_cancelled" for event in card.timeline)


def test_assembly_leaves_the_refund_classification_to_the_model(recorded_case):
    options, run = recorded_case("V03")
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    settings = Settings(**options, groq_test_assemble_reviewed_evidence=True)
    prepared = create_live_provider(settings).prepare(snapshot)
    raw = json.loads(run["raw_output"])
    initiation = prepared.generation_profile["reviewed_citation_rules_v1"]["refund_trace"][
        "fact_evidence"
    ][0]
    raw["facts"]["payment_outcome"] = {"value": "refund_pending", "evidence": [initiation]}
    raw["timeline"] = []
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    (payment,) = card.facts
    assert payment.value == "refund_pending"
    assert len(payment.evidence) == 1 and len(payment.evidence[0].fragments) == 1
    context = snapshot.to_validation_context()
    base = validate_assembly(card, context)
    refund = (settings.groq_test_case_evidence or {})["V03"]
    assert not check_case_evidence(card, context, base, refund).issues
    result = check_case_evidence(
        card,
        context,
        base,
        refund,
        assemble_reviewed_evidence=True,
    )
    assert [issue.code for issue in result.issues] == ["refund_fact_event_conflict"]
    assert settings.effective_validation_profile.endswith("+reviewed-evidence-test-v3")


def test_assembly_requires_the_isolated_citation_profile():
    with pytest.raises(ValidationError, match="GROQ_TEST_ASSEMBLE_REVIEWED_EVIDENCE"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://offline/unused",
            groq_test_assemble_reviewed_evidence=True,
        )


@pytest.fixture
def recorded_v04():
    (archive,) = (ROOT / "evals/live").glob("v48-*")
    (cohort,) = (ROOT / "evals/live").glob("v34-*")
    preflight = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))
    run = json.loads((archive / "full-V04/run.json").read_text(encoding="utf-8"))
    valid = json.loads((cohort / "full-V04/run.json").read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://offline/unused",
        live_enabled=True,
        llm_api_key="offline",
        **preflight["bound_settings"],
    )
    return settings, run["snapshot"], json.loads(valid["raw_output"])


def test_v04_uses_compact_output_and_assembles_the_linked_correspondence(recorded_v04):
    settings, recorded, raw = recorded_v04
    snapshot = AnalysisInput.model_validate_json(json.dumps(recorded))
    prepared = create_live_provider(settings).prepare(snapshot)
    wire = prepared.request["response_format"]["json_schema"]
    assert wire["name"] == "assembly_citation_ids_compact" and wire["strict"] is True
    schema = wire["schema"]
    keys = {"facts", "readiness", "follow_up"}
    assert set(schema["required"]) == set(schema["properties"]) == keys
    assert "TimelineEventWithCitationIds" not in schema["$defs"]
    assert (
        prepared.generation_profile["provider_schema_version"] == "assembly-citation-ids-compact-v1"
    )
    assert prepared.decode_output is not None
    compact = {key: raw[key] for key in keys}
    card = prepared.decode_output(json.dumps(compact))
    assert card.case_id == "V04"
    assert [event.event_type for event in card.timeline] == [
        "alternative_offered",
        "customer_replied",
        "customer_message_received",
    ]
    assert [event.time.value for event in card.timeline] == [
        "2026-10-08T12:10:00+03:00",
        "2026-10-08T12:25:00+03:00",
        "2026-10-08T12:27:00+03:00",
    ]
    assert card.facts[0].value == compact["facts"]["alternative_acceptance"]["value"]
    assert card.follow_up.model_dump(mode="json") == compact["follow_up"]
    result = validate_assembly(card, snapshot.to_validation_context())
    assert not check_reply_events(
        card, snapshot, result, settings.groq_test_acceptance_replies["V04"]
    ).issues

    for invalid in ({**compact, "case_id": "V01"}, {**compact, "timeline": []}):
        with pytest.raises(ValueError, match="Compact response fields"):
            prepared.decode_output(json.dumps(invalid))
    compact.pop("follow_up")
    with pytest.raises(ValueError, match="Compact response fields"):
        prepared.decode_output(json.dumps(compact))


@pytest.mark.parametrize("missing", ["offer", "reply"])
def test_v04_uses_the_full_format_if_a_linked_record_is_excluded(recorded_v04, missing):
    settings, recorded, _ = recorded_v04
    reply_id = settings.groq_test_acceptance_replies["V04"].source_id
    journal = next(source for source in recorded["catalog"] if source["source_id"] == reply_id)
    source = (
        journal
        if missing == "reply"
        else next(
            record
            for record in recorded["catalog"]
            if record["included"]
            and f"Квитанция переписки: {journal['locator']}." in record["text"]
        )
    )
    source.update(included=False, text=None, version_id=None, text_sha256=None)
    snapshot = AnalysisInput.model_validate_json(json.dumps(recorded))
    prepared = create_live_provider(settings).prepare(snapshot)
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert {"case_id", "timeline"} <= set(schema["required"])
    assert "source_events" not in prepared.generation_profile.get("reviewed_citation_rules_v1", {})


@pytest.fixture
def recorded_v06():
    (archive,) = (ROOT / "evals/live").glob("v51-*")
    folder = archive / "full-V06"
    preflight = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    options = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
        **preflight["bound_settings"],
    }
    return options, run, json.loads((folder / "request.json").read_text(encoding="utf-8"))


def test_v06_uses_both_ledger_citations_and_clears_the_covered_request(recorded_v06):
    options, run, request = recorded_v06
    settings = Settings(**options)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(settings).prepare(snapshot)
    assert prepared.request == request
    assert prepared.decode_output is not None
    raw = run["raw_output"]
    card = prepared.decode_output(raw)

    payment = next(fact for fact in card.facts if fact.key == "payment_outcome")
    assert payment.value == "authorization_release"
    (evidence,) = payment.evidence
    ledger = next(source for source in snapshot.catalog if source.kind == "payment_ledger")
    assert evidence.source_id == ledger.source_id
    assert ledger.text is not None
    assert evidence.fragments == ledger.text.splitlines()[1:]
    assert card.readiness == "ready_for_rules"
    assert card.follow_up.action == "none"
    assert not card.follow_up.requirements
    assert json.loads(raw)["follow_up"]["requirements"] == ["bank_settlement_document"]
    context = snapshot.to_validation_context()
    assert not check_required_events(
        card, context, validate_assembly(card, context), settings.required_events(snapshot)
    ).issues


@pytest.mark.parametrize("change", ["omit_negative_record", "contradictory_record", "disabled"])
def test_v06_keeps_the_request_without_a_reviewed_snapshot(recorded_v06, change):
    options, run, _ = recorded_v06
    original = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    raw = json.loads(run["raw_output"])
    if change == "disabled":
        options["groq_test_assemble_reviewed_evidence"] = False
    else:
        ledger = next(
            source for source in run["snapshot"]["catalog"] if source["kind"] == "payment_ledger"
        )
        if change == "omit_negative_record":
            text = ledger["text"].rsplit("\n", 1)[0]
            ledger.update(text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        else:
            text = "2026-06-22T16:00:00+03:00 | capture | банк провёл списание"
            extra = {
                **ledger,
                "source_id": "other_ledger",
                "locator": "other_ledger",
                "position": len(run["snapshot"]["catalog"]),
                "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            run["snapshot"]["catalog"].append(extra)
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    before = CitationCatalogue.from_snapshot(original)
    after = CitationCatalogue.from_snapshot(snapshot)
    new_ids = {
        (source_id, row["text"]): row["citation_id"]
        for source_id, rows in after.lines.items()
        for row in rows
        if row["citation_id"] is not None
    }
    old_ids = {
        row["citation_id"]: new_ids.get((source_id, row["text"]))
        for source_id, rows in before.lines.items()
        for row in rows
    }
    for item in (*raw["facts"].values(), *raw["timeline"]):
        item["evidence"] = [old_ids[citation] for citation in item["evidence"]]

    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    assert "reviewed_citation_rules_v1" not in prepared.generation_profile
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    assert card.readiness == "request_evidence"
    assert card.follow_up.action == "ask_user"
    assert card.follow_up.requirements == ["bank_settlement_document"]


def test_v06_keeps_the_request_when_the_output_conflicts_with_the_reviewed_timeline(recorded_v06):
    options, run, _ = recorded_v06
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    raw = json.loads(run["raw_output"])
    release = next(
        event for event in raw["timeline"] if event["event_type"] == "authorization_released"
    )
    release["time"]["value"] = "2026-06-22T16:44:00+03:00"

    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(raw))
    assert card.readiness == "request_evidence"
    assert card.follow_up.requirements == ["bank_settlement_document"]
