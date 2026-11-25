"""Rebind the same profile on a fresh fixture snapshot without a network call."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from proofline.config import Settings
from proofline.domain.assembly import AssemblyOutput
from proofline.domain.inputs import AnalysisInput
from proofline.providers import live_prompt_v33
from proofline.providers.citation_ids import CANCELLATION_NOTICE_GUIDANCE
from proofline.providers.factory import create_live_provider
from proofline.providers.groq_stage4_profile import bind_profile, read_profile
from proofline.providers.replay import ReplayCatalog
from proofline.validation.assembly import validate_assembly
from proofline.validation.case_evidence import check_case_evidence

ROOT = Path(__file__).resolve().parents[2]


def _without_v01_offer_changes(request):
    original = deepcopy(request)
    schema = original["response_format"]["json_schema"]["schema"]
    definitions = schema["$defs"]
    offer = definitions.pop("ReviewedV01Offer")["properties"]
    assert offer["event_type"]["enum"] == ["alternative_offered"]
    assert offer["time"]["properties"]["role"]["enum"] == ["sent"]
    assert offer["time"]["properties"]["value"]["enum"] == ["2026-08-14T09:10:00+03:00"]
    catalogue = json.loads(request["messages"][1]["content"])["catalog"]
    (letter,) = [
        {line["text"]: line["citation_id"] for line in source.get("citation_lines", [])}
        for source in catalogue
        if any(
            line["text"] == "Квитанция переписки: 011dad68c7164f5d."
            for line in source.get("citation_lines", [])
        )
    ]
    sent = letter["Отправлено: 2026-08-14T09:10:00+03:00"]
    (text,) = [
        citation for line, citation in letter.items() if line.startswith("Можем предложить выезд ")
    ]
    assert offer["evidence"] == {
        "title": "Evidence",
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"type": "string", "enum": [sent, text]},
    }
    event_type = definitions["TimelineEventWithCitationIds"]["properties"]
    assert event_type["event_type"]["enum"] == [
        value for value in definitions["EventType"]["enum"] if value != "alternative_offered"
    ]
    event_type["event_type"] = {"$ref": "#/$defs/EventType"}
    timeline = schema["properties"]["timeline"]
    assert timeline["items"] == {
        "anyOf": [
            {"$ref": "#/$defs/TimelineEventWithCitationIds"},
            {"$ref": "#/$defs/ReviewedV01Offer"},
        ]
    }
    timeline["items"] = {"$ref": "#/$defs/TimelineEventWithCitationIds"}
    instruction = original["messages"][0]
    previous, separator, guidance = instruction["content"].rpartition(
        "\n\nFor the verified supplier offer"
    )
    assert separator and "alternative_offered" in guidance
    assert f"{sent} for the sending line" in guidance
    assert f"{text} for the offer text" in guidance
    assert "separate from receipt" in guidance
    instruction["content"] = previous
    return original


@pytest.fixture
def recorded_v01():
    (archive,) = (ROOT / "evals/live").glob("v39-*")
    preflight = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))
    case = archive / "ablation-V01"
    run = json.loads((case / "run.json").read_text(encoding="utf-8"))
    options = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
        **preflight["bound_settings"],
    }
    request = json.loads((case / "request.json").read_text(encoding="utf-8"))
    return options, run, request


def test_v01_adds_the_reviewed_receipt_and_routes_to_its_unread_archive(recorded_v01):
    options, run, request = recorded_v01
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    request["response_format"]["json_schema"]["schema"]["properties"]["timeline"]["minItems"] = 1
    assert _without_v01_offer_changes(prepared.request) == request
    assert prepared.decode_output is not None
    raw = json.loads(run["raw_output"])
    card = prepared.decode_output(run["raw_output"])
    assert raw["follow_up"]["action"] == "ask_user"
    assert card.follow_up.model_dump(mode="json") == {
        "action": "read_source",
        "locator": "1618ae5e5b894145",
        "requirements": ["original_acceptance_reply"],
    }
    assert card.facts[0].value == "unconfirmed"
    (receipt,) = [e for e in card.timeline if e.event_type == "customer_message_received"]
    assert receipt.time.value == "2026-08-14T09:36:00+03:00"
    assert receipt.evidence[0].fragments == [
        "2026-08-14T09:36:00+03:00 — почтовый шлюз принял ответ клиента в этой переписке."
    ]
    rule = prepared.generation_profile["reviewed_citation_rules_v1"]["unread_reply"]
    raw["timeline"].append(
        {
            "event_type": receipt.event_type,
            "time": receipt.time.model_dump(mode="json"),
            "evidence": [rule["receipt_citation"]],
        }
    )
    assert prepared.decode_output(json.dumps(raw)) == card
    raw["readiness"] = "request_evidence"
    raw["follow_up"] = {"action": "none", "locator": None, "requirements": []}
    assert prepared.decode_output(json.dumps(raw)) == card

    raw["facts"]["alternative_acceptance"]["value"] = "not_accepted"
    assert prepared.decode_output(json.dumps(raw)).follow_up.action == "none"


@pytest.mark.parametrize("variant", ["missing", "ambiguous", "not_allowlisted", "stale_review"])
def test_v01_binding_fails_closed_without_one_reviewed_available_reference(recorded_v01, variant):
    options, run, _ = recorded_v01
    mark = options["groq_test_required_events"]["V01"][0]
    journal = next(
        source for source in run["snapshot"]["catalog"] if source["source_id"] == mark["source_id"]
    )
    pointer = "Оригинал MIME помещён в архив: 1618ae5e5b894145."
    assert pointer in journal["text"]
    if variant == "not_allowlisted":
        options["groq_test_lookup_sources"]["V01"] = []
    else:
        replacement = (
            "" if variant == "missing" else pointer + "\nОригинал MIME помещён в архив: different."
        )
        journal["text"] = journal["text"].replace(pointer, replacement)
        sha = hashlib.sha256(journal["text"].encode()).hexdigest()
        journal["text_sha256"] = sha
        if variant != "stale_review":
            mark["text_sha256"] = sha
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    assert "reviewed_citation_rules_v1" not in prepared.generation_profile
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "minItems" not in schema["properties"]["timeline"]
    assert "ReviewedV01Offer" not in schema["$defs"]


def test_v01_timeline_minimum_requires_the_linked_offer_letter(recorded_v01):
    options, run, request = recorded_v01
    offer_letter = next(
        source
        for source in run["snapshot"]["catalog"]
        if source["included"] and "Квитанция переписки:" in source["text"]
    )
    offer_letter["text"] = offer_letter["text"].replace(
        "Квитанция переписки: 011dad68c7164f5d.", "Квитанция переписки: другая."
    )
    offer_letter["text_sha256"] = hashlib.sha256(offer_letter["text"].encode()).hexdigest()
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    assert set(prepared.generation_profile["reviewed_citation_rules_v1"]) == {"unread_reply"}
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "minItems" not in schema["properties"]["timeline"]
    assert "ReviewedV01Offer" not in schema["$defs"]
    assert prepared.request["messages"][0] == request["messages"][0]


@pytest.mark.parametrize("variant", ["as_recorded", "missing_quote", "wrong_time", "other_source"])
def test_v01_copies_only_a_selected_offer_quote_to_its_matching_send_event(recorded_v01, variant):
    options, _, _ = recorded_v01
    (archive,) = (ROOT / "evals/live").glob("v40-*")
    condition = archive / "ablation-V01"
    run = json.loads((condition / "run.json").read_text(encoding="utf-8"))
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    prepared = create_live_provider(Settings(**options)).prepare(snapshot)
    request = json.loads((condition / "request.json").read_text(encoding="utf-8"))
    request["response_format"]["json_schema"]["schema"]["properties"]["timeline"]["minItems"] = 1
    assert _without_v01_offer_changes(prepared.request) == request
    assert prepared.decode_output is not None
    raw = json.loads(run["raw_output"])
    original_offer = raw["timeline"][0]
    assert original_offer["event_type"] == "alternative_offered"
    quote = (
        "Можем предложить выезд в 18:20 вместо 16:00, с тем же местом посадки. "
        "Напишите, подходит ли вам этот вариант."
    )
    catalogue = json.loads(prepared.request["messages"][1]["content"])["catalog"]
    (offer_id,) = [
        line["citation_id"]
        for source in catalogue
        for line in source.get("citation_lines", [])
        if line["text"] == quote
    ]
    assert offer_id in raw["facts"]["alternative_acceptance"]["evidence"]
    assert offer_id not in original_offer["evidence"]
    if variant == "missing_quote":
        raw["facts"]["alternative_acceptance"]["evidence"].remove(offer_id)
    elif variant == "wrong_time":
        original_offer["time"]["value"] = "2026-08-14T09:11:00+03:00"
    elif variant == "other_source":
        original_offer["evidence"] = raw["timeline"][1]["evidence"].copy()
    card = prepared.decode_output(json.dumps(raw))
    (offer,) = [event for event in card.timeline if event.event_type == "alternative_offered"]
    fragments = [fragment for evidence in offer.evidence for fragment in evidence.fragments]
    assert fragments.count(quote) == (1 if variant == "as_recorded" else 0)
    assert card.facts[0].value == "unconfirmed"


@pytest.fixture
def seeded_profile():
    specification = read_profile()
    catalog = ReplayCatalog(ROOT / "fixtures")
    recorded = {r.option.replay_id: r.input for r in catalog.recordings}
    snapshots = {}
    for case in specification.cases:
        body = recorded[f"v3-{case.condition}"].model_dump(mode="json")
        for source in body["catalog"]:
            if source["included"]:
                source["version_id"] = f"fresh-{case.case_id}-{source['source_id']}"
        snapshots[case.case_id] = AnalysisInput.model_validate_json(json.dumps(body))
    return specification, snapshots


def test_one_profile_rebinds_all_cases_and_pins_each_prompt(seeded_profile):
    specification, snapshots = seeded_profile
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://offline/unused",
        live_enabled=True,
        llm_api_key="offline",
        **bind_profile(specification, snapshots),
    )
    provider = create_live_provider(settings)
    assert len(specification.cases) == 6
    for case in specification.cases:
        snapshot = snapshots[case.case_id]
        prepared = provider.prepare(snapshot)
        assert prepared.request["max_completion_tokens"] == 3072
        assert prepared.generation_profile["prompt_sha256"] == case.expected_prompt_sha256
        assert len(settings.required_events(snapshot)) == len(case.reviewed_events)
        assert prepared.generation_profile["provider_lookup_source_ids"] == case.lookup_sources
        if case.reviewed_reply is not None:
            assert prepared.generation_profile["provider_covered_requirements"] == [
                "original_acceptance_reply"
            ]


def test_profile_rejects_changed_input_or_stale_reviewed_hash(seeded_profile):
    specification, snapshots = seeded_profile
    changed = dict(snapshots)
    changed["V02"] = changed["V02"].model_copy(update={"user_text": "New input"})
    with pytest.raises(ValueError, match="V02: input differs"):
        bind_profile(specification, changed)
    v02 = next(case for case in specification.cases if case.case_id == "V02")
    v02.reviewed_events[0]["text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="V02: cannot bind reviewed event"):
        bind_profile(specification, snapshots)


def test_qwen_without_reasoning_keeps_pinned_instruction_and_strict_wire_schema(
    seeded_profile, monkeypatch
):
    specification, snapshots = seeded_profile
    v02 = snapshots["V02"]
    bound = bind_profile(specification, snapshots)
    base = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
    }
    precedent = create_live_provider(Settings(**base, **bound)).prepare(v02)
    del bound["groq_reasoning_effort"]
    del bound["model_max_output_tokens"]
    monkeypatch.setenv("GROQ_REASONING_EFFORT", "none")
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "1000")
    calls = []

    def capture(request):
        calls.append(json.loads(request.content))
        return httpx.Response(429)

    settings = Settings(**base, **bound)
    provider = create_live_provider(settings, transport=httpx.MockTransport(capture))
    result = provider.generate(provider.prepare(v02))

    assert calls == [
        {**precedent.request, "reasoning_effort": "none", "max_completion_tokens": 1000}
    ]
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert result.generation_profile["reasoning_effort"] == "none"


def test_case_evidence_scope_composes_with_prompt_and_read_only_source_rules(seeded_profile):
    specification, snapshots = seeded_profile
    base = {
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
        **bind_profile(specification, snapshots),
    }
    prior = create_live_provider(Settings(_env_file=None, **base))
    scoped = create_live_provider(Settings(_env_file=None, **base, groq_test_case_evidence={}))
    withdrawal = ["original_customer_message", "delivery_receipt"]
    room = ["quiet_room", "courtyard_windows"]
    expected = {
        "V01": [*room, "original_acceptance_reply"],
        "V02": withdrawal,
        "V03": ["bank_settlement_document"],
        "V04": room,
        "V05": withdrawal,
        "V06": ["bank_settlement_document"],
    }
    for case_id, snapshot in snapshots.items():
        before = prior.prepare(snapshot)
        after = scoped.prepare(snapshot)
        original = before.request["response_format"]["json_schema"]["schema"]
        narrowed = after.request["response_format"]["json_schema"]["schema"]
        assert narrowed["$defs"]["RequirementKey"]["enum"] == expected[case_id]
        original["$defs"]["RequirementKey"]["enum"] = expected[case_id]
        if case_id == "V01":
            original["properties"]["timeline"]["minItems"] = 1
            assert _without_v01_offer_changes(after.request) == before.request
        elif case_id == "V02":
            current = after.request["messages"][0]["content"]
            previous, separator, guidance = current.rpartition(
                "\n\nFor the documented support form"
            )
            assert separator and previous == before.request["messages"][0]["content"]
            assert "2026-09-18T12:09:55+03:00 with role=sent" in guidance
            assert "2026-09-18T12:10:00+03:00 with role=received" in guidance
            assert "withdrawal_timing=unconfirmed" in guidance
            assert "readiness=request_evidence" in guidance
            assert "action=ask_user" in guidance and "locator=null" in guidance
            assert "original_customer_message and delivery_receipt" in guidance
            before.request["messages"][0]["content"] = current
        elif case_id == "V05":
            before.request["messages"][0]["content"] += CANCELLATION_NOTICE_GUIDANCE
            original["properties"]["timeline"] = narrowed["properties"]["timeline"]
            original["$defs"]["TimelineEventWithCitationIds"] = narrowed["$defs"][
                "TimelineEventWithCitationIds"
            ]
            original["$defs"]["ReviewedSupplierCancellation"] = narrowed["$defs"][
                "ReviewedSupplierCancellation"
            ]
        if case_id != "V01":
            assert after.request == before.request
        assert (
            after.generation_profile["provider_lookup_source_ids"]
            == before.generation_profile["provider_lookup_source_ids"]
        )
        assert after.generation_profile["request_profile"].endswith("-case-evidence-v1")

    cancellation = snapshots["V02"].model_copy(
        update={"requested_fact_keys": ("cancellation_status",)}
    )
    schema = scoped.prepare(cancellation).request["response_format"]["json_schema"]["schema"]
    definition = schema["properties"]["follow_up"]["$ref"].split("/")[-1]
    assert schema["$defs"][definition]["properties"]["action"]["enum"] == ["none"]


def test_case_evidence_settings_are_rejected_outside_the_citation_test(monkeypatch):
    monkeypatch.setenv("GROQ_TEST_CASE_EVIDENCE", "{}")
    with pytest.raises(ValidationError, match="GROQ_TEST_CASE_EVIDENCE"):
        Settings(_env_file=None, database_url="postgresql+psycopg://offline/unused")


def test_only_reviewed_v05_gets_guidance_and_the_cancellation_time_schema():
    (archive,) = (ROOT / "evals/live").glob("v29-*")
    receipt = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))
    configuration = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
        **receipt["bound_settings"],
    }
    provider = create_live_provider(Settings(**configuration))
    for case_id, case in receipt["cases"].items():
        folder = archive / case["condition"]
        snapshot_json = json.loads((folder / "run.json").read_text(encoding="utf-8"))["snapshot"]
        snapshot = AnalysisInput.model_validate_json(json.dumps(snapshot_json))
        expected = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        prepared = provider.prepare(snapshot)
        if case_id == "V01":
            schema = expected["response_format"]["json_schema"]["schema"]
            schema["properties"]["timeline"]["minItems"] = 1
            assert _without_v01_offer_changes(prepared.request) == expected
            continue
        elif case_id == "V02":
            current = prepared.request["messages"][0]["content"]
            previous, separator, _ = current.rpartition("\n\nFor the documented support form")
            assert separator and previous == expected["messages"][0]["content"]
            expected["messages"][0]["content"] = current
        elif case_id == "V03":
            expected["messages"][0]["content"] = (
                expected["messages"][0]["content"]
                .replace(live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE, "")
                .replace(live_prompt_v33.REPLY_PAIR_EXAMPLE, "")
            )
        elif case_id == "V05":
            schema = prepared.request["response_format"]["json_schema"]["schema"]
            definitions = schema["$defs"]
            assert schema["properties"]["timeline"]["minItems"] == 2
            items = schema["properties"]["timeline"]["items"]["anyOf"]
            assert items == [
                {"$ref": "#/$defs/TimelineEventWithCitationIds"},
                {"$ref": "#/$defs/ReviewedSupplierCancellation"},
            ]
            event = definitions["ReviewedSupplierCancellation"]["properties"]
            assert event["event_type"]["enum"] == ["supplier_cancelled"]
            assert event["time"]["properties"]["value"]["enum"] == ["2026-10-04T10:00:00+03:00"]
            assert prepared.generation_profile["prompt_version"].endswith("-cancellation-notice-v2")
            prior = expected["messages"][0]["content"]
            current = prepared.request["messages"][0]["content"]
            assert current.startswith(prior)
            assert "completed cancellation" in current[len(prior) :]
            assert "sending time" in current[len(prior) :]
            assert "at most once per evidence list" in current[len(prior) :]
            assert "Received by supplier" in current[len(prior) :]
            assert "that fact's own evidence" in current[len(prior) :]
            prepared.request["messages"][0]["content"] = prior
        if case_id in {"V03", "V05"}:
            prepared.request["response_format"]["json_schema"]["schema"] = expected[
                "response_format"
            ]["json_schema"]["schema"]
        assert prepared.request == expected

    folder = archive / "full-V05"
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    for mark in configuration["groq_test_required_events"]["V05"]:
        if mark["event_type"] == "supplier_cancelled":
            mark["source_version_id"] = "old-cancellation-version"
    prepared = create_live_provider(Settings(**configuration)).prepare(snapshot)
    assert prepared.request == json.loads((folder / "request.json").read_text(encoding="utf-8"))
    assert "reviewed_citation_rules_v1" not in prepared.generation_profile


def test_refund_guidance_and_timeline_minimum_require_reviewed_v03_sources(seeded_profile):
    specification, snapshots = seeded_profile
    base = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://offline/unused",
        "live_enabled": True,
        "llm_api_key": "offline",
        **bind_profile(specification, snapshots),
    }
    bindings = json.loads(
        (ROOT / "fixtures/live/groq-stage4-case-evidence.json").read_text(encoding="utf-8")
    )
    plain = create_live_provider(Settings(**base, groq_test_case_evidence={}))
    reviewed = create_live_provider(Settings(**base, groq_test_case_evidence=bindings))
    for case_id in ("V01", "V02", "V04", "V05", "V06"):
        after = reviewed.prepare(snapshots[case_id])
        before = plain.prepare(snapshots[case_id])
        assert (after.request, after.generation_profile) == (
            before.request,
            before.generation_profile,
        )

    before = plain.prepare(snapshots["V03"])
    after = reviewed.prepare(snapshots["V03"])
    before_text = before.request["messages"][0]["content"]
    old_example = live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE
    assert old_example in before_text
    assert old_example not in after.request["messages"][0]["content"]
    assert '"fragments"' not in after.request["messages"][0]["content"]
    text = after.request["messages"][0]["content"].removeprefix(
        before_text.replace(old_example, "").replace(live_prompt_v33.REPLY_PAIR_EXAMPLE, "")
    )
    assert text != after.request["messages"][0]["content"]
    assert all(
        term in text
        for term in ("payment_captured", "refund_initiated", "refund_settled", "matching trace")
    )
    assert bindings["V03"]["trace_id"] not in text
    assert after.generation_profile["prompt_sha256"] != before.generation_profile["prompt_sha256"]
    assert after.generation_profile["prompt_version"].endswith("-refund-evidence-v3")
    after.request["messages"][0]["content"] = before_text
    schema = after.request["response_format"]["json_schema"]["schema"]
    timeline = schema["properties"]["timeline"]
    assert timeline.pop("minItems", None) == 3
    assert after.request == before.request

    changed = snapshots["V03"].model_dump(mode="json")
    statement = next(
        s for s in changed["catalog"] if s["source_id"] == bindings["V03"]["bank_source_id"]
    )
    statement.update(included=False, text=None, version_id=None, text_sha256=None)
    without_statement = AnalysisInput.model_validate_json(json.dumps(changed))
    assert reviewed.prepare(without_statement).request == plain.prepare(without_statement).request

    edited = snapshots["V03"].model_dump(mode="json")
    statement = next(
        s for s in edited["catalog"] if s["source_id"] == bindings["V03"]["bank_source_id"]
    )
    statement["text"] += "\nНовая версия."
    statement["text_sha256"] = hashlib.sha256(statement["text"].encode()).hexdigest()
    edited_snapshot = AnalysisInput.model_validate_json(json.dumps(edited))
    assert reviewed.prepare(edited_snapshot).request == plain.prepare(edited_snapshot).request


@pytest.mark.parametrize("case_id", ["V03", "V05"])
def test_reviewed_citations_keep_v34_requests_and_use_only_selected_ids(case_id):
    (archive,) = (ROOT / "evals/live").glob("v34-*")
    receipt = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))
    folder = archive / f"full-{case_id}"
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    snapshot = AnalysisInput.model_validate_json(json.dumps(run["snapshot"]))
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://offline/unused",
        live_enabled=True,
        llm_api_key="offline",
        **receipt["bound_settings"],
    )
    prepared = create_live_provider(config).prepare(snapshot)
    expected_request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    if case_id == "V03":
        expected_request["messages"][0]["content"] = (
            expected_request["messages"][0]["content"]
            .replace(live_prompt_v33.SINGLE_DOCUMENT_TIMELINE_EXAMPLE, "")
            .replace(live_prompt_v33.REPLY_PAIR_EXAMPLE, "")
        )
    if case_id == "V05":
        expected_request["response_format"]["json_schema"]["schema"]["properties"]["timeline"][
            "minItems"
        ] = 2
    assert prepared.request == expected_request
    assert prepared.decode_output is not None
    rules = prepared.generation_profile["reviewed_citation_rules_v1"]
    result = prepared.decode_output(run["raw_output"])
    modified = json.loads(run["raw_output"])
    if case_id == "V03":
        trace = rules["refund_trace"]["trace"]
        fact = modified["facts"]["payment_outcome"]
        assert trace not in fact["evidence"]
        assert "Трасса: 189a5d15e84a4adb" in [
            fragment for evidence in result.facts[0].evidence for fragment in evidence.fragments
        ]
        for event in modified["timeline"]:
            event["evidence"] = [cid for cid in event["evidence"] if cid != trace]
        without_trace = prepared.decode_output(json.dumps(modified))
        assert "Трасса: 189a5d15e84a4adb" not in [
            fragment
            for evidence in without_trace.facts[0].evidence
            for fragment in evidence.fragments
        ]
    else:
        assert [event.event_type for event in result.timeline] == [
            "customer_replied",
            "customer_withdrawal",
            "supplier_cancelled",
        ]
        receipt_rule = rules["event_roles"][0]
        receipt = next(e for e in modified["timeline"] if e["time"]["role"] == "received")
        refusal = receipt_rule["evidence"][1]
        receipt["evidence"].remove(refusal)
        borrowed = prepared.decode_output(json.dumps(modified))
        assert borrowed.timeline[1].event_type == "customer_withdrawal"
        assert len(borrowed.timeline[1].evidence[0].fragments) == 2
        fact = modified["facts"]["withdrawal_timing"]
        fact["evidence"].remove(refusal)
        without_refusal = prepared.decode_output(json.dumps(modified))
        assert without_refusal.timeline[1].event_type == "customer_message_received"

        for rule in rules["event_roles"]:
            for event in modified["timeline"]:
                if event["event_type"] == rule["from_type"]:
                    event["evidence"].remove(rule["evidence"][0])
        assert [
            event.event_type for event in prepared.decode_output(json.dumps(modified)).timeline
        ] == [
            "customer_withdrawal",
            "customer_message_received",
            "supplier_cancelled",
        ]


@pytest.mark.parametrize(
    ("bank_selected", "expected_issue"),
    [(True, "refund_event_evidence_missing"), (False, "reviewed_refund_sources_unavailable")],
)
def test_a_second_settlement_at_submission_time_never_counts_as_proof(
    seeded_profile, bank_selected, expected_issue
):
    _, snapshots = seeded_profile
    snapshot = snapshots["V03"]
    (archive,) = (ROOT / "evals/live").glob("v19-*")
    body = json.loads((archive / "full-V03/proposal.json").read_text(encoding="utf-8"))["body"]
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://offline/unused",
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="none",
        groq_prompt_profile="v3.3-citation-ids-test",
        groq_test_case_evidence=json.loads(
            (ROOT / "fixtures/live/groq-stage4-case-evidence.json").read_text(encoding="utf-8")
        ),
    )
    wrong_event = {**body["timeline"][1], "event_type": "refund_settled"}
    if bank_selected:
        body["timeline"].insert(2, wrong_event)
    else:
        snapshot = snapshot.model_copy(
            update={
                "catalog": tuple(
                    source.model_copy(
                        update={
                            "included": False,
                            "text": None,
                            "version_id": None,
                            "text_sha256": None,
                        }
                    )
                    if source.source_id == "649b9972ad454b5e"
                    else source
                    for source in snapshot.catalog
                )
            }
        )
        body.update(
            facts=[{"key": "payment_outcome", "value": "unconfirmed", "evidence": []}],
            readiness="request_evidence",
            follow_up={
                "action": "ask_user",
                "locator": None,
                "requirements": ["bank_settlement_document"],
            },
            timeline=body["timeline"][:2],
        )
        context = snapshot.to_validation_context()
        output = AssemblyOutput.model_validate(body)
        base = validate_assembly(output, context)
        refund = settings.groq_test_case_evidence["V03"]
        assert not check_case_evidence(output, context, base, refund).issues
        body["timeline"].append(wrong_event)
    context = snapshot.to_validation_context()
    output = AssemblyOutput.model_validate(body)
    base = validate_assembly(output, context)
    assert not base.issues
    result = check_case_evidence(output, context, base, settings.groq_test_case_evidence["V03"])
    assert [issue.code for issue in result.issues] == [expected_issue]
