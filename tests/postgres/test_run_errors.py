import os
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from proofline.application.commands import RunCreate
from proofline.application.seed import seed_demo
from proofline.application.service import WorkflowService
from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import ProposalRevision, ValidationCheck
from proofline.providers.replay import GenerationResult, PreparedReplay, ReplayCatalog, ReplayOption

pytestmark = pytest.mark.postgres


class FailingProvider:
    def __init__(self, catalog: ReplayCatalog, failure: str) -> None:
        self.catalog, self.failure = catalog, failure
        self.calls = 0

    def options(self, input_data: AnalysisInput) -> list[ReplayOption]:
        return self.catalog.options(input_data)

    def prepare(self, replay_id: str, input_data: AnalysisInput) -> PreparedReplay:
        return self.catalog.prepare(replay_id, input_data)

    def generate(self, prepared: PreparedReplay) -> GenerationResult:
        self.calls += 1
        if self.failure == "timeout":
            raise TimeoutError("controlled timeout")
        if self.failure == "provider":
            raise RuntimeError("controlled provider failure")
        result = self.catalog.generate(prepared)
        return GenerationResult(
            "{" if self.failure == "json" else "{}", result.generation_profile, result.usage
        )


@pytest.mark.parametrize(
    "failure,code",
    [
        ("json", "INVALID_MODEL_OUTPUT"),
        ("schema", "INVALID_MODEL_OUTPUT"),
        ("timeout", "MODEL_TIMEOUT"),
        ("provider", "PROVIDER_ERROR"),
    ],
)
def test_failed_generation_is_recorded_and_never_retried_implicitly(
    postgres_engine: Engine,
    failure: str,
    code: str,
) -> None:
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures")
    seed_demo(database, catalog)
    provider = FailingProvider(catalog, failure)
    service = WorkflowService(
        database, Settings(database_url=os.environ["TEST_DATABASE_URL"]), provider
    )
    command = RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01")
    first, first_status = service.runs.create("V01", command, "attempt")
    repeated, repeated_status = service.runs.create("V01", command, "attempt")
    assert first_status == 201 and repeated_status == 200
    assert first == repeated and provider.calls == 1
    assert first.status == "failed" and first.error["code"] == code
    assert first.proposal_revision_id is None
    assert service.cases.get("V01").version == 1
    if failure in {"timeout", "provider"}:
        assert first.usage is None and first.raw_output is None
    else:
        assert first.raw_output == ("{" if failure == "json" else "{}")
    with database.sessions() as session:
        assert session.scalar(select(ProposalRevision.id)) is None
        assert session.scalar(select(ValidationCheck.id)) is None
