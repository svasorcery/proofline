"""HTTP contract and real PostgreSQL operator workflow acceptance."""

import importlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.providers.replay import ReplayCatalog


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        ("CaseCreate", {"title": " ", "user_text": "text", "requested_fact_keys": []}),
        ("CaseCreate", {"title": "case", "user_text": " ", "requested_fact_keys": []}),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"bad": "\ud800"},
            },
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"\ud800": {"invalid": None}},
            },
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"nested": [{"\ud800": {"invalid": None}}]},
            },
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"bad": float("nan")},
            },
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"bad": "\u0000"},
            },
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": {"\u0000": "bad key"},
            },
        ),
        (
            "CaseCreate",
            {"title": "case", "user_text": "\u0000", "requested_fact_keys": []},
        ),
        (
            "CaseCreate",
            {
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": ["payment_outcome", "payment_outcome"],
            },
        ),
        ("CasePatch", {"expected_case_version": 1}),
        ("CasePatch", {"expected_case_version": True, "title": "case"}),
        ("CasePatch", {"expected_case_version": 1, "title": None}),
        ("SourcePatch", {"expected_case_version": 1, "current_version_id": None}),
        ("SourcePatch", {"expected_case_version": 1, "position": -1}),
        (
            "SourceCreate",
            {
                "expected_case_version": 1,
                "kind": "document",
                "locator": "local:receipt",
                "title": "Receipt",
                "source_id": "forged",
            },
        ),
        ("RunCreate", {"expected_case_version": 1, "mode": "replay"}),
        ("RunCreate", {"expected_case_version": 1, "mode": "live", "replay_id": "v01"}),
        ("DecisionCreate", {"expected_case_version": 1, "kind": "approve", "reason": " "}),
        (
            "DecisionCreate",
            {"expected_case_version": 1, "kind": "approve", "reason": "ok", "actor": "forged"},
        ),
    ],
)
def test_commands_reject_ambiguous_or_untrusted_mutations(
    model: str, payload: dict[str, object]
) -> None:
    commands = importlib.import_module("proofline.application.commands")
    with pytest.raises(ValidationError):
        getattr(commands, model).model_validate(payload)


def test_case_patch_distinguishes_cleared_money_from_unset_fields() -> None:
    commands = importlib.import_module("proofline.application.commands")
    patch = commands.CasePatch(expected_case_version=1, money=None)
    assert patch.model_fields_set == {"expected_case_version", "money"}


def test_commands_preserve_literal_unicode_escape_text() -> None:
    commands = importlib.import_module("proofline.application.commands")
    command = commands.CaseCreate(
        title="Literal escape",
        user_text=r"\u0000",
        requested_fact_keys=[],
        context={r"\u0000": [r"\u0000"]},
    )
    assert command.user_text == r"\u0000"
    assert command.context == {r"\u0000": [r"\u0000"]}


@pytest.fixture
def api_client(postgres_engine: Engine) -> Iterator[TestClient]:
    from proofline.api.app import create_app
    from proofline.application.seed import seed_demo

    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(Path(__file__).resolve().parents[2] / "fixtures")
    seed_demo(database, catalog)
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=Path(__file__).resolve().parents[2] / "fixtures",
    )
    injected_pool = postgres_engine.pool
    with TestClient(create_app(settings, database=database, provider=catalog)) as client:
        yield client
    # Shutdown must leave the caller-owned database available.
    assert postgres_engine.pool is injected_pool
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def assert_error(response: Response, status: int, code: str | None = None) -> dict:
    assert response.status_code == status
    error = response.json()
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"] == response.headers["X-Request-ID"]
    if code:
        assert error["code"] == code
    return error


@pytest.fixture
def error_client(api_client: TestClient) -> Iterator[TestClient]:
    # The real application is already inside api_client's lifespan.
    client = TestClient(api_client.app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.postgres
@pytest.mark.parametrize("context", [{"\ud800": {"invalid": None}}, {"nested": [{"\ud800": None}]}])
def test_http_surrogate_context_keys_are_rejected_before_storage(
    error_client: TestClient,
    context: dict[str, object],
) -> None:
    payload = {
        "title": "sensitive-case",
        "user_text": "text",
        "requested_fact_keys": [],
        "context": context,
    }
    response = error_client.post(
        "/api/v1/cases",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json", "X-Request-ID": "surrogate-key-request"},
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["request_id"] == "surrogate-key-request"
    assert "sensitive-case" not in response.text
    assert "\\ud800" not in response.text
    assert "\\ufffd" not in response.text


@pytest.mark.postgres
def test_http_unexpected_exception_has_safe_correlated_error(error_client: TestClient) -> None:
    app = error_client.app
    assert isinstance(app, FastAPI)

    @app.post("/api/v1/controlled-error")
    def controlled_error(payload: dict[str, str]) -> None:
        raise RuntimeError(f"private exception: {payload['sensitive']}")

    response = error_client.post(
        "/api/v1/controlled-error",
        json={"sensitive": "private-input"},
        headers={"X-Request-ID": "unexpected-error-request"},
    )
    error = assert_error(response, 500, "INTERNAL_ERROR")
    assert error["request_id"] == "unexpected-error-request"
    assert error["details"] == {}
    assert "private-input" not in response.text
    assert "private exception" not in response.text
    assert "RuntimeError" not in response.text


@pytest.mark.postgres
def test_http_invalid_surrogate_field_path_still_returns_safe_validation_error(
    error_client: TestClient,
) -> None:
    response = error_client.post(
        "/api/v1/cases",
        content=(
            r'{"title":"case","user_text":"text","requested_fact_keys":[],"context":'
            r'{"\ud800":1e999}}'
        ),
        headers={"Content-Type": "application/json"},
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"]["errors"]
    assert "\\ud800" not in response.text


def replay(client: TestClient, case_id: str) -> dict:
    case = client.get(f"/api/v1/cases/{case_id}").json()
    options = client.get(f"/api/v1/cases/{case_id}/replay-options").json()
    response = client.post(
        f"/api/v1/cases/{case_id}/runs",
        json={
            "expected_case_version": case["version"],
            "mode": "replay",
            "replay_id": options[0]["replay_id"],
        },
        headers={"Idempotency-Key": f"replay-{case_id}"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "succeeded"
    repeated = client.post(
        f"/api/v1/cases/{case_id}/runs",
        json={
            "expected_case_version": case["version"],
            "mode": "replay",
            "replay_id": options[0]["replay_id"],
        },
        headers={"Idempotency-Key": f"replay-{case_id}"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == response.json()["id"]
    return client.get(f"/api/v1/proposals/{response.json()['proposal_revision_id']}").json()


@pytest.mark.postgres
def test_http_seed_replay_evidence_and_idempotent_operator_approval(api_client: TestClient) -> None:
    client = api_client
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    correlated = client.get("/api/v1/health", headers={"X-Request-ID": "request-123"})
    assert correlated.headers["X-Request-ID"] == "request-123"
    replaced = client.get("/api/v1/health", headers={"X-Request-ID": "unsafe/request"})
    assert str(UUID(replaced.headers["X-Request-ID"])) == replaced.headers["X-Request-ID"]
    assert client.get("/api/v1/ready").status_code == 200
    page = client.get("/api/v1/cases").json()
    assert page["total"] == 6
    assert {item["id"] for item in page["items"]} == {"V01", "V02", "V03", "V04", "V05", "V06"}
    proposal = replay(client, "V01")
    assert proposal["status"] == "needs_operator_review"
    assert proposal["latest_validation"]["resolved_spans"]
    span = proposal["latest_validation"]["resolved_spans"][0]
    version = client.get(
        f"/api/v1/cases/V01/sources/{span['source_id']}/versions/{span['source_version_id']}"
    ).json()
    assert version["text"][span["start_cp"] : span["end_cp"]] == span["fragment"]
    body = {
        "expected_case_version": proposal["case_version"],
        "kind": "approve",
        "reason": "Reviewed source evidence",
    }
    url = f"/api/v1/proposals/{proposal['id']}/decision"
    headers = {"Idempotency-Key": "approval-V01"}
    approved = client.post(url, json=body, headers=headers)
    assert approved.status_code == 201, approved.text
    assert approved.json()["actor"] == "demo_operator"
    repeated = client.post(url, json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == approved.json()["id"]
    conflict = client.post(url, json={**body, "reason": "Different reason"}, headers=headers)
    assert_error(conflict, 409, "IDEMPOTENCY_CONFLICT")
    assert client.get("/api/v1/cases/V01").json()["status"] == "approved"
    # Transport exposes neither command hashes nor idempotency storage.
    assert "request_hash" not in approved.text
    assert "idempotency_key" not in approved.text
    assert len(client.get("/api/v1/cases/V01/runs").json()) == 1
    assert (
        client.get(
            "/api/v1/runs/" + client.get("/api/v1/cases/V01/runs").json()[0]["id"]
        ).status_code
        == 200
    )


@pytest.mark.postgres
def test_http_v04_block_edit_validate_and_approve(api_client: TestClient) -> None:
    client = api_client
    proposal = replay(client, "V04")
    assert proposal["status"] == "blocked_for_approval"
    blocked = client.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={
            "expected_case_version": proposal["case_version"],
            "kind": "approve",
            "reason": "Check blocked guard",
        },
        headers={"Idempotency-Key": "blocked-approval"},
    )
    assert_error(blocked, 409, "VALIDATION_BLOCKED")
    body = proposal["body"]
    body["timeline"] = [
        event for event in body["timeline"] if event["event_type"] != "alternative_accepted"
    ]
    body["follow_up"]["action"] = "ask_user"
    body["follow_up"]["locator"] = None
    revision = client.post(
        f"/api/v1/proposals/{proposal['id']}/revisions",
        json={
            "expected_case_version": proposal["case_version"],
            "body": body,
            "reason": "Remove unsupported acceptance and request missing hotel confirmation",
        },
    )
    assert revision.status_code == 201, revision.text
    child = revision.json()
    assert child["origin"] == "operator"
    assert child["parent_revision_id"] == proposal["id"]
    assert child["status"] == "needs_operator_review", child["blocking_issues"]
    validated = client.post(
        f"/api/v1/proposals/{child['id']}/validate",
        json={"expected_case_version": child["case_version"]},
    )
    assert validated.status_code == 200, validated.text
    child = validated.json()
    assert child["latest_validation"]["id"] != revision.json()["latest_validation"]["id"]
    assert {check["id"] for check in child["validations"]} >= {
        child["latest_validation"]["id"],
        revision.json()["latest_validation"]["id"],
    }
    approved = client.post(
        f"/api/v1/proposals/{child['id']}/decision",
        json={
            "expected_case_version": child["case_version"],
            "kind": "approve",
            "reason": "Reviewed corrected conditional result",
        },
        headers={"Idempotency-Key": "corrected-approval"},
    )
    assert approved.status_code == 201, approved.text
    historical = client.get(f"/api/v1/proposals/{child['id']}").json()
    assert historical["decision_current"] is True
    assert approved.json()["validation_check_id"] in {
        check["id"] for check in historical["validations"]
    }
    edited = client.patch(
        "/api/v1/cases/V04",
        json={
            "expected_case_version": approved.json()["result_case_version"],
            "context": {"operator_note": "New reference context"},
        },
    )
    assert edited.status_code == 200, edited.text
    historical = client.get(f"/api/v1/proposals/{child['id']}").json()
    assert historical["stale"] is True
    assert historical["decision_current"] is False
    assert historical["decision"]["id"] == approved.json()["id"]
    assert (
        client.get(f"/api/v1/proposals/{proposal['id']}").json()["body"]["timeline"][-1][
            "event_type"
        ]
        == "alternative_accepted"
    )
    assert len(client.get("/api/v1/cases/V04/proposals").json()) == 2


@pytest.mark.postgres
def test_http_missing_evidence_uses_explicit_compatible_ablation(api_client: TestClient) -> None:
    client = api_client
    case = client.get("/api/v1/cases/V01").json()
    removed = client.patch(
        "/api/v1/cases/V01/sources/f9a88d5b726a4d4b",
        json={"expected_case_version": case["version"], "included": False},
    )
    assert removed.status_code == 200, removed.text
    options = client.get("/api/v1/cases/V01/replay-options").json()
    assert [option["replay_id"] for option in options] == ["v3-ablation-V01"]
    proposal = replay(client, "V01")
    assert proposal["body"]["readiness"] == "request_evidence"
    assert proposal["decision"] is None
    snapshot_source = next(
        source
        for source in proposal["snapshot"]["catalog"]
        if source["source_id"] == "f9a88d5b726a4d4b"
    )
    assert snapshot_source["included"] is False
    assert snapshot_source["text"] is None
    assert snapshot_source["version_id"] is None


@pytest.mark.postgres
def test_http_changed_input_refuses_recording_without_fallback(api_client: TestClient) -> None:
    client = api_client
    changed = client.patch(
        "/api/v1/cases/V01",
        json={"expected_case_version": 1, "user_text": "Different synthetic request"},
    )
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/cases/V01/replay-options").json() == []
    rejected = client.post(
        "/api/v1/cases/V01/runs",
        json={
            "expected_case_version": changed.json()["version"],
            "mode": "replay",
            "replay_id": "v3-full-V01",
        },
        headers={"Idempotency-Key": "mismatched-replay"},
    )
    assert_error(rejected, 409, "REPLAY_INPUT_MISMATCH")
    assert client.get("/api/v1/cases/V01/runs").json() == []


@pytest.mark.postgres
def test_http_source_registration_versions_selection_and_metadata(api_client: TestClient) -> None:
    client = api_client
    created = client.post(
        "/api/v1/cases",
        json={"title": "Synthetic", "user_text": "Review receipt", "requested_fact_keys": []},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    case_id = case["id"]
    registered = client.post(
        f"/api/v1/cases/{case_id}/sources",
        json={
            "expected_case_version": case["version"],
            "kind": "document",
            "locator": "local:receipt",
            "title": "Expected receipt",
        },
    )
    assert registered.status_code == 201, registered.text
    case = registered.json()
    source = case["sources"][0]
    assert source["included"] is False
    assert source["current_version_id"] is None
    source_url = f"/api/v1/cases/{case_id}/sources/{source['source_id']}"
    added = client.post(
        source_url + "/versions",
        json={
            "expected_case_version": case["version"],
            "text": "🙂 receipt\r\n<script>inert</script>",
        },
    )
    assert added.status_code == 201, added.text
    case = added.json()
    source = case["sources"][0]
    first_version = source["current_version_id"]
    changed = client.patch(
        source_url,
        json={
            "expected_case_version": case["version"],
            "included": True,
            "title": "Receipt",
            "trust": "operator",
            "position": 0,
        },
    )
    assert changed.status_code == 200, changed.text
    case = changed.json()
    assert case["sources"][0]["included"] is True
    assert case["content_revision"] > created.json()["content_revision"]
    old = client.get(source_url + f"/versions/{first_version}")
    assert old.status_code == 200
    assert old.json()["text"] == "🙂 receipt\n<script>inert</script>"
    added = client.post(
        source_url + "/versions",
        json={"expected_case_version": case["version"], "text": "Replacement receipt"},
    )
    assert added.status_code == 201, added.text
    case = added.json()
    restored = client.patch(
        source_url,
        json={"expected_case_version": case["version"], "current_version_id": first_version},
    )
    assert restored.status_code == 200, restored.text
    assert len(restored.json()["sources"][0]["versions"]) == 2
    assert (
        client.get(source_url + f"/versions/{first_version}").json()["text"] == old.json()["text"]
    )
    stale = client.patch(
        f"/api/v1/cases/{case_id}", json={"expected_case_version": 1, "title": "Late edit"}
    )
    assert_error(stale, 409, "CASE_VERSION_CONFLICT")


@pytest.mark.postgres
def test_http_error_shape_origins_limits_and_disabled_live(api_client: TestClient) -> None:
    client = api_client
    assert_error(client.get("/api/v1/cases/missing", headers={"X-Request-ID": "request-123"}), 404)
    assert_error(client.get("/missing"), 404)
    assert_error(client.get("/api/v1/cases?limit=101"), 422)
    assert_error(client.get("/api/v1/cases?status=unknown"), 422)
    invalid = client.post(
        "/api/v1/cases", json={"title": "secret-value", "user_text": 42, "requested_fact_keys": []}
    )
    error = assert_error(invalid, 422)
    assert "secret-value" not in str(error)
    surrogate = client.post(
        "/api/v1/cases",
        content=r'{"title":"case","user_text":"text","requested_fact_keys":[],"context":{"bad":"\ud800"}}',
        headers={"Content-Type": "application/json"},
    )
    assert_error(surrogate, 422, "VALIDATION_ERROR")
    for context in ({"bad": "\u0000"}, {"\u0000": "bad key"}):
        nul = client.post(
            "/api/v1/cases",
            json={
                "title": "case",
                "user_text": "text",
                "requested_fact_keys": [],
                "context": context,
            },
        )
        assert_error(nul, 422, "VALIDATION_ERROR")
    literal_escape = client.post(
        "/api/v1/cases",
        json={
            "title": "Literal escape",
            "user_text": r"\u0000",
            "requested_fact_keys": [],
            "context": {r"\u0000": [r"\u0000"]},
        },
    )
    assert literal_escape.status_code == 201, literal_escape.text
    assert literal_escape.json()["user_text"] == r"\u0000"
    assert literal_escape.json()["context"] == {r"\u0000": [r"\u0000"]}
    forbidden = client.post(
        "/api/v1/cases", json={}, headers={"Origin": "https://untrusted.example"}
    )
    assert_error(forbidden, 403, "ORIGIN_NOT_ALLOWED")
    preflight = client.options(
        "/api/v1/cases",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Idempotency-Key",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8000"
    assert "Access-Control-Allow-Credentials" not in preflight.headers
    oversized = client.post(
        "/api/v1/cases", json={"title": "Large", "user_text": "x" * 8001, "requested_fact_keys": []}
    )
    assert_error(oversized, 413)
    case = client.get("/api/v1/cases/V01").json()
    request = {"expected_case_version": case["version"], "mode": "live"}
    assert_error(client.post("/api/v1/cases/V01/runs", json=request), 422)
    assert_error(
        client.post("/api/v1/cases/V01/runs", json=request, headers={"Idempotency-Key": " "}), 422
    )
    disabled = client.post(
        "/api/v1/cases/V01/runs", json=request, headers={"Idempotency-Key": "disabled-live"}
    )
    assert_error(disabled, 503, "LIVE_NOT_CONFIGURED")
    assert client.get("/api/v1/cases/V01/runs").json() == []
    assert client.get("/api/v1/capabilities").status_code == 200


@pytest.mark.postgres
def test_http_unmigrated_database_keeps_health_and_normalizes_readiness(
    postgres_engine: Engine,
) -> None:
    from proofline.api.app import create_app

    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"])
    with TestClient(create_app(settings, database=database)) as client:
        assert client.get("/health").status_code == 200
        assert_error(client.get("/ready"), 503, "DATABASE_UNAVAILABLE")
        error = assert_error(client.get("/api/v1/cases"), 503, "DATABASE_UNAVAILABLE")
        assert "SELECT" not in str(error)


@pytest.mark.postgres
def test_http_database_connection_failure_does_not_break_process_health() -> None:
    from proofline.api.app import create_app

    settings = Settings(
        database_url="postgresql+psycopg://proofline:proofline_test@127.0.0.1:1/proofline_test?connect_timeout=1"
    )
    app = create_app(settings)
    database = app.state.service.database
    owned_pool = database.engine.pool
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert_error(client.get("/ready"), 503, "DATABASE_UNAVAILABLE")
            assert_error(client.get("/api/v1/cases"), 503, "DATABASE_UNAVAILABLE")
        assert database.engine.pool is not owned_pool
    finally:
        database.dispose()
