"""Reviewed timeline marks for a particular selected source version."""

from pydantic import BaseModel, ConfigDict, Field

from proofline.domain.assembly import EventTime, EventType, TimeRole
from proofline.domain.inputs import SnapshotSource


class RequiredEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    event_type: EventType
    time: EventTime
    source_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fragment: str = Field(min_length=1)

    @property
    def identity(self) -> tuple[EventType, TimeRole, str]:
        return self.event_type, self.time.role, self.time.value

    def matches(self, source: SnapshotSource | None) -> bool:
        if (
            source is None
            or not source.included
            or source.source_id != self.source_id
            or source.version_id != self.source_version_id
            or source.text_sha256 != self.text_sha256
            or source.text is None
            or not self.time.value
            or self.time.value not in self.fragment
        ):
            return False
        start = source.text.find(self.fragment)
        return start >= 0 and source.text.find(self.fragment, start + 1) < 0
