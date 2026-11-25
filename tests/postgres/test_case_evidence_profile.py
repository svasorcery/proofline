"""Exercise the test profile through PostgreSQL and an in-memory Groq transport."""

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from proofline.api.app import create_app
from proofline.application.seed import seed_demo
from proofline.application.state import case_or_404, current_input, read_session
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, ProposalRevision
from proofline.providers.factory import create_live_provider
from proofline.providers.replay import ReplayCatalog

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def recorded_citations(request):
    snapshot = json.loads(request["messages"][1]["content"])
    return [
        (source["source_id"], index, row["text"], row["citation_id"])
        for source in snapshot["catalog"]
        for index, row in enumerate(source.get("citation_lines", ()))
        if row["citation_id"] is not None
    ]


@pytest.mark.parametrize(
    ("version", "case_id", "enabled", "issues"),
    [
        (25, "V02", True, ["follow_up_out_of_scope", "follow_up_out_of_scope"]),
        (25, "V03", True, ["refund_settlement_link_missing", "required_timeline_event_missing"]),
        (25, "V02", False, []),
        (25, "V03", False, []),
        (15, "V02", True, []),
        (19, "V03", True, []),
        (29, "V05", True, ["notification_time_used_as_cancellation"]),
        (20, "V05", True, []),
        (29, "V05", False, []),
    ],
)
def test_case_evidence_profile_keeps_raw_response_and_blocks_approval(
    postgres_engine, version, case_id, enabled, issues
):
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(ROOT / "fixtures")
    seed_demo(database, catalog)
    (archive,) = (ROOT / "evals/live").glob(f"v{version}-*")
    folder = archive / f"full-{case_id}"
    old_run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    old_request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    old_card = json.loads((folder / "proposal.json").read_text(encoding="utf-8"))
    required_events = None
    if case_id == "V05" and enabled:
        (latest,) = (ROOT / "evals/live").glob("v29-*")
        preflight = json.loads((latest / "preflight.json").read_text(encoding="utf-8"))
        marks = preflight["bound_settings"]["groq_test_required_events"]["V05"]
        with read_session(database) as session:
            snapshot = current_input(session, case_or_404(session, case_id))
        versions = {source.source_id: source.version_id for source in snapshot.catalog}
        required_events = {
            case_id: [{**mark, "source_version_id": versions[mark["source_id"]]} for mark in marks]
        }
    settings = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=ROOT / "fixtures",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        llm_api_key="offline-fake-key",
        groq_prompt_profile="v3.3-citation-ids-test",
        groq_reasoning_effort="none",
        model_max_output_tokens=1000,
        groq_test_required_events=required_events,
        groq_test_case_evidence=(
            json.loads(
                (ROOT / "fixtures/live/groq-stage4-case-evidence.json").read_text(encoding="utf-8")
            )
            if enabled
            else None
        ),
    )
    responses = []
    wire_requests = []

    def respond(request):
        wire = json.loads(request.content)
        wire_requests.append(wire)
        substitutions = {}
        for old, current in zip(
            recorded_citations(old_request), recorded_citations(wire), strict=True
        ):
            assert old[:3] == current[:3]
            substitutions[old[3]] = current[3]
        payload = json.loads(old_run["raw_output"])
        for item in [*payload["facts"].values(), *payload["timeline"]]:
            item["evidence"] = [substitutions[value] for value in item["evidence"]]
        raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        responses.append(raw)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_archived",
                "object": "chat.completion",
                "created": 0,
                "model": settings.selected_model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": raw},
                    }
                ],
            },
        )

    provider = create_live_provider(settings, transport=httpx.MockTransport(respond))
    with TestClient(
        create_app(settings, database=database, provider=catalog, live_provider=provider)
    ) as client:
        case_path = f"/api/v1/cases/{case_id}"
        response = client.post(
            case_path + "/runs",
            json={"mode": "live", "expected_case_version": 1},
            headers={"Idempotency-Key": "archived-case-evidence"},
        )
        assert response.status_code == 201
        run = response.json()
        assert run["status"] == "succeeded"
        assert len(responses) == len(wire_requests) == 1
        assert run["raw_output"] == responses[0]
        proposal_path = f"/api/v1/proposals/{run['proposal_revision_id']}"
        proposal = client.get(proposal_path).json()
        assert proposal["body"] == old_card["body"]
        assert proposal["decision"] is None
        assert [issue["code"] for issue in proposal["blocking_issues"]] == issues
        assert proposal["status"] == ("blocked_for_approval" if issues else "needs_operator_review")
        assert (
            "case-evidence-test-v1" in proposal["latest_validation"]["profile_version"]
        ) == enabled
        if case_id == "V05":
            assert (
                "required-timeline-test-v4" in proposal["latest_validation"]["profile_version"]
            ) == enabled
            if issues:
                assert proposal["blocking_issues"][0]["path"] == ["timeline", 3, "evidence"]
        if issues:
            decision = client.post(
                proposal_path + "/decision",
                json={
                    "kind": "approve",
                    "reason": "Test approval",
                    "expected_case_version": client.get(case_path).json()["version"],
                },
                headers={"Idempotency-Key": "test-blocked-approval"},
            )
            assert decision.status_code == 409
            assert decision.json()["code"] == "VALIDATION_BLOCKED"
            assert client.get(proposal_path).json()["decision"] is None
        assert client.get(f"/api/v1/runs/{run['id']}").json()["raw_output"] == responses[0]
        with database.sessions() as session:
            assert session.get(AnalysisRun, run["id"]).raw_output == responses[0]
            assert session.get(ProposalRevision, proposal["id"]).body == old_card["body"]

    wire = wire_requests[0]["response_format"]["json_schema"]
    assert wire["strict"] is True
    expected = (
        ["original_customer_message", "delivery_receipt"]
        if case_id in {"V02", "V05"}
        else ["bank_settlement_document"]
    )
    if case_id == "V05" and not enabled:
        (v20,) = (ROOT / "evals/live").glob("v20-*")
        old_request = json.loads((v20 / "full-V05/request.json").read_text(encoding="utf-8"))
    legacy = old_request["response_format"]["json_schema"]["schema"]["$defs"]["RequirementKey"]
    assert wire["schema"]["$defs"]["RequirementKey"]["enum"] == (
        expected if enabled else legacy["enum"]
    )
