"""Live workflow acceptance with real PostgreSQL and an offline SDK transport."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.application.commands import CasePatch, DecisionCreate, RunCreate
from proofline.application.errors import ApplicationError
from proofline.application.seed import seed_demo
from proofline.application.service import WorkflowService
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, ReviewDecision
from proofline.providers.openai_responses import OpenAIResponsesProvider
from proofline.providers.replay import ReplayCatalog

pytestmark = pytest.mark.postgres
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def setup(postgres_engine):
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(FIXTURES)
    seed_demo(database, catalog)
    settings = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=FIXTURES,
        live_enabled=True,
        openai_api_key=SecretStr("offline-test-key"),
        openai_model="configured-test-model",
    )
    return database, settings, catalog


def transport(catalog, outcome="success", *, entered=None, release=None, case_id="V01"):
    requests = []
    raw = next(
        r.prepared.raw_output
        for r in catalog.recordings
        if r.option.replay_id == f"v3-full-{case_id}"
    )
    if outcome == "literal_nul":
        raw += "\u0000"

    def handle(request):
        requests.append(json.loads(request.content))
        if entered:
            entered.set()
            assert release.wait(10)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private key diagnostic", request=request)
        if outcome == "access":
            return httpx.Response(
                401,
                headers={"x-request-id": "req_denied"},
                json={"error": {"message": "offline-test-key private provider details"}},
            )
        if outcome == "refusal":
            content = [{"type": "refusal", "refusal": "Cannot comply"}]
        else:
            content = [
                {
                    "type": "output_text",
                    "text": (
                        "{"
                        if outcome == "invalid"
                        else raw.replace('"V01"', '"V01\\u0000"')
                        if outcome == "nul"
                        else raw
                    ),
                    "annotations": [],
                }
            ]
        return httpx.Response(
            200,
            headers={"x-request-id": "req_live"},
            json={
                "id": "resp_live",
                "model": "actual-test-model",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_live",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": content,
                    }
                ],
                "usage": None,
            },
        )

    return httpx.MockTransport(handle), requests


@pytest.mark.parametrize(
    "outcome,code",
    [
        ("success", None),
        ("refusal", "MODEL_REFUSAL"),
        ("invalid", "INVALID_MODEL_OUTPUT"),
        ("nul", "INVALID_MODEL_OUTPUT"),
        ("literal_nul", "INVALID_MODEL_OUTPUT"),
        ("timeout", "MODEL_TIMEOUT"),
        ("access", "PROVIDER_ACCESS_DENIED"),
    ],
)
def test_http_live_is_idempotent_and_never_approves_or_falls_back(setup, outcome, code):
    database, settings, catalog = setup
    mock, requests = transport(catalog, outcome)
    live = OpenAIResponsesProvider(settings, transport=mock)
    app = create_app(settings, database=database, provider=catalog, live_provider=live)
    with TestClient(app) as client:
        caps = client.get("/api/v1/capabilities").json()
        assert caps["default_mode"] == "replay" and caps["live_available"]
        assert caps["live_model"] == settings.openai_model
        assert caps["live_access_verified"] is False
        body = {"mode": "live", "expected_case_version": 1}
        path = "/api/v1/cases/V01/runs"
        first = client.post(path, json=body, headers={"Idempotency-Key": "live-one"})
        repeat = client.post(path, json=body, headers={"Idempotency-Key": "live-one"})
        assert first.status_code == 201 and repeat.status_code == 200
        assert first.json() == repeat.json() and len(requests) == 1
        run = first.json()
        assert run["mode"] == "live" and run["usage"] is None
        assert run["generation_profile"]["monetary_cost"] is None
        assert "offline-test-key" not in first.text
        assert "private provider details" not in first.text
        if outcome == "literal_nul":
            assert run["generation_profile"]["raw_output_encoding"] == "json-string"
            assert json.loads(run["raw_output"]).endswith("\u0000")
        if code:
            assert run["status"] == "failed" and run["error"]["code"] == code
            assert run["proposal_revision_id"] is None
            assert client.get("/api/v1/cases/V01").json()["version"] == 1
        else:
            assert run["status"] == "succeeded"
            proposal = client.get(f"/api/v1/proposals/{run['proposal_revision_id']}").json()
            assert proposal["status"] == "needs_operator_review"
            assert proposal["decision"] is None
            assert json.loads(requests[0]["input"]) == run["snapshot"]
        # An explicit Replay works without calling the configured live adapter.
        version = client.get("/api/v1/cases/V01").json()["version"]
        replay = client.post(
            path,
            json={"mode": "replay", "expected_case_version": version, "replay_id": "v3-full-V01"},
            headers={"Idempotency-Key": "replay-explicit"},
        )
        assert replay.status_code == 201 and replay.json()["mode"] == "replay"
        assert len(requests) == 1


def test_missing_live_key_keeps_replay_ready_and_creates_no_run(setup):
    database, settings, catalog = setup
    settings.openai_api_key = None
    app = create_app(settings, database=database, provider=catalog)
    with TestClient(app) as client:
        assert client.get("/api/v1/ready").status_code == 200
        caps = client.get("/api/v1/capabilities").json()
        assert caps["live_reason"] == "LIVE_KEY_MISSING"
        assert caps["replay_available"] and not caps["live_available"]
        result = client.post(
            "/api/v1/cases/V01/runs",
            json={"mode": "live", "expected_case_version": 1},
            headers={"Idempotency-Key": "unavailable"},
        )
        assert result.status_code == 503 and result.json()["code"] == "LIVE_NOT_CONFIGURED"
    with database.sessions() as session:
        assert session.scalar(select(AnalysisRun.id)) is None


def test_live_schema_success_keeps_contradictory_card_blocked(setup):
    database, settings, catalog = setup
    mock, requests = transport(catalog, case_id="V04")
    service = WorkflowService(
        database, settings, catalog, live_provider=OpenAIResponsesProvider(settings, transport=mock)
    )
    run, _ = service.runs.create("V04", RunCreate(expected_case_version=1, mode="live"), "live-v04")
    proposal = service.proposals.get(run.proposal_revision_id)
    assert run.status == "succeeded" and proposal.status == "blocked_for_approval"
    assert len(requests) == 1 and proposal.decision is None
    with pytest.raises(ApplicationError) as blocked:
        service.proposals.decide(
            proposal.id,
            DecisionCreate(expected_case_version=2, kind="approve", reason="Checked"),
            "approval",
        )
    assert blocked.value.code == "VALIDATION_BLOCKED"


def test_malformed_provider_response_persists_received_usage_and_ids(setup):
    database, settings, catalog = setup
    requests = []
    usage = {"input_tokens": 13, "output_tokens": 2, "total_tokens": 15}

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_malformed"},
            json={
                "id": "resp_malformed",
                "model": "actual-test-model",
                "status": "completed",
                "output": None,
                "usage": usage,
            },
        )

    service = WorkflowService(
        database,
        settings,
        catalog,
        live_provider=OpenAIResponsesProvider(settings, transport=httpx.MockTransport(handle)),
    )
    command = RunCreate(expected_case_version=1, mode="live")
    run, _ = service.runs.create("V01", command, "malformed")
    repeated, _ = service.runs.create("V01", command, "malformed")
    assert run == repeated and len(requests) == 1
    assert run.status == "failed" and run.error["code"] == "INVALID_MODEL_OUTPUT"
    assert run.usage == usage
    assert run.generation_profile["provider_request_id"] == "req_malformed"
    assert run.generation_profile["provider_response_id"] == "resp_malformed"
    assert run.generation_profile["latency_seconds"] >= 0
    assert json.loads(run.raw_output)["output"] is None


@pytest.mark.parametrize("interleaving", ["edit", "decision", "restart"])
def test_concurrent_live_retains_snapshot_cas_and_single_request(setup, interleaving):
    database, settings, catalog = setup
    entered, release = Event(), Event()
    mock, requests = transport(catalog, entered=entered, release=release)
    live = OpenAIResponsesProvider(settings, transport=mock)
    service = WorkflowService(database, settings, catalog, live_provider=live)
    original, _ = service.runs.create(
        "V01",
        RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
        "original",
    )
    command = RunCreate(expected_case_version=2, mode="live")
    before = service.cases.get("V01")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.runs.create, "V01", command, "live-running")
        try:
            assert entered.wait(10)
            repeated, status = service.runs.create("V01", command, "live-running")
            assert status == 202 and repeated.status == "running"
            with pytest.raises(ApplicationError) as conflict:
                service.runs.create("V01", command, "another-live")
            assert conflict.value.code == "ANALYSIS_ALREADY_RUNNING"
            if interleaving == "edit":
                service.cases.update(
                    "V01",
                    CasePatch(
                        expected_case_version=2, user_text=before.user_text + " Updated input."
                    ),
                )
            elif interleaving == "decision":
                service.proposals.decide(
                    original.proposal_revision_id,
                    DecisionCreate(expected_case_version=2, kind="approve", reason="Checked"),
                    "human",
                )
            else:
                replacement = WorkflowService(database, settings, catalog)
                assert replacement.recover_interrupted_runs() == 1
        finally:
            release.set()
        completed, _ = pending.result(timeout=10)
    assert len(requests) == 1
    if interleaving == "restart":
        assert completed.status == "failed" and completed.error["code"] == "PROCESS_INTERRUPTED"
        assert completed.proposal_revision_id is None
    else:
        candidate = service.proposals.get(completed.proposal_revision_id)
        assert not candidate.active and candidate.decision is None
        assert candidate.stale == (interleaving == "edit")
        assert candidate.snapshot.user_text == before.user_text
        if interleaving == "decision":
            assert service.cases.get("V01").status == "approved"
    assert service.cases.get("V01").current_proposal_revision_id == original.proposal_revision_id
    with database.sessions() as session:
        decisions = list(session.scalars(select(ReviewDecision)))
        assert len(decisions) == (1 if interleaving == "decision" else 0)
