"""Immutable validation inputs bound to a case and a concrete source version."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proofline.domain.assembly import FactKey

type SourceKind = Literal["document", "provider_log", "payment_ledger"]


class SourceReference(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    case_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    kind: SourceKind
    locator: str = Field(min_length=1)


class SourceSnapshot(SourceReference):
    version_id: str = Field(min_length=1)
    text: str
    text_sha256: str

    @classmethod
    def from_text(cls, source: SourceReference, *, version_id: str, text: str) -> Self:
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        return cls(
            case_id=source.case_id,
            source_id=source.source_id,
            kind=source.kind,
            locator=source.locator,
            version_id=version_id,
            text=normalised,
            text_sha256=hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        )

    @model_validator(mode="after")
    def check_stored_text(self) -> Self:
        if "\r" in self.text:
            raise ValueError("Stored source text must have normalised LF line endings")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("Source text does not match its SHA256")
        return self


class ValidationContext(BaseModel):
    """The server's selected versions and visible, authorised source catalogue."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    case_id: str = Field(min_length=1)
    requested_fact_keys: tuple[FactKey, ...]
    sources: tuple[SourceSnapshot, ...]
    catalog: tuple[SourceReference, ...]

    @model_validator(mode="after")
    def check_bindings(self) -> Self:
        if len(set(self.requested_fact_keys)) != len(self.requested_fact_keys):
            raise ValueError("Requested fact keys must be unique")
        if any(source.case_id != self.case_id for source in (*self.sources, *self.catalog)):
            raise ValueError("Every source must belong to the context case")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("Select exactly one version per source")
        references = {source.source_id: source for source in self.catalog}
        if len(references) != len(self.catalog):
            raise ValueError("Source catalogue IDs must be unique")
        if len({source.locator for source in self.catalog}) != len(self.catalog):
            raise ValueError("Source catalogue locators must be unique")
        for source in self.sources:
            reference = references.get(source.source_id)
            if reference is None or (reference.kind, reference.locator) != (
                source.kind,
                source.locator,
            ):
                raise ValueError("Selected source metadata must match the catalogue")
        return self
