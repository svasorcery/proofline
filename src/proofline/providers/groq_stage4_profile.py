"""Bind the versioned synthetic Groq profile to fresh, selected source versions."""

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from proofline.application.state import case_or_404, current_input, read_session
from proofline.domain.inputs import AnalysisInput, compatibility_digest
from proofline.domain.present_confirmations import IncludedAcceptanceReply
from proofline.domain.required_events import RequiredEvent
from proofline.persistence.database import Database, open_database

DEFAULT_PROFILE = Path("fixtures/live/groq-stage4-profile.json")


class TrialCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str
    condition: str
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    excluded_sources: list[str]
    lookup_sources: list[str]
    reviewed_events: list[dict[str, JsonValue]]
    reviewed_reply: dict[str, JsonValue] | None


class TrialProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: Literal[1]
    provider: Literal["groq"]
    model: Literal["qwen/qwen3.8-27b"]
    profile: Literal["v3.3-citation-ids-test"]
    reasoning_effort: Literal["medium"]
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    cases: list[TrialCase] = Field(min_length=1)


def read_profile(path: Path = DEFAULT_PROFILE) -> TrialProfile:
    return TrialProfile.model_validate_json(path.read_text(encoding="utf-8"))


def snapshots_for(database: Database, profile: TrialProfile) -> dict[str, AnalysisInput]:
    with read_session(database) as session:
        return {
            case.case_id: current_input(session, case_or_404(session, case.case_id))
            for case in profile.cases
        }


def bind_profile(profile: TrialProfile, snapshots: Mapping[str, AnalysisInput]) -> dict[str, Any]:
    if len({case.case_id for case in profile.cases}) != len(profile.cases):
        raise ValueError("The profile contains duplicate cases")
    events: dict[str, list[dict[str, JsonValue]]] = {}
    replies: dict[str, dict[str, JsonValue]] = {}
    lookups: dict[str, list[str]] = {}
    for case in profile.cases:
        snapshot = snapshots[case.case_id]
        if snapshot.case_id != case.case_id or compatibility_digest(snapshot) != case.input_digest:
            raise ValueError(f"{case.case_id}: input differs from the pinned profile")
        sources = {source.source_id: source for source in snapshot.catalog}
        excluded = {source.source_id for source in sources.values() if not source.included}
        if excluded != set(case.excluded_sources) or not set(case.lookup_sources) <= excluded:
            raise ValueError(f"{case.case_id}: unread sources do not match the profile")
        if case.lookup_sources:
            lookups[case.case_id] = case.lookup_sources

        marks = []
        for entry in case.reviewed_events:
            source = sources[str(entry["source_id"])]
            mark = RequiredEvent.model_validate({**entry, "source_version_id": source.version_id})
            if not mark.matches(source):
                raise ValueError(f"{case.case_id}: cannot bind reviewed event {mark.event_type}")
            marks.append(mark.model_dump(mode="json"))
        if marks:
            events[case.case_id] = marks

        if case.reviewed_reply is not None:
            source = sources[str(case.reviewed_reply["source_id"])]
            reply = IncludedAcceptanceReply.model_validate(
                {**case.reviewed_reply, "source_version_id": source.version_id}
            )
            if not reply.matches(snapshot):
                raise ValueError(f"{case.case_id}: cannot bind reviewed reply")
            if reply.event_evidence is not None:
                assert source.text is not None
                if any(
                    source.text.count(anchor) != 1
                    for anchor in reply.event_evidence.model_dump().values()
                ):
                    raise ValueError(f"{case.case_id}: cannot bind reviewed reply events")
            replies[case.case_id] = reply.model_dump(mode="json")

    return {
        "llm_provider": profile.provider,
        "llm_model": profile.model,
        "groq_prompt_profile": profile.profile,
        "groq_reasoning_effort": profile.reasoning_effort,
        "model_max_output_tokens": profile.max_output_tokens,
        "model_timeout_seconds": profile.timeout_seconds,
        "groq_test_lookup_sources": lookups,
        "groq_test_required_events": events,
        "groq_test_acceptance_replies": replies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    profile = read_profile(parser.parse_args().profile)
    database = open_database(os.environ["DATABASE_URL"])
    try:
        bound = bind_profile(profile, snapshots_for(database, profile))
    finally:
        database.dispose()
    print(json.dumps(bound, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
