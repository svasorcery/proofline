"""Strict operator commands. Omitted patch fields are distinct from explicit null."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator

from proofline.domain.assembly import AssemblyOutput, FactKey
from proofline.domain.inputs import Money, validate_storage_json
from proofline.domain.sources import SourceKind


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Value must contain non-whitespace text")
    return value


def _unique(value: list[FactKey]) -> list[FactKey]:
    if len(set(value)) != len(value):
        raise ValueError("Requested fact keys must be unique")
    return value


type Nonblank = Annotated[str, AfterValidator(_nonblank)]
type Title = Annotated[Nonblank, Field(max_length=200)]
type Locator = Annotated[Nonblank, Field(max_length=1000)]
type Trust = Annotated[Nonblank, Field(max_length=100)]
type Reason = Annotated[Nonblank, Field(max_length=2000)]
type FactKeys = Annotated[list[FactKey], AfterValidator(_unique)]


class _Command(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def finite_utf8_json(self) -> Self:
        # JSON-mode serialization can replace invalid dictionary keys. Validate
        # the original Python strings before any potentially lossy conversion.
        payload = self.model_dump(mode="python")
        validate_storage_json(payload)
        return self


class VersionCommand(_Command):
    expected_case_version: int = Field(gt=0)


class _PatchCommand(VersionCommand):
    @model_validator(mode="after")
    def mutation_required(self) -> Self:
        changed = self.model_fields_set - {"expected_case_version"}
        if not changed:
            raise ValueError("At least one mutation is required")
        for name in changed:
            if getattr(self, name) is None and not (
                isinstance(self, CasePatch) and name == "money"
            ):
                raise ValueError(f"Explicit null is not permitted for {name}")
        return self


class CaseCreate(_Command):
    title: Title
    user_text: Nonblank
    requested_fact_keys: FactKeys
    money: Money | None = None
    context: dict[str, JsonValue] = Field(default_factory=dict)


class CasePatch(_PatchCommand):
    title: Title | None = None
    user_text: Nonblank | None = None
    requested_fact_keys: FactKeys | None = None
    money: Money | None = None
    context: dict[str, JsonValue] | None = None


class SourceCreate(VersionCommand):
    kind: SourceKind
    locator: Locator
    title: Title
    trust: Trust = "synthetic"
    text: Nonblank | None = None
    included: bool | None = None


class SourcePatch(_PatchCommand):
    kind: SourceKind | None = None
    locator: Locator | None = None
    title: Title | None = None
    trust: Trust | None = None
    position: int | None = Field(default=None, ge=0)
    included: bool | None = None
    current_version_id: Nonblank | None = None


class SourceVersionCreate(VersionCommand):
    text: Nonblank


class RunCreate(VersionCommand):
    mode: Literal["replay", "live"]
    replay_id: Nonblank | None = None

    @model_validator(mode="after")
    def replay_selection(self) -> Self:
        if self.mode == "replay" and self.replay_id is None:
            raise ValueError("Replay mode requires replay_id")
        if self.mode == "live" and self.replay_id is not None:
            raise ValueError("Live mode does not accept replay_id")
        return self


class RevisionCreate(VersionCommand):
    body: AssemblyOutput
    reason: Reason


class ActivateProposal(VersionCommand):
    proposal_revision_id: Nonblank


class DecisionCreate(VersionCommand):
    kind: Literal["approve", "reject"]
    reason: Reason
