"""Trusted binding of an already supplied acceptance reply to the case snapshot."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from proofline.domain.inputs import AnalysisInput


class ReplyEventEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    sent_header: str
    reply_text: str
    receipt_record: str

    @field_validator("sent_header", "reply_text", "receipt_record")
    @classmethod
    def nonblank_anchor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An event needs an exact nonblank evidence fragment")
        return value


class IncludedAcceptanceReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    source_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fragment: str
    event_evidence: ReplyEventEvidence | None = None

    @field_validator("fragment")
    @classmethod
    def nonblank_fragment(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reviewed reply needs a literal fragment")
        return value

    @model_validator(mode="after")
    def reply_text_is_part_of_reviewed_reply(self) -> Self:
        marks = self.event_evidence
        if marks is not None and marks.reply_text not in self.fragment:
            raise ValueError("The event reply text must be part of the reviewed reply")
        return self

    def matches(self, snapshot: AnalysisInput) -> bool:
        source = next((s for s in snapshot.catalog if s.source_id == self.source_id), None)
        if (
            source is None
            or not source.included
            or source.version_id != self.source_version_id
            or source.text_sha256 != self.text_sha256
            or source.text is None
        ):
            return False
        start = source.text.find(self.fragment)
        return start >= 0 and source.text.find(self.fragment, start + 1) < 0
