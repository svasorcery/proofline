"""The isolated profile stores selected IDs and the resolved card separately."""

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


@pytest.mark.parametrize(
    ("case_id", "variant"),
    [
        ("V04", "valid"),
        ("V04", "reviewed_reply_edit"),
        ("V02", "valid"),
        ("V04", "unknown_id"),
        ("V04", "wrong_event"),
        ("V02", "empty_timeline"),
        ("V02", "reverse_order"),
    ],
)
def test_citation_profile_persists_original_ids_and_canonical_card(
    postgres_engine, case_id, variant
):
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(ROOT / "fixtures")
    seed_demo(database, catalog)
    archive = (
        ROOT
        / "evals/live"
        / (
            "v11-groq-qwen-same-document-v04-2026-11-18"
            if case_id == "V04"
            else "v8-groq-qwen-choices-v02-v04-2026-11-17"
        )
    )
    previous = json.loads((archive / f"full-{case_id}/run.json").read_text(encoding="utf-8"))
    answer = json.loads(previous["raw_output"])
    with read_session(database) as session:
        snapshot = current_input(session, case_or_404(session, case_id))
    bindings = None
    required_events = None
    if case_id == "V04":
        binding = json.loads((archive / "preflight.json").read_text(encoding="utf-8"))[
            "reply_binding"
        ]
        source = next(s for s in snapshot.catalog if s.source_id == binding["source_id"])
        assert binding["text_sha256"] == source.text_sha256
        bindings = {case_id: {**binding, "source_version_id": source.version_id}}
    else:
        sources = {source.source_id: source.version() for source in snapshot.catalog}
        marks = []
        for event, (source_id, line) in zip(
            answer["timeline"],
            [("9edd7227853c42a4", 0), ("5c3fe2fc12a3430e", 1), ("5c3fe2fc12a3430e", 2)],
            strict=True,
        ):
            source = sources[source_id]
            marks.append(
                {
                    "event_type": event["event_type"],
                    "time": event["time"],
                    "source_id": source_id,
                    "source_version_id": source.version_id,
                    "text_sha256": source.text_sha256,
                    "fragment": source.text.split("\n")[line],
                }
            )
        required_events = {case_id: marks}
    settings = Settings(
        _env_file=None,
        database_url=os.environ["TEST_DATABASE_URL"],
        fixtures_dir=ROOT / "fixtures",
        live_enabled=True,
        llm_provider="groq",
        llm_model="qwen/qwen3.8-27b",
        llm_api_key="offline-fake-key",
        groq_prompt_profile="v3.3-citation-ids-test",
        groq_reasoning_effort="medium",
        groq_test_acceptance_replies=bindings,
        groq_test_required_events=required_events,
        groq_test_case_evidence={} if variant == "reviewed_reply_edit" else None,
    )
    responses = []
    expected_quotes = {}

    def reply(http_request):
        request = json.loads(http_request.content)
        assert request["reasoning_effort"] == "medium"
        wire = request["response_format"]["json_schema"]
        assert wire["strict"] is True and wire["name"] == "assembly_citation_ids"
        assert "minItems" not in wire["schema"]["properties"]["timeline"]
        payload = json.loads(request["messages"][1]["content"])
        assert payload["case_id"] == case_id
        lines = {source["source_id"]: source["citation_lines"] for source in payload["catalog"]}
        for source in payload["catalog"]:
            assert "text" not in source
        offered_ids = [row["citation_id"] for rows in lines.values() for row in rows]
        assert len(offered_ids) == 10
        for definition in ("FactWithCitationIds", "TimelineEventWithCitationIds"):
            assert wire["schema"]["$defs"][definition]["properties"]["evidence"]["items"] == {
                "type": "string",
                "enum": offered_ids,
            }

        def ids(source_id, *numbers):
            rows = [lines[source_id][number - 1] for number in numbers]
            expected_quotes.update({row["citation_id"]: row["text"] for row in rows})
            return [row["citation_id"] for row in rows]

        if case_id == "V04":
            message = "f71822cddbf648f2"
            assert (
                "original_acceptance_reply" not in wire["schema"]["$defs"]["RequirementKey"]["enum"]
            )
            facts = {
                "alternative_acceptance": [
                    *ids(message, 4, 5),
                    *ids("60605d18ce3c437c", 1),
                ]
            }
            events = {
                "alternative_offered": ids("7c4b78e8ce5a4af4", 2, 3),
                "customer_replied": ids(message, 2, 1 if variant == "wrong_event" else 4),
                "customer_message_received": ids(message, 1),
            }
        else:
            form, log = "5c3fe2fc12a3430e", "9edd7227853c42a4"
            facts = {
                "cancellation_status": ids(log, 1),
                "withdrawal_timing": ids(form, 4, 5) + ids(log, 2, 3),
            }
            events = {
                "supplier_cancelled": ids(log, 1),
                "customer_replied": ids(form, 2, 4),
                "customer_message_received": ids(form, 3, 4),
            }
        if variant == "unknown_id":
            next(iter(facts.values())).append("q-unknown")
        for key, fact in answer["facts"].items():
            fact["evidence"] = facts[key]
        for event in answer["timeline"]:
            event["evidence"] = events[event["event_type"]]
        if variant == "empty_timeline":
            answer["timeline"] = []
        elif variant == "reverse_order":
            answer["timeline"].reverse()
        raw = json.dumps(answer, ensure_ascii=False, indent=2) + "\n"
        responses.append(raw)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_citations",
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

    provider = create_live_provider(settings, transport=httpx.MockTransport(reply))
    app = create_app(settings, database=database, provider=catalog, live_provider=provider)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/cases/{case_id}/runs",
            json={"mode": "live", "expected_case_version": 1},
            headers={"Idempotency-Key": "citation-ids-test"},
        )
        assert response.status_code == 201 and len(responses) == 1
        run = response.json()
        assert run["raw_output"] == responses[0]
        assert run["generation_profile"]["provider_schema_version"] == "assembly-citation-ids-v1"
        with read_session(database) as session:
            saved = session.get(AnalysisRun, run["id"])
            assert saved.raw_output == responses[0]
            if variant == "unknown_id":
                assert saved.status == "failed" and saved.error["code"] == "INVALID_MODEL_OUTPUT"
                assert run["proposal_revision_id"] is None
                return
            assert saved.status == "succeeded"
            card = session.get(ProposalRevision, run["proposal_revision_id"])
            assert card.analysis_run_id == saved.id and card.snapshot == saved.snapshot
            body = card.body
        proposal = client.get(f"/api/v1/proposals/{run['proposal_revision_id']}").json()
        assert proposal["body"] == body and proposal["decision"] is None
        assert body["follow_up"] == answer["follow_up"]
        expected_timeline = (
            list(reversed(answer["timeline"])) if variant == "reverse_order" else answer["timeline"]
        )
        assert [event["time"] for event in body["timeline"]] == [
            event["time"] for event in expected_timeline
        ]
        for item, selection in [
            *((fact, answer["facts"][fact["key"]]) for fact in body["facts"]),
            *zip(body["timeline"], expected_timeline, strict=True),
        ]:
            assert [fragment for entry in item["evidence"] for fragment in entry["fragments"]] == [
                expected_quotes[cid] for cid in selection["evidence"]
            ]
        if variant in {"wrong_event", "empty_timeline"}:
            assert proposal["status"] == "blocked_for_approval"
            assert [issue["code"] for issue in proposal["blocking_issues"]] == [
                "reply_text_missing_from_send"
                if variant == "wrong_event"
                else "required_timeline_event_missing"
            ]
        else:
            assert proposal["status"] == "needs_operator_review"
            assert not proposal["blocking_issues"]
        if variant == "reviewed_reply_edit":
            body["follow_up"]["requirements"].append("original_acceptance_reply")
            version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
            edited = client.post(
                f"/api/v1/proposals/{proposal['id']}/revisions",
                json={"expected_case_version": version, "body": body, "reason": "Test edit"},
            )
            assert edited.status_code == 201
            revised = edited.json()
            assert revised["status"] == "blocked_for_approval"
            assert [issue["code"] for issue in revised["blocking_issues"]] == [
                "follow_up_already_covered"
            ]
            assert client.get(f"/api/v1/runs/{run['id']}").json()["raw_output"] == responses[0]
