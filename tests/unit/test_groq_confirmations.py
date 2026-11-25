"""Compare the isolated profile's actual SDK requests against archived cases."""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from proofline.application.state import record_validation
from proofline.config import Settings
from proofline.domain.assembly import TimelineEvent
from proofline.domain.inputs import AnalysisInput, json_digest
from proofline.persistence.models import ProposalRevision
from proofline.providers.factory import create_live_provider
from proofline.validation.assembly import validate_assembly

ROOT = Path(__file__).resolve().parents[2]
V04 = ROOT / "evals/live/v9-groq-qwen-choices-v04-2026-11-18/full-V04"
V04_CONFIRMATIONS = ROOT / "evals/live/v10-groq-qwen-confirmations-v04-2026-11-18/full-V04"
V02_PRESENT = ROOT / "evals/live/v8-groq-qwen-choices-v02-v04-2026-11-17/full-V02"
V02_EMPTY = ROOT / "evals/live/v13-groq-qwen-citation-ids-v02-2026-11-18/full-V02"
V01 = ROOT / "evals/live/v7-groq-qwen-choices-2026-11-17/ablation-V01"
V01_WRONG_ROLE = ROOT / "evals/live/v17-groq-qwen-lookup-example-v01-2026-11-18/ablation-V01"
V06_REVERSE = ROOT / "evals/live/v21-groq-qwen-timeline-v06-2026-11-18/full-V06"
V06_HOLD_OPENING = ROOT / "evals/live/v22-groq-qwen-timeline-order-v06-2026-11-18/full-V06"
ORIGINAL = "original_acceptance_reply"
SENT_HEADER = "Заголовок исходного письма Date: 2026-10-08T12:25:00+03:00"
REPLY_ANCHOR = "Если там тихий номер с окнами во двор, согласен на соседний корпус."
RECEIVED_AT = "2026-10-08T12:27:00+03:00"
RECEIPT = f"{RECEIVED_AT} | inbound-message | клиент → отель"
REVIEWED_REPLY = (
    "Текст принятого сообщения:\n"
    "«Если там тихий номер с окнами во двор, согласен на соседний корпус. "
    "Если окна на улицу, этот вариант мне не подходит. "
    "Подтвердите, пожалуйста, куда выходят окна.»"
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def settings(**updates):
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        llm_api_key=SecretStr("offline-confirmations"),
        **updates,
    )


def archive(folder):
    run = read(folder / "run.json")
    return AnalysisInput.model_validate_json(json.dumps(run["snapshot"])), run["raw_output"]


def v04_binding():
    case, raw = archive(V04)
    source = next(source for source in case.catalog if source.source_id == "f71822cddbf648f2")
    return (
        case,
        raw,
        {
            "source_id": source.source_id,
            "source_version_id": source.version_id,
            "text_sha256": source.text_sha256,
            "fragment": REVIEWED_REPLY,
        },
    )


def sdk_exchange(case, raw, **updates):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_confirmations",
                "object": "chat.completion",
                "model": "qwen/qwen3.8-27b",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": raw},
                    }
                ],
            },
        )

    provider = create_live_provider(settings(**updates), transport=httpx.MockTransport(handle))
    prepared = provider.prepare(case)
    result = provider.generate(prepared)
    assert calls == [prepared.request] and result.raw_output == raw and result.error is None
    assert prepared.decode_output is not None
    return calls[0], prepared, result


def allowed(request):
    return request["response_format"]["json_schema"]["schema"]["$defs"]["RequirementKey"]["enum"]


def assert_only_prompt_example_added(request, previous):
    instructions = request["messages"][0]["content"]
    previous_instructions = previous["messages"][0]["content"]
    assert instructions.startswith(previous_instructions)
    example = instructions.removeprefix(previous_instructions)
    assert example.startswith("\n\nSingle-document timeline example.")
    assert request == {
        **previous,
        "messages": [
            {**previous["messages"][0], "content": instructions},
            *previous["messages"][1:],
        ],
    }
    return example


def test_v04_confirmation_is_unavailable_and_decoder_preserves_other_questions():
    case, raw, binding = v04_binding()
    request, prepared, result = sdk_exchange(
        case,
        raw,
        groq_prompt_profile="v3.3-confirmations-test",
        groq_test_acceptance_replies={"V04": binding},
    )
    previous = read(V04 / "request.json")
    allowed(previous).remove(ORIGINAL)
    assert_only_prompt_example_added(request, previous)
    profile = result.generation_profile
    assert profile["provider_covered_requirements"] == [ORIGINAL]
    assert profile["request_profile"] == "groq-chat_completions-v33-confirmations-test-v1"
    with pytest.raises(ValueError, match="already included"):
        prepared.decode_output(raw)
    corrected = json.loads(raw)
    corrected["follow_up"]["requirements"].remove(ORIGINAL)
    body = prepared.decode_output(json.dumps(corrected))
    assert body.follow_up.requirements == ["quiet_room", "courtyard_windows"]
    assert body.readiness == "request_evidence"
    assert not validate_assembly(body, case.to_validation_context()).issues


def test_v01_can_still_request_the_unread_original():
    case, raw = archive(V01)
    _, _, unrelated = v04_binding()
    request, prepared, result = sdk_exchange(
        case,
        raw,
        groq_prompt_profile="v3.3-confirmations-test",
        groq_test_acceptance_replies={"V04": unrelated},
        groq_test_lookup_sources={"V01": ("f9a88d5b726a4d4b",)},
    )
    assert_only_prompt_example_added(request, read(V01 / "request.json"))
    assert result.generation_profile["provider_covered_requirements"] == []
    body = prepared.decode_output(raw)
    assert body.follow_up.model_dump() == {
        "action": "read_source",
        "locator": "1618ae5e5b894145",
        "requirements": [ORIGINAL],
    }


@pytest.mark.parametrize("changed", ["excluded", "source_version_id", "text_sha256", "fragment"])
def test_stale_or_absent_evidence_does_not_hide_the_question(changed):
    case, raw, binding = v04_binding()
    if changed == "excluded":
        dump = case.model_dump(mode="json")
        source = next(s for s in dump["catalog"] if s["source_id"] == binding["source_id"])
        source.update(included=False, text=None, version_id=None, text_sha256=None)
        case = AnalysisInput.model_validate_json(json.dumps(dump))
    else:
        binding[changed] = "0" * 64 if changed == "text_sha256" else "mismatch"
    request, prepared, result = sdk_exchange(
        case,
        raw,
        groq_prompt_profile="v3.3-confirmations-test",
        groq_test_acceptance_replies={"V04": binding},
    )
    assert ORIGINAL in allowed(request)
    assert result.generation_profile["provider_covered_requirements"] == []
    assert ORIGINAL in prepared.decode_output(raw).follow_up.requirements


def test_confirmation_binding_cannot_affect_previous_profiles():
    case, raw, binding = v04_binding()
    with pytest.raises(ValidationError, match="GROQ_TEST_ACCEPTANCE_REPLIES"):
        settings(
            groq_prompt_profile="v3.3-choices-test", groq_test_acceptance_replies={"V04": binding}
        )
    request, _, result = sdk_exchange(case, raw, groq_prompt_profile="v3.3-choices-test")
    assert request == read(V04 / "request.json")
    assert "provider_covered_requirements" not in result.generation_profile


def event_binding(folder):
    case, _ = archive(folder)
    source = next(item for item in case.catalog if item.source_id == "f71822cddbf648f2")
    return {
        "source_id": source.source_id,
        "source_version_id": source.version_id,
        "text_sha256": source.text_sha256,
        "fragment": REVIEWED_REPLY,
        "event_evidence": {
            "sent_header": SENT_HEADER,
            "reply_text": REPLY_ANCHOR,
            "receipt_record": RECEIPT,
        },
    }


@pytest.mark.parametrize(
    ("folder", "variant", "expected"),
    [
        (V04, "archived", []),
        (
            V04_CONFIRMATIONS,
            "archived",
            [("reply_text_missing_from_send", ["timeline", 1, "evidence"])],
        ),
        (
            V04,
            "receipt-time-only",
            [("receipt_record_missing_from_receipt", ["timeline", 2, "evidence"])],
        ),
        (V04_CONFIRMATIONS, "corrected", []),
        (V04_CONFIRMATIONS, "stale-binding", []),
        (V04_CONFIRMATIONS, "no-anchors", []),
        (V04_CONFIRMATIONS, "v32", []),
    ],
)
def test_v04_event_citations_are_checked_only_with_current_test_profile_marks(
    folder, variant, expected
):
    archived_card = read(folder / "proposal.json")
    body = archived_card["body"]
    if variant == "receipt-time-only":
        body["timeline"][2]["evidence"][0]["fragments"][0] = RECEIVED_AT
    if variant == "corrected":
        body["timeline"][1]["evidence"][0]["fragments"].append(REPLY_ANCHOR)
    binding = event_binding(folder)
    if variant == "stale-binding":
        binding["source_version_id"] = "superseded"
    if variant == "no-anchors":
        del binding["event_evidence"]
    config = (
        settings()
        if variant == "v32"
        else settings(
            groq_prompt_profile="v3.3-confirmations-test",
            groq_test_acceptance_replies={"V04": binding},
        )
    )
    proposal = ProposalRevision(
        id="offline", case_id="V04", snapshot=archived_card["snapshot"], body=body
    )
    session = Mock()
    check = record_validation(session, proposal, config)
    assert [(issue["code"], issue["path"]) for issue in check.issues] == expected
    assert check.result == ("blocked_for_approval" if expected else "needs_operator_review")
    assert check.profile_version == config.effective_validation_profile
    assert (check.profile_version != config.validation_profile) == (
        variant not in {"v32", "no-anchors"}
    )
    session.add.assert_called_once_with(check)


def test_v04_sdk_request_adds_a_literal_single_document_example_only():
    case, raw = archive(V04_CONFIRMATIONS)
    binding = event_binding(V04_CONFIRMATIONS)
    request, _, response = sdk_exchange(
        case,
        raw,
        groq_prompt_profile="v3.3-confirmations-test",
        groq_test_acceptance_replies={"V04": binding},
    )
    example = assert_only_prompt_example_added(request, read(V04_CONFIRMATIONS / "request.json"))
    _, source_label, source_and_example = example.partition("\nIncluded source correspondence-12\n")
    assert source_label
    source_text, timeline_label, timeline = source_and_example.partition("\n\nTimeline entries\n")
    assert timeline_label
    sent, received = TypeAdapter(list[TimelineEvent]).validate_json(timeline)
    assert (sent.event_type, sent.time.role) == ("customer_replied", "sent")
    assert (received.event_type, received.time.role) == ("customer_message_received", "received")
    (sent_evidence,) = sent.evidence
    (received_evidence,) = received.evidence
    assert sent_evidence.source_id == received_evidence.source_id == "correspondence-12"
    sent_header, reply_text = sent_evidence.fragments
    (receipt_record,) = received_evidence.fragments
    assert sent_header == f"Original email Date: {sent.time.value}"
    assert reply_text == "I can take the earlier shuttle if my bicycle fits."
    assert receipt_record == f"{received.time.value} | inbound-message | customer → hotel"
    assert all(source_text.count(quote) == 1 for quote in (sent_header, reply_text, receipt_record))
    assert sent.time.value < received.time.value

    profile = response.generation_profile
    historical = read(V04_CONFIRMATIONS / "run.json")["generation_profile"]
    instructions = request["messages"][0]["content"]
    assert profile["prompt_version"] != historical["prompt_version"]
    assert profile["prompt_sha256"] == hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    assert profile["request_sha256"] == json_digest(request)
    for key in ("policy_sha256", "schema_sha256", "assembly_schema_sha256", "request_profile"):
        assert profile[key] == historical[key]
    assert profile["provider_reply_evidence"] == {
        key: binding[key] for key in ("source_id", "source_version_id", "text_sha256")
    }


def test_citation_ids_are_scoped_to_the_snapshot_and_unread_sources_have_no_ids():
    case, raw = archive(V04_CONFIRMATIONS)
    hidden_id = case.catalog[0].source_id
    provider = create_live_provider(
        settings(
            groq_prompt_profile="v3.3-citation-ids-test",
            groq_test_lookup_sources={"V04": (hidden_id,)},
        )
    )
    original = provider.prepare(case)
    cid = json.loads(original.request["messages"][1]["content"])["catalog"][0]["citation_lines"][0][
        "citation_id"
    ]
    changed = case.model_dump(mode="json")
    changed["catalog"][0].update(included=False, text=None, text_sha256=None, version_id=None)
    snapshot = AnalysisInput.model_validate_json(json.dumps(changed))
    prepared = provider.prepare(snapshot)
    user_content = json.loads(prepared.request["messages"][1]["content"])
    assert prepared.generation_profile["prompt_version"].endswith("-lookup-example-v1")
    assert "REQUESTING AN UNREAD ORIGINAL" in prepared.request["messages"][0]["content"]
    unread = user_content["catalog"][0]
    assert unread["source_id"] == hidden_id and unread["text"] is None
    assert "citation_lines" not in unread
    assert prepared.generation_profile["provider_lookup_source_ids"] == [hidden_id]

    answer = json.loads(raw)
    for item in [*answer["facts"].values(), *answer["timeline"]]:
        item["evidence"] = []
    answer["facts"]["alternative_acceptance"]["evidence"] = [cid]
    assert prepared.decode_output is not None
    with pytest.raises(ValueError, match="Unknown citation ID"):
        prepared.decode_output(json.dumps(answer))


def test_empty_citation_catalogue_only_allows_empty_evidence_arrays():
    case, raw = archive(V04_CONFIRMATIONS)
    data = case.model_dump(mode="json")
    for source in data["catalog"]:
        source.update(included=False, text=None, text_sha256=None, version_id=None)
    snapshot = AnalysisInput.model_validate_json(json.dumps(data))
    provider = create_live_provider(settings(groq_prompt_profile="v3.3-citation-ids-test"))
    prepared = provider.prepare(snapshot)
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    for name in ("FactWithCitationIds", "TimelineEventWithCitationIds"):
        assert schema["$defs"][name]["properties"]["evidence"]["maxItems"] == 0
    assert prepared.generation_profile["provider_citation_count"] == 0
    answer = json.loads(raw)
    for item in [*answer["facts"].values(), *answer["timeline"]]:
        item["evidence"] = []
    assert prepared.decode_output is not None
    card = prepared.decode_output(json.dumps(answer))
    result = validate_assembly(card, snapshot.to_validation_context())
    assert result.state == "blocked_for_approval"
    answer["facts"]["alternative_acceptance"]["evidence"] = ["q-unknown"]
    with pytest.raises(ValidationError):
        prepared.decode_output(json.dumps(answer))


@pytest.mark.parametrize(
    ("times", "order"),
    [
        (None, ("supplier_cancelled", "authorization_released")),
        (
            ("2026-06-22T16:43:00+05:00", "2026-06-22T16:40:00+03:00"),
            ("authorization_released", "supplier_cancelled"),
        ),
        (
            ("2026-06-22T16:43:00+03:00", "2026-06-22T13:43:00Z"),
            ("authorization_released", "supplier_cancelled"),
        ),
        (
            ("2026-06-22T16:43:00+03:00", "invalid"),
            ("authorization_released", "supplier_cancelled"),
        ),
    ],
)
def test_citation_profile_normalizes_chronology_only_when_times_parse(times, order):
    snapshot, raw = archive(V06_REVERSE)
    original = json.loads(raw)
    if times is not None:
        for event, value in zip(original["timeline"], times, strict=True):
            event["time"]["value"] = value
        raw = json.dumps(original)
    _, prepared, response = sdk_exchange(
        snapshot, raw, groq_prompt_profile="v3.3-citation-ids-test"
    )
    assert response.raw_output == raw
    card = prepared.decode_output(response.raw_output)
    assert tuple(event.event_type for event in card.timeline) == order
    assert {event.event_type: event.time.value for event in card.timeline} == {
        event["event_type"]: event["time"]["value"] for event in original["timeline"]
    }
    assert response.generation_profile["canonical_timeline_order"] == "utc-v1"
    result = validate_assembly(card, snapshot.to_validation_context())
    if times is None:
        assert not result.issues
    if times is not None and "invalid" in times:
        assert "invalid_or_naive_time" in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    ("folder", "reviewed", "expected"),
    [
        (V06_HOLD_OPENING, True, ["authorization_open_used_as_release"]),
        (V06_HOLD_OPENING, False, []),
        (V06_REVERSE, True, []),
    ],
)
def test_reviewed_ledger_opening_does_not_establish_release(folder, reviewed, expected):
    snapshot, raw = archive(folder)
    marks = read(folder.parent / "preflight.json")["reviewed_events"] if reviewed else None
    config = settings(groq_prompt_profile="v3.3-citation-ids-test", groq_test_required_events=marks)
    prepared = create_live_provider(config).prepare(snapshot)
    card = prepared.decode_output(raw)
    proposal = ProposalRevision(
        id="offline",
        case_id="V06",
        snapshot=snapshot.model_dump(mode="json"),
        body=card.model_dump(mode="json"),
    )
    check = record_validation(Mock(), proposal, config)
    assert [issue["code"] for issue in check.issues] == expected
    assert check.result == ("blocked_for_approval" if expected else "needs_operator_review")
    assert prepared.generation_profile["prompt_version"].endswith("-bank-operations-v1") is reviewed


def test_one_groq_instance_preserves_case_specific_prompts():
    v01 = ROOT / "evals/live/v18-groq-qwen-receipt-role-v01-2026-11-18/ablation-V01"
    v03 = ROOT / "evals/live/v19-groq-qwen-citation-ids-v03-v05-v06-2026-11-18/full-V03"
    v06 = ROOT / "evals/live/v23-groq-qwen-bank-operations-v06-2026-11-18/full-V06"
    config = settings(
        groq_prompt_profile="v3.3-citation-ids-test",
        groq_test_lookup_sources={"V01": ("f9a88d5b726a4d4b",)},
        groq_test_required_events={
            "V01": [read(v01.parent / "preflight.json")["reviewed_receipt"]],
            **read(v06.parent / "preflight.json")["reviewed_events"],
        },
    )
    provider = create_live_provider(config)
    for folder in (v01, v03, v06):
        snapshot, _ = archive(folder)
        prepared = provider.prepare(snapshot)
        original = read(folder / "request.json")
        assert prepared.request["messages"][0] == original["messages"][0]
        profile = read(folder / "run.json")["generation_profile"]
        assert prepared.generation_profile["prompt_sha256"] == profile["prompt_sha256"]


@pytest.mark.parametrize(
    ("folder", "stale", "reviewed_count", "missing"),
    [(V02_PRESENT, False, 3, False), (V02_EMPTY, False, 3, True), (V02_EMPTY, True, 0, False)],
)
def test_required_events_use_only_source_backed_marks(folder, stale, reviewed_count, missing):
    card = read(folder / "proposal.json")
    snapshot, _ = archive(folder)
    sources = {source.source_id: source.version() for source in snapshot.catalog}
    marks = []
    for event_type, role, time_value, source_id, line in [
        ("supplier_cancelled", "occurred", "2026-09-18T10:20:00+03:00", "9edd7227853c42a4", 0),
        ("customer_replied", "sent", "2026-09-18T12:09:55+03:00", "5c3fe2fc12a3430e", 1),
        (
            "customer_message_received",
            "received",
            "2026-09-18T12:10:00+03:00",
            "5c3fe2fc12a3430e",
            2,
        ),
    ]:
        source = sources[source_id]
        marks.append(
            {
                "event_type": event_type,
                "time": {"role": role, "value": time_value},
                "source_id": source_id,
                "source_version_id": "old" if stale else source.version_id,
                "text_sha256": source.text_sha256,
                "fragment": source.text.split("\n")[line],
            }
        )
    config = settings(
        groq_prompt_profile="v3.3-citation-ids-test", groq_test_required_events={"V02": marks}
    )
    prepared = create_live_provider(config).prepare(snapshot)
    schema = prepared.request["response_format"]["json_schema"]["schema"]
    assert "minItems" not in schema["properties"]["timeline"]
    assert prepared.generation_profile.get("reviewed_timeline_events", 0) == reviewed_count
    assert prepared.generation_profile["prompt_version"].endswith("-timeline-example-v1") == bool(
        reviewed_count
    )
    assert ("TIMELINE WITH CITATION IDS" in prepared.request["messages"][0]["content"]) == bool(
        reviewed_count
    )
    other, _ = archive(V04_CONFIRMATIONS)
    other_request = create_live_provider(config).prepare(other).request
    assert (
        "minItems"
        not in other_request["response_format"]["json_schema"]["schema"]["properties"]["timeline"]
    )
    proposal = ProposalRevision(
        id="offline", case_id="V02", snapshot=card["snapshot"], body=card["body"]
    )
    check = record_validation(Mock(), proposal, config)
    assert (
        "required_timeline_event_missing" in {issue["code"] for issue in check.issues}
    ) is missing
    if missing:
        assert check.result == "blocked_for_approval"


@pytest.mark.parametrize(
    ("folder", "independent_send", "expected"),
    [(V01, False, False), (V01_WRONG_ROLE, False, True), (V01_WRONG_ROLE, True, False)],
)
def test_receipt_time_is_not_sending_evidence(folder, independent_send, expected):
    card = read(folder / "proposal.json")
    source_id = "b8821b7c6a2d432a"
    source = next(item for item in card["snapshot"]["catalog"] if item["source_id"] == source_id)
    time_value = "2026-08-14T09:36:00+03:00"
    receipt = source["text"].split("\n", 1)[0]
    if independent_send:
        header = f"Отправлено исходное письмо: {time_value}"
        source["text"] += "\n" + header
        source["text_sha256"] = hashlib.sha256(source["text"].encode()).hexdigest()
        source["version_id"] += "-with-independent-send"
        sent = next(
            event
            for event in card["body"]["timeline"]
            if event["time"]["role"] == "sent" and event["event_type"] == "customer_replied"
        )
        sent["evidence"][0]["fragments"] = [
            receipt + "\n" + "\n".join(source["text"].split("\n")[1:])
        ]
    config = settings(
        groq_prompt_profile="v3.3-citation-ids-test",
        groq_test_lookup_sources={"V01": ("f9a88d5b726a4d4b",)},
        groq_test_required_events={
            "V01": [
                {
                    "event_type": "customer_message_received",
                    "time": {"role": "received", "value": time_value},
                    "source_id": source_id,
                    "source_version_id": source["version_id"],
                    "text_sha256": source["text_sha256"],
                    "fragment": receipt,
                }
            ]
        },
    )
    proposal = ProposalRevision(
        id="offline", case_id="V01", snapshot=card["snapshot"], body=card["body"]
    )
    check = record_validation(Mock(), proposal, config)
    assert ("receipt_time_used_as_sending" in {issue["code"] for issue in check.issues}) is expected
    if expected:
        assert check.result == "blocked_for_approval"
