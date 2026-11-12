import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.application.seed import seed_demo
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import ProposalRevision
from proofline.providers.replay import GenerationResult, PreparedReplay, ReplayCatalog

pytestmark = pytest.mark.postgres


class CountingReplay(ReplayCatalog):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls = 0

    def generate(self, prepared: PreparedReplay) -> GenerationResult:
        self.calls += 1
        return super().generate(prepared)


@pytest.mark.parametrize("commit_reached_database", [False, True])
def test_completion_failure_is_reconciled_without_regeneration(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    commit_reached_database: bool,
) -> None:
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    provider = CountingReplay(Path(__file__).resolve().parents[2] / "fixtures")
    seed_demo(database, provider)
    app = create_app(
        Settings(database_url=os.environ["TEST_DATABASE_URL"]), database=database, provider=provider
    )
    command = {"expected_case_version": 1, "mode": "replay", "replay_id": "v3-full-V01"}
    headers = {"Idempotency-Key": "completion-attempt"}
    with TestClient(app) as client:
        if commit_reached_database:
            finish = app.state.service.runs._finish

            def lose_commit_acknowledgement(*args, **kwargs):
                finish(*args, **kwargs)
                raise OperationalError(
                    "commit acknowledgement", {}, RuntimeError("connection lost")
                )

            with monkeypatch.context() as patch:
                patch.setattr(app.state.service.runs, "_finish", lose_commit_acknowledgement)
                failed = client.post("/api/v1/cases/V01/runs", json=command, headers=headers)
        else:

            def fail_completion(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith("UPDATE analysis_runs"):
                    raise OperationalError(statement, parameters, RuntimeError("connection lost"))

            event.listen(postgres_engine, "before_cursor_execute", fail_completion)
            try:
                failed = client.post("/api/v1/cases/V01/runs", json=command, headers=headers)
                assert client.get("/api/v1/ready").status_code == 503
            finally:
                event.remove(postgres_engine, "before_cursor_execute", fail_completion)
        assert failed.status_code == 503
        assert client.get("/api/v1/ready").status_code == 200
        repeated = client.post("/api/v1/cases/V01/runs", json=command, headers=headers)
        assert repeated.status_code == 200
        assert provider.calls == 1
        result = repeated.json()
        with database.sessions() as session:
            proposals = session.scalars(select(ProposalRevision)).all()
        if commit_reached_database:
            assert result["status"] == "succeeded" and len(proposals) == 1
            assert client.get("/api/v1/cases/V01").json()["version"] == 2
        else:
            assert result["status"] == "failed"
            assert result["error"]["code"] == "RUN_INTERRUPTED"
            assert proposals == []
            assert client.get("/api/v1/cases/V01").json()["version"] == 1
            new_run = client.post(
                "/api/v1/cases/V01/runs",
                json=command,
                headers={"Idempotency-Key": "explicit-new-attempt"},
            )
            assert new_run.status_code == 201 and new_run.json()["status"] == "succeeded"
            assert provider.calls == 2
