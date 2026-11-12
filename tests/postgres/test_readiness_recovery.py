import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.application.seed import seed_demo
from proofline.application.state import current_input
from proofline.config import Settings
from proofline.domain.inputs import compatibility_digest
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, Case
from proofline.providers.replay import ReplayCatalog

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("entry_point", ["ready", "command"])
def test_readiness_recovers_previous_owner_when_database_becomes_ready_after_startup(
    postgres_engine: Engine,
    entry_point: str,
) -> None:
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures")
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"])
    app = create_app(settings, database=database, provider=catalog)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 503
        # The same readiness transition occurs after a DB outage or delayed migration.
        upgrade_database(postgres_engine)
        seed_demo(database, catalog)
        with database.sessions.begin() as session:
            case = session.get(Case, "V01")
            snapshot = current_input(session, case)
            run = AnalysisRun(
                case_id="V01",
                mode="replay",
                idempotency_key="old-request",
                request_hash="old-hash",
                snapshot=snapshot.model_dump(mode="json"),
                input_digest=compatibility_digest(snapshot),
                content_revision=1,
                base_case_version=1,
                owner_instance_id="previous-process",
                status="running",
                generation_profile={},
            )
            session.add(run)
            session.flush()
            run_id = run.id
        if entry_point == "ready":
            assert client.get("/api/v1/ready").status_code == 200
        response = client.post(
            "/api/v1/cases/V01/runs",
            json={
                "mode": "replay",
                "replay_id": "v3-full-V01",
                "expected_case_version": 1,
            },
            headers={"Idempotency-Key": "explicit-new-attempt"},
        )
        assert response.status_code == 201 and response.json()["status"] == "succeeded"
        recovered = client.get(f"/api/v1/runs/{run_id}").json()
        assert recovered["status"] == "failed"
        assert recovered["error"]["code"] == "PROCESS_INTERRUPTED"
