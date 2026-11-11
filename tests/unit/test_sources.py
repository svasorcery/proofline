import hashlib

import pytest
from pydantic import ValidationError

from proofline.domain.sources import SourceReference, SourceSnapshot, ValidationContext


def reference(case_id: str = "case") -> SourceReference:
    return SourceReference(
        case_id=case_id, source_id="source", kind="document", locator="document:1"
    )


def test_source_creation_normalises_only_line_endings() -> None:
    snapshot = SourceSnapshot.from_text(reference(), version_id="version:1", text="😀 a\r\nb\rc  ")
    assert snapshot.text == "😀 a\nb\nc  "
    assert snapshot.text_sha256 == hashlib.sha256("😀 a\nb\nc  ".encode()).hexdigest()
    with pytest.raises(ValidationError):
        snapshot.text = "edited"


@pytest.mark.parametrize(
    "field,value",
    [
        ("text_sha256", "0" * 64),
        ("text", "changed"),
        ("version_id", ""),
    ],
)
def test_forged_or_incomplete_source_versions_are_rejected(field: str, value: str) -> None:
    snapshot = SourceSnapshot.from_text(reference(), version_id="version:1", text="original")
    payload = snapshot.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(payload)


def test_already_stored_text_must_have_normalised_line_endings() -> None:
    payload = SourceSnapshot.from_text(reference(), version_id="v1", text="a\nb").model_dump()
    payload["text"] = "a\r\nb"
    payload["text_sha256"] = hashlib.sha256(b"a\r\nb").hexdigest()
    with pytest.raises(ValidationError, match="normalised"):
        SourceSnapshot.model_validate(payload)


def test_distinct_source_ids_cannot_share_an_authorised_locator() -> None:
    first = reference()
    second = SourceReference(
        case_id="case", source_id="second", kind="document", locator=first.locator
    )
    with pytest.raises(ValidationError, match="locators must be unique"):
        ValidationContext(
            case_id="case", requested_fact_keys=(), sources=(), catalog=(first, second)
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "different_case",
        "duplicate_source",
        "duplicate_locator",
        "missing_catalog",
        "different_metadata",
        "duplicate_fact",
    ],
)
def test_context_rejects_ambiguous_or_cross_case_bindings(mutation: str) -> None:
    ref = reference()
    snapshot = SourceSnapshot.from_text(ref, version_id="v1", text="evidence")
    payload = {
        "case_id": "case",
        "requested_fact_keys": ("cancellation_status",),
        "sources": (snapshot,),
        "catalog": (ref,),
    }
    if mutation == "different_case":
        payload["case_id"] = "other-case"
    elif mutation == "duplicate_source":
        payload["sources"] = (snapshot, snapshot)
    elif mutation == "duplicate_locator":
        payload["catalog"] = (ref, ref)
    elif mutation == "missing_catalog":
        payload["catalog"] = ()
    elif mutation == "different_metadata":
        payload["catalog"] = (ref.model_copy(update={"kind": "provider_log"}),)
    else:
        payload["requested_fact_keys"] = ("cancellation_status", "cancellation_status")
    with pytest.raises(ValidationError):
        ValidationContext.model_validate(payload)
