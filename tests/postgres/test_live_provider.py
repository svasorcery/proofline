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
from proofline.application.state import case_or_404, current_input, read_session
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, ReviewDecision
from proofline.providers.factory import create_live_provider
from proofline.providers.openai_responses import OpenAIResponsesProvider
from proofline.providers.replay import ReplayCatalog

pytestmark = pytest.mark.postgres
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_groq_json_rejection_persists_safe_diagnostics_and_idempotency(setup):
    database, settings, catalog = setup
    settings.llm_provider = "groq"
    settings.llm_model = "offline-model"
    settings.llm_api_key = SecretStr("offline-diagnostic-key")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            400,
            headers={"x-request-id": "req_json_rejected"},
            json={
                "error": {
                    "code": "json_validate_failed",
                    "type": "invalid_request_error",
                    "param": "response_format.json_schema.schema",
                    "message": "offline-diagnostic-key private input",
                    "failed_generation": json.dumps(
                        {"case_id": "V02", "private-key-name": "private generation"}
                    ),
                }
            },
        )

    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    app = create_app(settings, database=database, provider=catalog, live_provider=live)
    with TestClient(app) as client:
        replay = client.post(
            "/api/v1/cases/V02/runs",
            json={"mode": "replay", "expected_case_version": 1, "replay_id": "v3-full-V02"},
            headers={"Idempotency-Key": "original"},
        ).json()
        original = client.get(f"/api/v1/proposals/{replay['proposal_revision_id']}").json()
        body = {"mode": "live", "expected_case_version": 2}
        first = client.post(
            "/api/v1/cases/V02/runs", json=body, headers={"Idempotency-Key": "json-failed"}
        )
        repeat = client.post(
            "/api/v1/cases/V02/runs", json=body, headers={"Idempotency-Key": "json-failed"}
        )
        assert first.status_code == 201 and repeat.status_code == 200
        assert first.json() == repeat.json() and len(calls) == 1
        run = first.json()
        assert run["status"] == "failed" and run["error"]["code"] == "INVALID_MODEL_OUTPUT"
        assert (
            run["raw_output"] is None
            and run["usage"] is None
            and run["proposal_revision_id"] is None
        )
        profile = run["generation_profile"]
        assert (
            profile["provider_request_id"] == "req_json_rejected" and profile["http_status"] == 400
        )
        assert profile["provider_error_code"] == "json_validate_failed"
        assert profile["provider_error_type"] == "invalid_request_error"
        assert profile["provider_error_param"] == "response_format.json_schema.schema"
        assert profile["provider_error_stage"] == "output_validation"
        assert profile["provider_output_field_diagnostics"] == {
            "missing_required_fields": ["facts", "follow_up", "readiness", "timeline"],
            "unexpected_field_count": 1,
        }
        assert profile["policy_version"] == "proofline-policy-v3.2-candidate-1"
        assert profile["api"] == "chat_completions"
        assert str(calls[0].url) == "https://api.groq.com/openai/v1/chat/completions"
        assert "offline-diagnostic-key" not in first.text and "private" not in first.text
        historical = client.get(f"/api/v1/proposals/{original['id']}").json()
        assert historical == original
        assert (
            client.get("/api/v1/cases/V02").json()["current_proposal_revision_id"] == original["id"]
        )


@pytest.mark.parametrize("invalid", [None, "extra_fact", "duplicate_key", "included_lookup"])
def test_choices_profile_keeps_model_text_and_canonical_card_separate(setup, invalid):
    database, _, catalog = setup
    record = next(r for r in catalog.recordings if r.option.replay_id == "v3-ablation-V01")
    body = json.loads(record.prepared.raw_output)
    target = next(s for s in record.input.catalog if s.locator == body["follow_up"]["locator"])
    included = next(s for s in record.input.catalog if s.included and s.kind == "document")
    settings = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=FIXTURES,
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        groq_prompt_profile="v3.3-choices-test",
        groq_test_lookup_sources={"V01": (target.source_id, included.source_id)},
        llm_api_key=SecretStr("offline-choices-key"),
    )
    wire = {
        **body,
        "facts": {
            item["key"]: {"value": item["value"], "evidence": item["evidence"]}
            for item in body["facts"]
        },
    }
    if invalid == "extra_fact":
        wire["facts"]["payment_outcome"] = {"value": "unconfirmed", "evidence": []}
    elif invalid == "included_lookup":
        wire["follow_up"] = {**body["follow_up"], "locator": included.locator}
    raw = json.dumps(wire, ensure_ascii=False, indent=2) + "\n"
    if invalid == "duplicate_key":
        raw = raw.replace('"facts": {', '"facts": {}, "facts": {', 1)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_choices",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen/qwen3.8-27b",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": raw},
                    }
                ],
            },
        )

    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    app = create_app(settings, database=database, provider=catalog, live_provider=live)
    with TestClient(app) as client:
        patch = client.patch(
            f"/api/v1/cases/V01/sources/{target.source_id}",
            json={"included": False, "expected_case_version": 1},
        )
        assert patch.status_code == 200
        path = "/api/v1/cases/V01/runs"
        command = {"mode": "live", "expected_case_version": patch.json()["version"]}
        created = client.post(path, json=command, headers={"Idempotency-Key": "choices"})
        assert created.status_code == 201
        run = created.json()
        read = client.get(f"/api/v1/runs/{run['id']}").json()
        assert read["raw_output"] == raw
        repeated = client.post(path, json=command, headers={"Idempotency-Key": "choices"})
        assert repeated.status_code == 200 and repeated.json() == run and len(calls) == 1
        profile = run["generation_profile"]
        assert profile["provider_schema_version"] == "assembly-choices-v1"
        assert profile["schema_version"] == "assembly-output-v1"
        assert profile["provider_lookup_source_ids"] == [target.source_id]
        assert (
            profile["prompt_version"]
            == client.get("/api/v1/capabilities").json()["live_profile"]["prompt_version"]
        )
        schema = calls[0]["response_format"]["json_schema"]["schema"]
        assert schema["$defs"]["RequestedFacts"]["required"] == ["alternative_acceptance"]
        if invalid:
            assert run["status"] == "failed" and run["error"]["code"] == "INVALID_MODEL_OUTPUT"
            assert run["proposal_revision_id"] is None
            assert client.get("/api/v1/cases/V01/proposals").json() == []
        else:
            assert run["status"] == "succeeded"
            proposal = client.get(f"/api/v1/proposals/{run['proposal_revision_id']}").json()
            assert proposal["body"] == body
            assert proposal["analysis_run_id"] == run["id"]
            assert proposal["status"] == "needs_operator_review" and proposal["decision"] is None


@pytest.mark.parametrize("variant", ["v9-original", "v9-two-questions", "v10-original"])
def test_confirmations_profile_persists_raw_output_and_checks_event_citations(setup, variant):
    database, _, catalog = setup
    archive = (
        "v10-groq-qwen-confirmations-v04-2026-11-18"
        if variant == "v10-original"
        else "v9-groq-qwen-choices-v04-2026-11-18"
    )
    path = FIXTURES.parent / "evals/live" / archive / "full-V04/run.json"
    archived = json.loads(path.read_text(encoding="utf-8"))
    raw = archived["raw_output"]
    if variant == "v9-two-questions":
        body = json.loads(raw)
        body["follow_up"]["requirements"].remove("original_acceptance_reply")
        raw = json.dumps(body, ensure_ascii=False)
    with read_session(database) as session:
        snapshot = current_input(session, case_or_404(session, "V04"))
    source = next(item for item in snapshot.catalog if item.source_id == "f71822cddbf648f2")
    assert source.text is not None
    # This span was reviewed in the synthetic case, independently of the returned questions.
    fragment = source.text.split("Текст принятого сообщения:\n", 1)[1].split("\n", 1)[0]
    reply_text = "Если там тихий номер с окнами во двор, согласен на соседний корпус."
    receipt_record = "2026-10-08T12:27:00+03:00 | inbound-message | клиент → отель"
    settings = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=FIXTURES,
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        groq_reasoning_effort="medium",
        groq_prompt_profile="v3.3-confirmations-test",
        groq_test_acceptance_replies={
            "V04": {
                "source_id": source.source_id,
                "source_version_id": source.version_id,
                "text_sha256": source.text_sha256,
                "fragment": fragment,
                "event_evidence": {
                    "sent_header": "Заголовок исходного письма Date: 2026-10-08T12:25:00+03:00",
                    "reply_text": reply_text,
                    "receipt_record": receipt_record,
                },
            }
        },
        llm_api_key=SecretStr("offline-confirmations"),
    )

    def handle(_request):
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_confirmations",
                "object": "chat.completion",
                "model": "qwen/qwen3.8-27b",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": raw},
                    }
                ],
            },
        )

    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    app = create_app(settings, database=database, provider=catalog, live_provider=live)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cases/V04/runs",
            json={"mode": "live", "expected_case_version": 1},
            headers={"Idempotency-Key": "confirmations"},
        )
        assert response.status_code == 201
        run = client.get(f"/api/v1/runs/{response.json()['id']}").json()
        assert run["raw_output"] == raw
        assert run["generation_profile"]["provider_covered_requirements"] == [
            "original_acceptance_reply"
        ]
        if variant == "v9-original":
            assert run["status"] == "failed" and run["error"]["code"] == "INVALID_MODEL_OUTPUT"
            assert run["proposal_revision_id"] is None
        else:
            assert run["status"] == "succeeded"
            proposal = client.get(f"/api/v1/proposals/{run['proposal_revision_id']}").json()
            assert proposal["body"]["follow_up"]["requirements"] == [
                "quiet_room",
                "courtyard_windows",
            ]
            assert proposal["decision"] is None
            profile = client.get("/api/v1/capabilities").json()["validation_profile"]
            assert proposal["latest_validation"]["profile_version"] == profile
            assert proposal["validation_current"]
            if variant == "v9-two-questions":
                assert proposal["status"] == "needs_operator_review"
                assert not proposal["blocking_issues"]
            else:
                assert proposal["status"] == "blocked_for_approval"
                assert [
                    (issue["code"], issue["path"]) for issue in proposal["blocking_issues"]
                ] == [("reply_text_missing_from_send", ["timeline", 1, "evidence"])]
                assert client.get("/api/v1/cases/V04").json()["status"] == "blocked_for_approval"
                revised = proposal["body"]
                revised["timeline"][1]["evidence"][0]["fragments"].append(reply_text)
                edit = client.post(
                    f"/api/v1/proposals/{proposal['id']}/revisions",
                    json={
                        "body": revised,
                        "reason": "Добавлена цитата ответа к отправке.",
                        "expected_case_version": proposal["case_version"],
                    },
                )
                assert edit.status_code == 201
                assert edit.json()["status"] == "needs_operator_review"
                assert edit.json()["decision"] is None
                assert client.get(f"/api/v1/runs/{run['id']}").json()["raw_output"] == raw


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


def transport(
    catalog, outcome="success", *, entered=None, release=None, case_id="V01", provider="openai"
):
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
        if provider == "groq":
            return httpx.Response(
                200,
                headers={"x-request-id": "req_live"},
                json={
                    "id": "chatcmpl_live",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "actual-test-model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": content[0].get("text"),
                                "refusal": "Cannot comply" if outcome == "refusal" else None,
                            },
                        }
                    ],
                    "usage": None,
                },
            )
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


@pytest.mark.parametrize("provider", ["openai", "groq"])
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
def test_http_live_is_idempotent_and_never_approves_or_falls_back(setup, outcome, code, provider):
    database, settings, catalog = setup
    if provider == "groq":
        settings.llm_provider = provider
        settings.llm_api_key = SecretStr("offline-test-key")
        settings.llm_model = settings.openai_model
        settings = settings.model_copy(update={"groq_reasoning_effort": "medium"})
    mock, requests = transport(catalog, outcome, provider=provider)
    live = create_live_provider(settings, transport=mock)
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
        if provider == "groq":
            assert requests[0]["reasoning_effort"] == "medium"
            assert run["generation_profile"]["reasoning_effort"] == "medium"
            assert run["generation_profile"]["request_profile"] == "groq-chat_completions-effort-v1"
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
            snapshot_text = (
                requests[0]["messages"][1]["content"]
                if provider == "groq"
                else requests[0]["input"]
            )
            assert json.loads(snapshot_text) == run["snapshot"]
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


@pytest.mark.parametrize("provider", ["groq", "deepseek", "anthropic", "openai"])
def test_configured_provider_preserves_snapshot_history_and_idempotency(setup, provider):
    from proofline.providers.factory import create_live_provider

    database, settings, catalog = setup
    settings.llm_provider = provider
    settings.llm_model = "new-provider-model"
    settings.llm_api_key = SecretStr("new-provider-secret")
    raw = next(
        r.prepared.raw_output for r in catalog.recordings if r.option.replay_id == "v3-full-V01"
    )
    calls = []

    def handle(request):
        calls.append(request)
        body = (
            {
                "id": "msg_native",
                "type": "message",
                "role": "assistant",
                "model": "returned-model",
                "content": [{"type": "text", "text": raw}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            }
            if provider == "anthropic"
            else {
                "id": "resp_native",
                "model": "returned-model",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": raw}]}],
                "usage": {"input_tokens": 12, "output_tokens": 3},
            }
        )
        if provider == "groq":
            body = {
                "id": "chatcmpl_native",
                "object": "chat.completion",
                "created": 1,
                "model": "returned-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": raw,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
        return httpx.Response(200, json=body)

    # The normal facade must select a provider from settings without connecting.
    selected = WorkflowService(database, settings, catalog)
    assert selected.capabilities().live_provider == provider
    assert selected.capabilities().live_model == "new-provider-model"
    assert selected.runs.live_provider.__class__ is create_live_provider(settings).__class__
    live = create_live_provider(settings, transport=httpx.MockTransport(handle))
    app = create_app(settings, database=database, provider=catalog, live_provider=live)
    with TestClient(app) as client:
        original = client.post(
            "/api/v1/cases/V01/runs",
            json={"mode": "replay", "expected_case_version": 1, "replay_id": "v3-full-V01"},
            headers={"Idempotency-Key": "before-live"},
        ).json()
        old_proposal = client.get(f"/api/v1/proposals/{original['proposal_revision_id']}").json()
        body = {"mode": "live", "expected_case_version": 2}
        first = client.post(
            "/api/v1/cases/V01/runs", json=body, headers={"Idempotency-Key": "selected-live"}
        )
        repeat = client.post(
            "/api/v1/cases/V01/runs", json=body, headers={"Idempotency-Key": "selected-live"}
        )
        assert first.status_code == 201 and repeat.status_code == 200
        assert first.json() == repeat.json() and len(calls) == 1
        run = first.json()
        assert run["status"] == "succeeded" and run["generation_profile"]["provider"] == provider
        assert run["usage"] == (
            {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
            if provider == "groq"
            else {"input_tokens": 12, "output_tokens": 3}
        )
        assert "new-provider-secret" not in first.text
        proposal = client.get(f"/api/v1/proposals/{run['proposal_revision_id']}").json()
        assert proposal["status"] == "needs_operator_review" and proposal["decision"] is None
        historical = client.get(f"/api/v1/proposals/{old_proposal['id']}").json()
        assert (
            historical["body"] == old_proposal["body"]
            and historical["snapshot"] == old_proposal["snapshot"]
        )


@pytest.mark.parametrize("provider", ["groq", "anthropic"])
@pytest.mark.parametrize("unsafe", ["bad\x00value", "bad\ud800value"])
def test_unsafe_provider_metadata_is_saved_as_failed_run(setup, provider, unsafe):
    from proofline.providers.factory import create_live_provider

    database, settings, catalog = setup
    settings.llm_provider = provider
    settings.llm_model = "selected-model"
    settings.llm_api_key = SecretStr("offline-test-key")
    raw = next(
        r.prepared.raw_output for r in catalog.recordings if r.option.replay_id == "v3-full-V01"
    )
    body = {"id": unsafe, "model": "model", "usage": {"input_tokens": 12, "extra": unsafe}}
    if provider == "anthropic":
        body.update(
            type="message",
            role="assistant",
            stop_reason="end_turn",
            content=[{"type": "text", "text": raw}],
        )
    else:
        body.update(
            object="chat.completion",
            created=1,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": raw,
                    },
                }
            ],
        )
    mock = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            content=json.dumps(body, ensure_ascii=True).encode(),
            headers={"content-type": "application/json"},
        )
    )
    service = WorkflowService(
        database, settings, catalog, live_provider=create_live_provider(settings, transport=mock)
    )
    command = RunCreate(expected_case_version=1, mode="live")
    run, _ = service.runs.create("V01", command, "unsafe-metadata")
    repeated, _ = service.runs.create("V01", command, "unsafe-metadata")
    assert run == repeated and run.status == "failed"
    assert run.error["code"] == "INVALID_MODEL_OUTPUT" and run.proposal_revision_id is None
    assert run.usage is None and run.generation_profile["provider_response_id"] is None
    diagnostic = json.loads(run.raw_output)
    assert diagnostic["generation_profile"]["provider_response_id"] == unsafe
    assert diagnostic["usage"]["extra"] == unsafe
