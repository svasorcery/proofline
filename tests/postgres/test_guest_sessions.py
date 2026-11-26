"""Guest ownership at the HTTP boundary, using isolated PostgreSQL data."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.application.seed import seed_demo
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.providers.replay import ReplayCatalog

ORIGIN = "https://demo.example"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
API = "/api/v1"
COOKIE = "proofline_guest"
MIGRATIONS = FIXTURES.parent / "src/proofline/persistence/migrations"


def settings(**options) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/proofline_test",
        fixtures_dir=FIXTURES,
        app_origin=ORIGIN,
        **options,
    )


def test_guest_mode_keeps_live_disabled() -> None:
    with pytest.raises(ValidationError, match="GUEST_MODE"):
        settings(guest_mode=True, live_enabled=True)


def open_session(client: TestClient) -> str:
    response = client.post(f"{API}/guest-session", headers={"Origin": ORIGIN})
    assert response.status_code == 204, response.text
    if cookie := response.headers.get("set-cookie"):
        assert {"httponly", "secure", "samesite=strict", "path=/api/v1"} <= {
            part.strip().lower() for part in cookie.split(";")[1:]
        }
    return client.cookies[COOKIE]


def case_list(client: TestClient) -> list[dict]:
    response = client.get(f"{API}/cases")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 6
    return response.json()["items"]


@pytest.mark.postgres
def test_guests_get_independent_workspaces_and_cannot_access_each_others_objects(
    postgres_engine: Engine,
) -> None:
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalogue = ReplayCatalog(FIXTURES)
    seed_demo(database, catalogue)
    guest_settings = settings(guest_mode=True)
    app = create_app(guest_settings, database=database, provider=catalogue)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        assert first.get(f"{API}/health").status_code == 200
        assert first.get(f"{API}/ready").status_code == 200
        denied = first.get(f"{API}/cases", headers={"X-Guest-ID": "local-seed"})
        assert denied.status_code == 401
        assert denied.json()["code"] == "GUEST_SESSION_REQUIRED"
        assert denied.headers["cache-control"] == "no-store"
        assert (
            first.get(f"{API}/cases", headers={"Cookie": f"{COOKIE}={'A' * 43}"}).status_code == 401
        )
        assert first.post(f"{API}/guest-session").status_code == 403
        assert (
            first.post(
                f"{API}/guest-session", headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )

        token = open_session(first)
        assert open_session(first) == token
        assert open_session(second) != token
        one, two = case_list(first), case_list(second)
        assert {c["title"] for c in one} == {c["title"] for c in two}
        assert {c["id"] for c in one}.isdisjoint(c["id"] for c in two)
        assert first.get(f"{API}/cases/V01").status_code == 404
        chosen = next(case for case in one if case["title"] == catalogue.cases[0].title)
        case_url = f"{API}/cases/{chosen['id']}"
        detail = first.get(case_url).json()
        source = detail["sources"][0]
        source_url = (
            f"{case_url}/sources/{source['source_id']}/versions/{source['current_version_id']}"
        )
        assert first.get(source_url).status_code == 200
        assert second.get(source_url).status_code == 404
        assert second.get(case_url).status_code == 404
        assert second.get(f"{case_url}/runs").status_code == 404
        assert second.get(f"{case_url}/proposals").status_code == 404
        assert second.get(f"{case_url}/replay-options").status_code == 404
        source_base = source_url.split("/versions/")[0]
        assert (
            second.post(
                f"{source_base}/versions",
                json={"expected_case_version": detail["version"], "text": "Foreign"},
            ).status_code
            == 404
        )
        assert (
            second.patch(
                case_url, json={"expected_case_version": detail["version"], "title": "Other guest"}
            ).status_code
            == 404
        )

        (option,) = first.get(f"{case_url}/replay-options").json()
        assert option["case_id"] == chosen["id"]
        response = first.post(
            f"{case_url}/runs",
            json={
                "expected_case_version": detail["version"],
                "mode": "replay",
                "replay_id": option["replay_id"],
            },
            headers={"Idempotency-Key": "guest-replay"},
        )
        foreign_run = second.post(
            f"{case_url}/runs",
            json={
                "expected_case_version": detail["version"],
                "mode": "replay",
                "replay_id": option["replay_id"],
            },
            headers={"Idempotency-Key": "guest-replay"},
        )
        assert foreign_run.status_code == 404
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "succeeded"
        assert json.loads(run["raw_output"])["case_id"] == catalogue.cases[0].input.case_id
        assert run["generation_profile"]["new_model_call"] is False
        assert run["snapshot"]["case_id"] == chosen["id"]
        assert run["input_digest"] == option["input_digest"]
        proposal_url = f"{API}/proposals/{run['proposal_revision_id']}"
        proposal = first.get(proposal_url).json()
        assert proposal["body"]["case_id"] == chosen["id"]
        assert proposal["latest_validation"]["issues"] == []
        assert second.get(f"{API}/runs/{run['id']}").status_code == 404
        assert second.get(proposal_url).status_code == 404
        assert (
            second.post(
                f"{proposal_url}/validate", json={"expected_case_version": proposal["case_version"]}
            ).status_code
            == 404
        )
        correction = {
            "expected_case_version": proposal["case_version"],
            "body": proposal["body"],
            "reason": "Checked in my workspace",
        }
        assert second.post(f"{proposal_url}/revisions", json=correction).status_code == 404
        reject = {
            "expected_case_version": proposal["case_version"],
            "kind": "reject",
            "reason": "Demo decision",
        }
        assert (
            second.post(
                f"{proposal_url}/decision",
                json=reject,
                headers={"Idempotency-Key": "other-decision"},
            ).status_code
            == 404
        )
        corrected = first.post(f"{proposal_url}/revisions", json=correction)
        assert corrected.status_code == 201, corrected.text
        child = corrected.json()
        other_case = next(case for case in two if case["title"] == chosen["title"])
        other_url = f"{API}/cases/{other_case['id']}"
        assert (
            second.post(
                f"{other_url}/active-proposal",
                json={
                    "expected_case_version": other_case["version"],
                    "proposal_revision_id": child["id"],
                },
            ).status_code
            == 404
        )
        assert second.get(f"{API}/cases/{other_case['id']}/runs").json() == []
        assert second.get(f"{API}/cases/{other_case['id']}/proposals").json() == []
        decision = first.post(
            f"{API}/proposals/{child['id']}/decision",
            json={**reject, "expected_case_version": child["case_version"]},
            headers={"Idempotency-Key": "guest-decision"},
        )
        assert decision.status_code == 201, decision.text
        assert decision.json()["actor"] == "demo_guest"
        assert second.get(f"{API}/proposals/{child['id']}").status_code == 404
        edited = second.patch(
            other_url,
            json={
                "expected_case_version": other_case["version"],
                "user_text": "Changed synthetic case",
            },
        )
        assert edited.status_code == 200
        assert second.get(f"{other_url}/replay-options").json() == []
        assert first.get(case_url).json()["user_text"] == detail["user_text"]

    restarted = create_app(guest_settings, database=database, provider=catalogue)
    with TestClient(restarted, base_url=ORIGIN, cookies={COOKIE: token}) as first:
        assert first.get(proposal_url).status_code == 200
        assert first.get(f"{case_url}/proposals").status_code == 200
        assert len(first.get(f"{case_url}/proposals").json()) == 2
        assert first.get(f"{API}/proposals/{child['id']}").json()["decision"]["kind"] == "reject"
    local = create_app(settings(), database=database, provider=catalogue)
    with TestClient(local, base_url=ORIGIN) as operator:
        assert operator.get(f"{API}/cases/V01").status_code == 200
        assert len(case_list(operator)) == 6
        assert operator.get(case_url).status_code == 404
        assert operator.get(proposal_url).status_code == 404


@pytest.mark.postgres
def test_migration_preserves_local_rows_and_rejects_downgrade_with_guests(
    postgres_engine: Engine,
) -> None:
    migration = Config()
    migration.set_main_option("script_location", str(MIGRATIONS))
    with postgres_engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.upgrade(migration, "0001_case_workflow")
        connection.execute(
            text(
                "INSERT INTO cases (id, title, user_text, requested_fact_keys) "
                "VALUES ('original', 'Local history', 'Demo', '[]')"
            )
        )
    upgrade_database(postgres_engine)
    with postgres_engine.begin() as connection:
        assert connection.execute(
            text("SELECT title, guest_id, fixture_case_id FROM cases WHERE id = 'original'")
        ).one() == (
            "Local history",
            None,
            None,
        )
        connection.execute(
            text("INSERT INTO guest_sessions (id, token_digest) VALUES ('guest', 'digest')")
        )
        connection.execute(
            text(
                "INSERT INTO cases (id, title, user_text, requested_fact_keys, guest_id) "
                "VALUES ('guest-case', 'Guest history', 'Demo', '[]', 'guest')"
            )
        )
    with pytest.raises(RuntimeError, match="guest sessions"), postgres_engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.downgrade(migration, "0001_case_workflow")
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT guest_id FROM cases WHERE id = 'guest-case'")) == "guest"
        )
