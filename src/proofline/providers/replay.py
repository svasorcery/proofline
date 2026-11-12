import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from proofline.application.errors import ApplicationError
from proofline.domain.assembly import AssemblyOutput, FactKey
from proofline.domain.inputs import AnalysisInput, Money, SnapshotSource, compatibility_digest
from proofline.domain.sources import SourceKind, SourceReference, SourceSnapshot


class _FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Source(_FixtureModel):
    id: str
    kind: SourceKind
    locator: str
    text: str
    trust: str


class _CatalogueEntry(_FixtureModel):
    kind: SourceKind
    title: str
    locator: str


class _Case(_FixtureModel):
    id: str
    title: str
    user_text: str
    questions: list[FactKey]
    money: Money
    family: str
    initial_sources: list[_Source]
    sources: list[_Source]
    catalog: list[_CatalogueEntry]


class _Record(_FixtureModel):
    replay_id: str
    case_id: str
    mode: str
    removed_source_id: str | None
    cases_path: str
    output_path: str
    raw_response_path: str
    prompt_path: str
    metadata_path: str
    prompt_sha256: str
    output_sha256: str
    generation_policy: str
    recorded_on: str


class _Catalog(_FixtureModel):
    format_version: int
    description: str
    records: list[_Record]


class _Metadata(_FixtureModel):
    model: str
    reasoning_effort: str
    elapsed_seconds: float
    exit_code: int
    usage: dict[str, JsonValue]
    own_tool_events: int
    monetary_cost: float | None


class _Profile(_FixtureModel):
    policy_version: str
    schema_version: str
    policy_sha256: str
    schema_sha256: str
    cases_sha256: str
    catalog_sha256: str
    metadata_sha256: dict[str, str]


class ReplayOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    replay_id: str
    case_id: str
    condition: str
    recorded_on: str
    model: str
    input_digest: str
    prompt_sha256: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    raw_output: str
    generation_profile: dict[str, JsonValue]
    usage: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class PreparedReplay:
    option: ReplayOption
    raw_output: str
    generation_profile: dict[str, JsonValue]
    usage: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class ReplayRecording:
    option: ReplayOption
    input: AnalysisInput
    prepared: PreparedReplay


@dataclass(frozen=True, slots=True)
class SeedCase:
    title: str
    input: AnalysisInput


class ReplayProvider(Protocol):
    def options(self, input_data: AnalysisInput) -> list[ReplayOption]: ...
    def prepare(self, replay_id: str, input_data: AnalysisInput) -> PreparedReplay: ...
    def generate(self, prepared: PreparedReplay) -> GenerationResult: ...


def _snapshot(case: _Case, removed: str | None = None) -> AnalysisInput:
    titles = {entry.locator: entry.title for entry in case.catalog}
    sources = []
    for index, source in enumerate([*case.initial_sources, *case.sources]):
        reference = SourceReference(
            case_id=case.id, source_id=source.id, kind=source.kind, locator=source.locator
        )
        version = SourceSnapshot.from_text(
            reference, version_id=f"seed:{source.id}", text=source.text
        )
        included = source.id != removed
        sources.append(
            SnapshotSource(
                **reference.model_dump(),
                title=titles.get(source.locator, source.id),
                trust=source.trust,
                position=index,
                included=included,
                version_id=version.version_id if included else None,
                text=version.text if included else None,
                text_sha256=version.text_sha256 if included else None,
            )
        )
    return AnalysisInput(
        case_id=case.id,
        user_text=case.user_text,
        requested_fact_keys=tuple(case.questions),
        money=case.money,
        context={},
        catalog=tuple(sources),
    )


class ReplayCatalog:
    """Load checked, immutable recordings once; prepare only exact matching inputs."""

    def __init__(self, fixtures_root: Path) -> None:
        self._root = fixtures_root.resolve()
        profile_root = self._root / "replay/v3/profile"
        profile = _Profile.model_validate_json((profile_root / "manifest.json").read_bytes())
        cases = TypeAdapter(list[_Case]).validate_json(
            self._checked_bytes("fixtures/cases/travel-cases.json", profile.cases_sha256)
        )
        self.cases = tuple(SeedCase(case.title, _snapshot(case)) for case in cases)
        by_id = {case.id: case for case in cases}
        if len(by_id) != len(cases):
            raise ValueError("Seed case IDs must be unique")
        catalog = _Catalog.model_validate_json(
            self._checked_bytes("fixtures/replay/v3/catalog.json", profile.catalog_sha256)
        )
        if catalog.format_version != 1:
            raise ValueError("Unsupported replay catalogue version")
        for role, digest in {
            "policy": profile.policy_sha256,
            "schema": profile.schema_sha256,
        }.items():
            self._checked_bytes(f"fixtures/replay/v3/profile/{role}.json", digest)
        recordings = []
        for record in catalog.records:
            if record.generation_policy != profile.policy_version:
                raise ValueError("Recording policy does not match its historical profile")
            input_data = _snapshot(by_id[record.case_id], record.removed_source_id)
            raw = self._checked_bytes(record.output_path, record.output_sha256).decode("utf-8")
            self._checked_bytes(record.prompt_path, record.prompt_sha256)
            output = AssemblyOutput.model_validate_json(raw)
            if output.case_id != record.case_id:
                raise ValueError("Replay output case does not match its catalogue entry")
            metadata = _Metadata.model_validate_json(
                self._checked_bytes(record.metadata_path, profile.metadata_sha256[record.replay_id])
            )
            option = ReplayOption(
                replay_id=record.replay_id,
                case_id=record.case_id,
                condition=record.mode,
                recorded_on=record.recorded_on,
                model=metadata.model,
                input_digest=compatibility_digest(input_data),
                prompt_sha256=record.prompt_sha256,
                output_sha256=record.output_sha256,
            )
            generation_profile: dict[str, JsonValue] = {
                "mode": "replay",
                "replay_id": record.replay_id,
                "recorded_on": record.recorded_on,
                "model": metadata.model,
                "reasoning_effort": metadata.reasoning_effort,
                "policy_version": record.generation_policy,
                "policy_sha256": profile.policy_sha256,
                "schema_version": profile.schema_version,
                "schema_sha256": profile.schema_sha256,
                "prompt_sha256": record.prompt_sha256,
                "output_sha256": record.output_sha256,
                "recorded_elapsed_seconds": metadata.elapsed_seconds,
                "usage_origin": "recorded",
                "new_model_call": False,
                "provider_request_id": None,
                "monetary_cost": metadata.monetary_cost,
            }
            prepared = PreparedReplay(option, raw, generation_profile, metadata.usage)
            recordings.append(ReplayRecording(option, input_data, prepared))
        self.recordings = tuple(recordings)
        self._by_id = {r.option.replay_id: r for r in self.recordings}
        if len(self._by_id) != len(self.recordings):
            raise ValueError("Replay IDs must be unique")

    def _path(self, name: str) -> Path:
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or relative.parts[0] != "fixtures":
            raise ValueError("Replay path must be rooted in fixtures")
        path = self._root.joinpath(*relative.parts[1:]).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Replay path escapes fixtures")
        return path

    def _checked_bytes(self, name: str, expected: str) -> bytes:
        data = self._path(name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Replay artifact hash mismatch: {name}")
        return data

    def options(self, input_data: AnalysisInput) -> list[ReplayOption]:
        digest = compatibility_digest(input_data)
        return [r.option for r in self.recordings if r.option.input_digest == digest]

    def prepare(self, replay_id: str, input_data: AnalysisInput) -> PreparedReplay:
        recording = self._by_id.get(replay_id)
        if recording is None or recording.option.input_digest != compatibility_digest(input_data):
            raise ApplicationError(
                "REPLAY_INPUT_MISMATCH", "No recording matches the selected input."
            )
        return copy.deepcopy(recording.prepared)

    def generate(self, prepared: PreparedReplay) -> GenerationResult:
        return GenerationResult(
            prepared.raw_output,
            copy.deepcopy(prepared.generation_profile),
            copy.deepcopy(prepared.usage),
        )
