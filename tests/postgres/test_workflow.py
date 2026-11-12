import os
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from proofline.application.commands import (
    CaseCreate,
    CasePatch,
    DecisionCreate,
    RevisionCreate,
    RunCreate,
    SourcePatch,
    SourceVersionCreate,
)
from proofline.application.errors import ApplicationError
from proofline.application.seed import seed_demo
from proofline.application.service import WorkflowService
from proofline.config import Settings
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, ProposalRevision, ReviewDecision
from proofline.providers.replay import ReplayCatalog

pytestmark = pytest.mark.postgres
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def service(postgres_engine: Engine) -> WorkflowService:
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(FIXTURES)
    seed_demo(database, catalog)
    return WorkflowService(
        database,
        Settings(database_url=os.environ["TEST_DATABASE_URL"], fixtures_dir=FIXTURES),
        catalog,
    )


def analyse(service: WorkflowService, case_id: str = "V01", key: str = "first") -> str:
    case = service.cases.get(case_id)
    run, status = service.runs.create(
        case_id,
        RunCreate(
            expected_case_version=case.version, mode="replay", replay_id=f"v3-full-{case_id}"
        ),
        key,
    )
    assert status == 201 and run.status == "succeeded"
    assert run.proposal_revision_id is not None
    return run.proposal_revision_id


def test_seed_never_overwrites_operator_edits(service: WorkflowService) -> None:
    original = service.cases.get("V01")
    changed = service.cases.update(
        "V01", CasePatch(expected_case_version=original.version, title="My case title")
    )
    assert seed_demo(service.database, ReplayCatalog(FIXTURES)) == []
    assert service.cases.get("V01").title == "My case title"
    assert changed.content_revision == original.content_revision
    assert len(service.runs.options("V01")) == 1


def test_review_is_versioned_idempotent_and_never_automatic(service: WorkflowService) -> None:
    proposal_id = analyse(service)
    proposal = service.proposals.get(proposal_id)
    assert proposal.status == "needs_operator_review" and proposal.decision is None
    command = DecisionCreate(
        expected_case_version=proposal.case_version, kind="approve", reason="Checked"
    )
    decision, status = service.proposals.decide(proposal_id, command, "review-1")
    repeated, repeat_status = service.proposals.decide(proposal_id, command, "review-1")
    assert status == 201 and repeat_status == 200 and decision == repeated
    assert service.proposals.get(proposal_id).decision_current
    assert service.cases.get("V01").status == "approved"
    assert len(service.cases.list(status="approved").items) == 1
    with pytest.raises(ApplicationError, match="already") as error:
        service.proposals.decide(
            proposal_id,
            DecisionCreate(
                expected_case_version=decision.result_case_version,
                kind="reject",
                reason="Changed mind",
            ),
            "another-review",
        )
    assert error.value.code == "ALREADY_REVIEWED"


def test_blocked_recording_requires_a_new_corrected_revision(service: WorkflowService) -> None:
    proposal_id = analyse(service, "V04")
    original = service.proposals.get(proposal_id)
    with pytest.raises(ApplicationError) as error:
        service.proposals.decide(
            proposal_id,
            DecisionCreate(
                expected_case_version=original.case_version, kind="approve", reason="Try"
            ),
            "approval",
        )
    assert error.value.code == "VALIDATION_BLOCKED"
    assert service.cases.get("V04").version == original.case_version
    body = original.body.model_copy(deep=True)
    body.timeline = [event for event in body.timeline if event.event_type != "alternative_accepted"]
    body.follow_up.action = "ask_user"
    body.follow_up.locator = None
    corrected = service.proposals.revise(
        proposal_id,
        RevisionCreate(
            expected_case_version=original.case_version,
            body=body,
            reason="Request the missing hotel confirmation",
        ),
    )
    assert corrected.status == "needs_operator_review"
    assert corrected.parent_revision_id == original.id
    assert service.proposals.get(original.id).body == original.body
    decision, _ = service.proposals.decide(
        corrected.id,
        DecisionCreate(
            expected_case_version=corrected.case_version,
            kind="approve",
            reason="Checked source text",
        ),
        "corrected-approval",
    )
    assert decision.actor == "demo_operator"


def test_source_updates_preserve_old_proof_and_invalidate_approval(
    service: WorkflowService,
) -> None:
    proposal_id = analyse(service)
    proposal = service.proposals.get(proposal_id)
    decision, _ = service.proposals.decide(
        proposal_id,
        DecisionCreate(
            expected_case_version=proposal.case_version, kind="approve", reason="Checked"
        ),
        "decision",
    )
    source = service.cases.get("V01").sources[0]
    old = service.cases.get_version("V01", source.source_id, source.current_version_id)
    changed = service.cases.add_version(
        "V01",
        source.source_id,
        SourceVersionCreate(
            expected_case_version=decision.result_case_version, text=old.text + "\nNew information."
        ),
    )
    historic = service.proposals.get(proposal_id)
    assert historic.stale and not historic.decision_current
    assert historic.decision is not None and historic.decision.kind == "approve"
    assert service.cases.get_version("V01", source.source_id, old.id).text == old.text
    assert changed.content_revision == 2 and service.runs.options("V01") == []
    with service.database.sessions() as session:
        assert len(session.scalars(select(AnalysisRun)).all()) == 1
        assert len(session.scalars(select(ProposalRevision)).all()) == 1
        assert len(session.scalars(select(ReviewDecision)).all()) == 1


def test_noop_input_patches_do_not_invalidate_an_approved_revision(
    service: WorkflowService,
) -> None:
    proposal_id = analyse(service)
    proposal = service.proposals.get(proposal_id)
    service.proposals.decide(
        proposal_id,
        DecisionCreate(
            expected_case_version=proposal.case_version, kind="approve", reason="Checked"
        ),
        "approve",
    )
    original = service.cases.get("V01")
    unchanged = service.cases.update(
        "V01",
        CasePatch(
            expected_case_version=original.version,
            user_text=original.user_text,
            requested_fact_keys=original.requested_fact_keys,
            money=original.money,
            context=original.context,
        ),
    )
    assert unchanged.version == original.version
    source = unchanged.sources[0]
    unchanged = service.cases.update_source(
        "V01",
        source.source_id,
        SourcePatch(
            expected_case_version=unchanged.version,
            title=source.title,
            included=source.included,
            current_version_id=source.current_version_id,
            position=source.position,
        ),
    )
    assert (
        unchanged.version == original.version
        and unchanged.content_revision == original.content_revision
    )
    assert service.proposals.get(proposal_id).decision_current


def test_json_boolean_change_is_not_lost_to_python_numeric_equality(
    service: WorkflowService,
) -> None:
    case = service.cases.create(
        CaseCreate(
            title="Typed context", user_text="Example", requested_fact_keys=[], context={"flag": 1}
        )
    )
    service.cases.update(case.id, CasePatch(expected_case_version=1, context={"flag": True}))
    persisted = service.cases.get(case.id)
    assert persisted.context["flag"] is True
    assert persisted.version == 2 and persisted.content_revision == 2
