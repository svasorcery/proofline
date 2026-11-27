"""Persisted admission and guest Live over an offline Groq transport."""

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, Case
from proofline.providers.factory import create_live_provider
from proofline.providers.replay import ReplayCatalog

ORIGIN = "https://guests.example"
API = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
V55_V06 = FIXTURES.parent / "evals/live/v55-groq-gpt-oss-20b-stage4-acceptance-2026-11-25/full-V06"


def config(**updates):
    options = {
        "guest_mode": True,
        "live_enabled": True,
        "llm_provider": "groq",
        "llm_model": "openai/gpt-oss-20b",
        "llm_api_key": "offline-placeholder",
        "groq_reasoning_effort": "low",
        "model_max_output_tokens": 1000,
    }
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/proofline_test",
        fixtures_dir=FIXTURES,
        app_origin=ORIGIN,
        **{**options, **updates},
    )


def test_guest_live_requires_the_bounded_groq_profile():
    assert config().live_unavailable_reason is None
    for invalid in (
        {"llm_provider": "openai"},
        {"llm_model": "other-model"},
        {"model_max_output_tokens": 1001},
        {"groq_reasoning_effort": "high"},
    ):
        with pytest.raises(ValidationError, match="Guest Live"):
            config(**invalid)


def guest_app(engine: Engine, handle):
    upgrade_database(engine)
    database = Database(engine, sessionmaker(engine, expire_on_commit=False))
    settings = config()
    provider = create_live_provider(settings, transport=httpx.MockTransport(handle))
    return database, create_app(
        settings, database=database, provider=ReplayCatalog(FIXTURES), live_provider=provider
    )


def create_workspace(client: TestClient) -> list[dict]:
    assert client.post(f"{API}/guest-session", headers={"Origin": ORIGIN}).status_code == 204
    return client.get(f"{API}/cases").json()["items"]


def start(client: TestClient, case: dict, key: str):
    return client.post(
        f"{API}/cases/{case['id']}/runs",
        json={"mode": "live", "expected_case_version": case["version"]},
        headers={"Idempotency-Key": key},
    )


def rate_limited(response, reason: str):
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "GUEST_LIVE_RATE_LIMITED"
    details = response.json()["details"]
    assert details["limit"] == reason
    assert details["retry_after_seconds"] > 0
    assert response.headers["retry-after"] == str(details["retry_after_seconds"])


def fake_groq_rejection():
    return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})


@pytest.mark.postgres
@pytest.mark.parametrize("variant", ["v55", "missing_release", "missing_release_quote"])
def test_guest_v06_assembles_the_card_with_its_own_sources(postgres_engine, variant):
    recorded_request = json.loads((V55_V06 / "request.json").read_text(encoding="utf-8"))
    recorded_run = json.loads((V55_V06 / "run.json").read_text(encoding="utf-8"))
    expected_body = json.loads((V55_V06 / "proposal.json").read_text(encoding="utf-8"))["body"]
    recorded_sources = json.loads(recorded_request["messages"][1]["content"])["catalog"]
    calls = []

    def citation_ids(sources):
        return {
            (source["source_id"], row["text"]): row["citation_id"]
            for source in sources
            for row in source["citation_lines"]
            if row["citation_id"] is not None
        }

    def handle(request):
        sent = json.loads(request.content)
        calls.append(sent)
        if sent["response_format"]["json_schema"]["name"] != "assembly_citation_ids":
            return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})
        guest_input = json.loads(sent["messages"][1]["content"])
        old_ids = citation_ids(recorded_sources)
        new_ids = citation_ids(guest_input["catalog"])
        rebind = {old_id: new_ids[line] for line, old_id in old_ids.items()}
        raw = json.loads(recorded_run["raw_output"])
        if variant != "v55":
            # Reproduce v57's answer shape using the tracked v55 recording.
            raw["timeline"] = [
                event
                for event in raw["timeline"]
                if event["event_type"] != "authorization_released"
            ]
            raw["readiness"] = "ready_for_rules"
            raw["follow_up"] = {"action": "none", "locator": None, "requirements": []}
        if variant == "missing_release_quote":
            (opening,) = (qid for (_, line), qid in old_ids.items() if "auth.open" in line)
            raw["facts"]["payment_outcome"]["evidence"] = [opening]
        raw["case_id"] = guest_input["case_id"]
        for item in [*raw["facts"].values(), *raw["timeline"]]:
            item["evidence"] = [rebind[quote_id] for quote_id in item["evidence"]]
        return httpx.Response(
            200,
            json={
                "id": "offline-v55",
                "object": "chat.completion",
                "created": 0,
                "model": sent["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(raw)},
                    }
                ],
            },
        )

    database, app = guest_app(postgres_engine, handle)
    with TestClient(app, base_url=ORIGIN) as client:
        cases = {case["id"]: case for case in create_workspace(client)}
        with database.sessions() as session:
            case_id = session.scalar(select(Case.id).where(Case.fixture_case_id == "V06"))
        assert case_id is not None and case_id != "V06"
        response = start(client, cases[case_id], "v55-guest")
        assert response.status_code == 201, response.text
        (sent,) = calls
        assert sent["response_format"]["json_schema"]["name"] == "assembly_citation_ids"
        assert sent["response_format"]["json_schema"]["strict"] is True
        assert sent["messages"][0] == recorded_request["messages"][0]
        run = response.json()
        assert run["status"] == "succeeded", run["error"]
        assert run["generation_profile"]["guest_live"] is True
        raw = json.loads(run["raw_output"])
        assert raw["case_id"] == case_id
        proposal = client.get(f"{API}/proposals/{run['proposal_revision_id']}").json()
        if variant == "v55":
            assert raw["follow_up"]["requirements"] == ["bank_settlement_document"]
        else:
            assert [event["event_type"] for event in raw["timeline"]] == ["supplier_cancelled"]
        if variant == "missing_release_quote":
            assert [event["event_type"] for event in proposal["body"]["timeline"]] == [
                "supplier_cancelled"
            ]
        else:
            assert proposal["body"] == {**expected_body, "case_id": case_id}
        if variant == "missing_release":
            rules = run["generation_profile"]["reviewed_citation_rules_v1"]
            assert (
                rules["authorization_release"]["release"]["evidence"]
                == raw["facts"]["payment_outcome"]["evidence"]
            )
        assert proposal["latest_validation"]["result"] == "needs_operator_review"
        assert proposal["latest_validation"]["issues"] == []
        assert proposal["decision"] is None
        ledger = next(
            source for source in run["snapshot"]["catalog"] if source["kind"] == "payment_ledger"
        )
        for span in proposal["latest_validation"]["resolved_spans"]:
            if span["source_id"] == ledger["source_id"]:
                assert span["source_version_id"] == ledger["version_id"]


@pytest.mark.postgres
def test_guest_cooldown_counts_provider_failures_and_preserves_replay(postgres_engine):
    calls = []

    def handle(request):
        calls.append(request)
        return fake_groq_rejection()

    database, app = guest_app(postgres_engine, handle)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        initial, other = create_workspace(first), create_workspace(second)
        assert first.get(f"{API}/capabilities").json()["live_available"] is True
        accepted = start(first, initial[0], "first")
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["error"]["code"] == "PROVIDER_RATE_LIMITED"
        assert accepted.json()["generation_profile"]["guest_live"] is True
        repeated = start(first, initial[0], "first")
        assert repeated.status_code == 200 and repeated.json()["id"] == accepted.json()["id"]
        rate_limited(start(second, other[0], "cooldown"), "cooldown")

        path = f"{API}/cases/{other[0]['id']}"
        option = second.get(f"{path}/replay-options").json()[0]
        replay = second.post(
            f"{path}/runs",
            json={
                "mode": "replay",
                "replay_id": option["replay_id"],
                "expected_case_version": other[0]["version"],
            },
            headers={"Idempotency-Key": "replay"},
        )
        assert replay.status_code == 201 and replay.json()["status"] == "succeeded"
        assert len(calls) == 1

        with database.sessions.begin() as session:
            older = datetime.now(UTC) - timedelta(seconds=70)
            session.execute(
                update(AnalysisRun)
                .where(AnalysisRun.mode == "live")
                .values(started_at=older, finished_at=older)
            )
        edited = first.patch(
            f"{API}/cases/{initial[1]['id']}",
            json={"user_text": "Edited text", "expected_case_version": initial[1]["version"]},
        ).json()
        denied = start(first, edited, "edited")
        assert (
            denied.status_code == 422 and denied.json()["code"] == "GUEST_LIVE_INPUT_NOT_SUPPORTED"
        )
        assert len(calls) == 1
        assert start(first, initial[2], "after-cooldown").status_code == 201
        assert len(calls) == 2


@pytest.mark.postgres
def test_guest_and_shared_rolling_budgets_survive_a_restart(postgres_engine):
    calls = []

    def handle(request):
        calls.append(request)
        return fake_groq_rejection()

    database, app = guest_app(postgres_engine, handle)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        initial, other = create_workspace(first), create_workspace(second)
        first_cookie = first.cookies["proofline_guest"]
        second_cookie = second.cookies["proofline_guest"]
    now = datetime.now(UTC)

    def historical_runs(case_id: str, count: int, start_number: int):
        at = now - timedelta(hours=2)
        return [
            AnalysisRun(
                case_id=case_id,
                mode="live",
                status="failed",
                idempotency_key=f"archive-{i}",
                request_hash="diagnostic",
                snapshot={},
                input_digest="fixture",
                content_revision=1,
                base_case_version=1,
                owner_instance_id="previous",
                started_at=at,
                finished_at=at,
            )
            for i in range(start_number, start_number + count)
        ]

    with database.sessions.begin() as session:
        session.add_all(historical_runs(initial[0]["id"], 3, 0))
    fresh = create_app(
        config(),
        database=database,
        provider=ReplayCatalog(FIXTURES),
        live_provider=create_live_provider(config(), transport=httpx.MockTransport(handle)),
    )
    with (
        TestClient(fresh, base_url=ORIGIN, cookies={"proofline_guest": first_cookie}) as first,
        TestClient(fresh, base_url=ORIGIN, cookies={"proofline_guest": second_cookie}) as second,
    ):
        rate_limited(start(first, initial[1], "guest-cap"), "guest_day")
        with database.sessions.begin() as session:
            session.add_all(historical_runs(initial[0]["id"], 17, 3))
        rate_limited(start(second, other[0], "shared-cap"), "shared_day")
        assert calls == []
        with database.sessions.begin() as session:
            expired = now - timedelta(hours=25)
            session.execute(update(AnalysisRun).values(started_at=expired, finished_at=expired))
        assert start(second, other[0], "new-window").status_code == 201
        assert len(calls) == 1


@pytest.mark.postgres
@pytest.mark.parametrize("isolation_level", ["READ COMMITTED", "REPEATABLE READ"])
def test_parallel_guests_share_one_live_slot(postgres_engine, isolation_level):
    barrier, release = Barrier(2), Event()
    calls = []

    def handle(request):
        calls.append(request)
        assert release.wait(12)
        return fake_groq_rejection()

    database, app = guest_app(
        postgres_engine.execution_options(isolation_level=isolation_level), handle
    )
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        one, two = create_workspace(first)[0], create_workspace(second)[0]

        def simultaneous(client, case):
            barrier.wait(timeout=10)
            return start(client, case, "simultaneous")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(simultaneous, first, one),
                pool.submit(simultaneous, second, two),
            ]
            try:
                complete, _ = wait(futures, return_when=FIRST_COMPLETED, timeout=8)
                assert len(complete) == 1
                rate_limited(next(iter(complete)).result(), "busy")
            finally:
                release.set()
            assert sorted(future.result().status_code for future in futures) == [201, 429]
        assert len(calls) == 1
        with database.sessions() as session:
            assert len(session.scalars(select(AnalysisRun)).all()) == 1


@pytest.mark.postgres
def test_an_uncertain_admission_keeps_the_budget_without_holding_the_slot(
    postgres_engine, monkeypatch
):
    calls = []

    def handle(request):
        calls.append(request)
        return fake_groq_rejection()

    database, app = guest_app(postgres_engine, handle)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        one, two = create_workspace(first)[0], create_workspace(second)[0]
        begin = database.sessions.begin
        lose_ack = True

        @contextmanager
        def begin_with_commit_failure():
            nonlocal lose_ack
            with begin() as session:
                yield session
            if lose_ack:
                lose_ack = False
                raise OperationalError("COMMIT", None, RuntimeError("lost acknowledgement"))

        monkeypatch.setattr(database.sessions, "begin", begin_with_commit_failure)
        first_result = start(first, one, "uncertain")
        assert first_result.status_code == 503
        assert calls == []
        rate_limited(start(second, two, "next"), "cooldown")
        recovered = start(first, one, "uncertain")
        assert recovered.status_code == 200
        assert recovered.json()["error"]["code"] == "RUN_INTERRUPTED"
        assert calls == []
