import copy
from typing import Any

import pytest
from pydantic import ValidationError

from proofline.domain.assembly import AssemblyOutput
from proofline.domain.sources import SourceReference, SourceSnapshot, ValidationContext
from proofline.validation import validate_assembly


@pytest.fixture
def context() -> ValidationContext:
    reference = SourceReference(
        case_id="example", source_id="notice", kind="document", locator="document:notice"
    )
    snapshot = SourceSnapshot.from_text(
        reference, version_id="v1", text="😀 Notice\nCancelled at 2026-01-02T10:00:00+03:00."
    )
    missing = SourceReference(
        case_id="example", source_id="bank", kind="document", locator="document:bank"
    )
    return ValidationContext(
        case_id="example",
        requested_fact_keys=("cancellation_status",),
        sources=(snapshot,),
        catalog=(reference, missing),
    )


def validate(payload: dict[str, Any], context: ValidationContext) -> Any:
    return validate_assembly(AssemblyOutput.model_validate(payload), context)


def codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def test_success_binds_exact_version_hash_and_codepoint_spans_without_approval(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    before = copy.deepcopy(valid_payload)
    result = validate(valid_payload, context)
    assert result.state == "needs_operator_review"
    assert result.issues == ()
    span = result.fragments[0]
    assert span.source_id == "notice"
    assert span.source_version_id == "v1"
    assert span.source_sha256 == context.sources[0].text_sha256
    assert span.start_cp == 9
    assert context.sources[0].text[span.start_cp : span.end_cp] == span.fragment
    assert span.path == ("facts", 0, "evidence", 0, "fragments", 0)
    assert valid_payload == before


@pytest.mark.parametrize(
    "fragment,expected",
    [
        ("Notice Cancelled", "fragment_not_literal"),
        ("", "empty_fragment"),
        ("  ", "empty_fragment"),
        ("2026", ""),
    ],
)
def test_fragments_are_literal_and_nonempty(
    valid_payload: dict[str, Any], context: ValidationContext, fragment: str, expected: str
) -> None:
    valid_payload["facts"][0]["evidence"][0]["fragments"] = [fragment]
    result = validate(valid_payload, context)
    if expected:
        assert expected in codes(result)
        assert result.state == "blocked_for_approval"
    else:
        assert result.state == "needs_operator_review"


def test_ambiguous_and_overlapping_occurrences_block_approval(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    ref = context.catalog[0]
    source = SourceSnapshot.from_text(ref, version_id="v2", text="aaa")
    ctx = ValidationContext(
        case_id="example",
        requested_fact_keys=context.requested_fact_keys,
        sources=(source,),
        catalog=context.catalog,
    )
    valid_payload["facts"][0]["evidence"][0]["fragments"] = ["aa"]
    assert "ambiguous_fragment" in codes(validate(valid_payload, ctx))


def test_nonadjacent_fragments_are_resolved_separately(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    valid_payload["facts"][0]["evidence"][0]["fragments"] = ["Notice", "Cancelled"]
    result = validate(valid_payload, context)
    assert [s.fragment for s in result.fragments[:2]] == ["Notice", "Cancelled"]
    assert result.fragments[0].end_cp < result.fragments[1].start_cp


def test_unselected_source_cannot_supply_evidence(
    valid_payload: dict[str, Any], context: ValidationContext
) -> None:
    valid_payload["facts"][0]["evidence"][0]["source_id"] = "bank"
    result = validate(valid_payload, context)
    assert "source_not_visible" in codes(result)
    assert result.issues[0].message


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("case", "case_id_mismatch"),
        ("missing_fact", "fact_keys_mismatch"),
        ("duplicate_fact", "fact_keys_mismatch"),
        ("wrong_value", "invalid_fact_value"),
        ("no_evidence", "known_fact_without_evidence"),
        ("unconfirmed_ready", "premature_readiness"),
        ("no_followup", "missing_followup"),
        ("none_payload", "unexpected_followup_payload"),
        ("ask_locator", "ask_user_has_locator"),
        ("empty_requirements", "empty_requirements"),
        ("duplicate_requirements", "duplicate_requirements"),
        ("unauthorised_locator", "locator_not_authorized"),
        ("wrong_kind", "wrong_lookup_kind"),
        ("repeat_snapshot", "repeated_snapshot_lookup"),
    ],
)
def test_structural_and_consistency_failures_are_actionable(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    mutation: str,
    expected: str,
) -> None:
    follow = valid_payload["follow_up"]
    if mutation == "case":
        valid_payload["case_id"] = "another-case"
    elif mutation == "missing_fact":
        valid_payload["facts"] = []
    elif mutation == "duplicate_fact":
        valid_payload["facts"] *= 2
    elif mutation == "wrong_value":
        valid_payload["facts"][0]["value"] = "refund_settled"
    elif mutation == "no_evidence":
        valid_payload["facts"][0]["evidence"] = []
    elif mutation == "unconfirmed_ready":
        valid_payload["facts"][0]["value"] = "unconfirmed"
    elif mutation == "no_followup":
        valid_payload["readiness"] = "request_evidence"
    elif mutation == "none_payload":
        follow["requirements"] = ["quiet_room"]
    elif mutation == "ask_locator":
        follow.update(
            action="ask_user", locator="document:bank", requirements=["bank_settlement_document"]
        )
    elif mutation == "empty_requirements":
        follow["action"] = "ask_user"
    elif mutation == "duplicate_requirements":
        follow.update(action="ask_user", requirements=["quiet_room", "quiet_room"])
    elif mutation == "unauthorised_locator":
        follow.update(
            action="read_source",
            locator="https://evil.example",
            requirements=["bank_settlement_document"],
        )
    elif mutation == "wrong_kind":
        follow.update(
            action="query_payment",
            locator="document:bank",
            requirements=["bank_settlement_document"],
        )
    else:
        follow.update(action="read_source", locator="document:notice", requirements=["quiet_room"])
    result = validate(valid_payload, context)
    assert expected in codes(result)
    assert result.state == "blocked_for_approval"


@pytest.mark.parametrize(
    "value,role,expected",
    [
        ("not-a-time", "occurred", "invalid_or_naive_time"),
        ("2026-01-02T10:00:00", "occurred", "invalid_or_naive_time"),
        ("2026-01-02T07:00:00Z", "occurred", "time_not_in_fragments"),
        ("2026-01-02T10:00:00+03:00", "sent", "wrong_time_role"),
    ],
)
def test_time_needs_an_offset_literal_support_and_correct_role(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    value: str,
    role: str,
    expected: str,
) -> None:
    valid_payload["timeline"][0]["time"] = {"value": value, "role": role}
    assert expected in codes(validate(valid_payload, context))


def test_timeline_order_and_duplicates_use_instants_not_string_order(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    early = "2026-01-02T10:00:00+03:00"
    late = "2026-01-02T08:00:00Z"
    snapshot = SourceSnapshot.from_text(
        context.catalog[0], version_id="v2", text=f"{early}\n{late}"
    )
    ctx = ValidationContext(
        case_id="example",
        requested_fact_keys=("cancellation_status",),
        sources=(snapshot,),
        catalog=context.catalog,
    )
    valid_payload["facts"][0].update(value="unconfirmed", evidence=[])
    valid_payload["readiness"] = "human_review"

    def event(time: str) -> dict[str, Any]:
        return {
            "event_type": "supplier_cancelled",
            "time": {"value": time, "role": "occurred"},
            "evidence": [{"source_id": "notice", "fragments": [time]}],
        }

    valid_payload["timeline"] = [event(early), event(late)]
    assert validate(valid_payload, ctx).state == "needs_operator_review"
    valid_payload["timeline"].reverse()
    assert "timeline_not_sorted" in codes(validate(valid_payload, ctx))
    valid_payload["timeline"] = [event(early), event(early)]
    assert "duplicate_events" in codes(validate(valid_payload, ctx))


@pytest.mark.parametrize(
    "facts", [[], [{"key": "alternative_acceptance", "value": "conditional", "evidence": []}]]
)
def test_acceptance_event_requires_an_explicit_confirmed_fact(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    facts: list[Any],
) -> None:
    valid_payload["facts"] = facts
    valid_payload["timeline"][0]["event_type"] = "alternative_accepted"
    valid_payload["timeline"][0]["time"]["role"] = "received"
    assert "acceptance_fact_event_conflict" in codes(validate(valid_payload, context))


def test_request_for_known_but_missing_document_reaches_operator(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    valid_payload["readiness"] = "request_evidence"
    valid_payload["follow_up"] = {
        "action": "read_source",
        "locator": "document:bank",
        "requirements": ["bank_settlement_document"],
    }
    assert validate(valid_payload, context).state == "needs_operator_review"


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"])
def test_utc_overflow_is_a_blocking_issue_not_an_exception(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    value: str,
) -> None:
    valid_payload["timeline"][0]["time"]["value"] = value
    assert "invalid_or_naive_time" in codes(validate(valid_payload, context))


def test_validation_rechecks_mutated_dto_collections(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    output = AssemblyOutput.model_validate(valid_payload)
    output.facts.append({"key": "invented"})
    with pytest.raises(ValidationError):
        validate_assembly(output, context)


def test_validation_rechecks_bypassed_snapshot_construction(
    valid_payload: dict[str, Any],
    context: ValidationContext,
) -> None:
    forged = context.sources[0].model_copy(update={"text": "changed after hashing"})
    forged_context = context.model_copy(update={"sources": (forged,)})
    with pytest.raises(ValidationError, match="SHA256"):
        validate(valid_payload, forged_context)


@pytest.mark.parametrize(
    "fragment",
    [
        "2026-01-02T10:00:00+03:30",
        "2026-01-02T10:00:00+03",
    ],
)
def test_truncated_timestamp_cannot_change_the_instant(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    fragment: str,
) -> None:
    text = "Received: 2026-01-02T10:00:00+03:30."
    source = SourceSnapshot.from_text(context.catalog[0], version_id="v2", text=text)
    ctx = ValidationContext(
        case_id="example",
        requested_fact_keys=context.requested_fact_keys,
        catalog=context.catalog,
        sources=(source,),
    )
    valid_payload["facts"][0].update(value="unconfirmed", evidence=[])
    valid_payload["readiness"] = "human_review"
    valid_payload["timeline"][0].update(
        time={"value": "2026-01-02T10:00:00+03", "role": "occurred"},
        evidence=[{"source_id": "notice", "fragments": [fragment]}],
    )
    assert "time_not_in_fragments" in codes(validate(valid_payload, ctx))


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-02T10:00:00+03:99",
        "2026-01-02X10:00:00+03:00",
        "2026-01-02T10:00:00.1234567Z",
        "2026-01-02T10:00:00+24:00",
    ],
)
def test_timestamp_components_are_not_silently_corrected_or_truncated(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    value: str,
) -> None:
    source = SourceSnapshot.from_text(context.catalog[0], version_id="v2", text=f"Time: {value}.")
    ctx = ValidationContext(
        case_id="example",
        requested_fact_keys=context.requested_fact_keys,
        catalog=context.catalog,
        sources=(source,),
    )
    valid_payload["facts"][0].update(value="unconfirmed", evidence=[])
    valid_payload["readiness"] = "human_review"
    valid_payload["timeline"][0].update(
        time={"value": value, "role": "occurred"},
        evidence=[{"source_id": "notice", "fragments": [value]}],
    )
    assert "invalid_or_naive_time" in codes(validate(valid_payload, ctx))


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-02T10:00:00+03:30",
        "2026-01-02T10:00:00+03",
        "2026-01-02T10:00:00.123456Z",
        "2026-01-02T10:00Z",
    ],
)
def test_complete_supported_timestamp_preserves_the_original_spelling(
    valid_payload: dict[str, Any],
    context: ValidationContext,
    value: str,
) -> None:
    source = SourceSnapshot.from_text(context.catalog[0], version_id="v2", text=f"Time: {value}.")
    ctx = ValidationContext(
        case_id="example",
        requested_fact_keys=context.requested_fact_keys,
        catalog=context.catalog,
        sources=(source,),
    )
    valid_payload["facts"][0].update(value="unconfirmed", evidence=[])
    valid_payload["readiness"] = "human_review"
    valid_payload["timeline"][0].update(
        time={"value": value, "role": "occurred"},
        evidence=[{"source_id": "notice", "fragments": [value]}],
    )
    model = AssemblyOutput.model_validate(valid_payload)
    assert validate_assembly(model, ctx).state == "needs_operator_review"
    assert model.timeline[0].time.value == value
