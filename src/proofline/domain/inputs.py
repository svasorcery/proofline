"""Application input snapshots; replay identity is separate from database bindings."""

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from proofline.domain.assembly import FactKey
from proofline.domain.sources import SourceReference, SourceSnapshot, ValidationContext


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def json_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_storage_json(value: JsonValue) -> None:
    """Reject non-finite JSON, invalid UTF-8 and PostgreSQL's forbidden NUL."""

    def visit(item: JsonValue) -> None:
        if isinstance(item, str) and "\u0000" in item:
            raise ValueError("NUL characters cannot be stored in PostgreSQL text or JSON")
        if isinstance(item, dict):
            for key, child in item.items():
                visit(key)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    canonical_json(value).encode("utf-8")


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    amount_minor: int
    currency: Literal["USD"]
    scale: int = Field(ge=2, le=2)


class SnapshotSource(SourceReference):
    title: str
    trust: str
    position: int = Field(ge=0)
    included: bool
    version_id: str | None
    text: str | None
    text_sha256: str | None

    def reference(self) -> SourceReference:
        return SourceReference(
            case_id=self.case_id, source_id=self.source_id, kind=self.kind, locator=self.locator
        )

    def version(self) -> SourceSnapshot:
        return SourceSnapshot.model_validate(
            {
                "case_id": self.case_id,
                "source_id": self.source_id,
                "kind": self.kind,
                "locator": self.locator,
                "version_id": self.version_id,
                "text": self.text,
                "text_sha256": self.text_sha256,
            }
        )

    @model_validator(mode="after")
    def selected_text_only(self) -> Self:
        if self.included:
            self.version()
        elif any(value is not None for value in (self.version_id, self.text, self.text_sha256)):
            raise ValueError(
                "Unselected source text and version bindings must not enter model input"
            )
        return self


class AnalysisInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    case_id: str
    user_text: str
    requested_fact_keys: tuple[FactKey, ...]
    money: Money | None
    context: dict[str, JsonValue]
    catalog: tuple[SnapshotSource, ...]

    def to_validation_context(self) -> ValidationContext:
        return ValidationContext(
            case_id=self.case_id,
            requested_fact_keys=self.requested_fact_keys,
            sources=tuple(source.version() for source in self.catalog if source.included),
            catalog=tuple(source.reference() for source in self.catalog),
        )

    @model_validator(mode="after")
    def valid_snapshot(self) -> Self:
        self.to_validation_context()
        if [source.position for source in self.catalog] != list(range(len(self.catalog))):
            raise ValueError("Snapshot catalogue positions must be contiguous and ordered")
        canonical_json(self.context).encode("utf-8")
        return self


def compatibility_digest(value: AnalysisInput) -> str:
    payload = value.model_dump(mode="json")
    for source in payload["catalog"]:
        del source["version_id"]
        del source["text_sha256"]
    return json_digest(payload)


def source_bindings(value: AnalysisInput) -> dict[str, JsonValue]:
    return {
        source.source_id: {"version_id": source.version_id, "text_sha256": source.text_sha256}
        for source in value.catalog
        if source.included
    }
