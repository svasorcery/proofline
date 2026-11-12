"""Coordinate real transactions with events; no SQLite or timing-based sleeps."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from proofline.application.commands import (
    ActivateProposal,
    CasePatch,
    DecisionCreate,
    RunCreate,
    VersionCommand,
)
from proofline.application.errors import ApplicationError
from proofline.application.seed import seed_demo
from proofline.application.service import WorkflowService
from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput
from proofline.persistence.database import Database
from proofline.persistence.migrate import upgrade_database
from proofline.persistence.models import AnalysisRun, ProposalRevision, ReviewDecision
from proofline.providers.replay import GenerationResult, PreparedReplay, ReplayCatalog, ReplayOption

pytestmark = pytest.mark.postgres
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class BlockingReplay:
    def __init__(self, catalog: ReplayCatalog) -> None:
        self.catalog = catalog
        self.entered, self.release = Event(), Event()
        self.lock = Lock()
        self.calls = 0

    def options(self, input_data: AnalysisInput) -> list[ReplayOption]:
        return self.catalog.options(input_data)

    def prepare(self, replay_id: str, input_data: AnalysisInput) -> PreparedReplay:
        return self.catalog.prepare(replay_id, input_data)

    def generate(self, prepared: PreparedReplay) -> GenerationResult:
        with self.lock:
            self.calls += 1
        self.entered.set()
        if not self.release.wait(20):
            raise TimeoutError("Test did not release the controlled provider")
        return self.catalog.generate(prepared)


@pytest.fixture
def setup(postgres_engine: Engine) -> tuple[Database, Settings, ReplayCatalog]:
    upgrade_database(postgres_engine)
    database = Database(postgres_engine, sessionmaker(postgres_engine, expire_on_commit=False))
    catalog = ReplayCatalog(FIXTURES)
    seed_demo(database, catalog)
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"], fixtures_dir=FIXTURES)
    return database, settings, catalog


def test_same_key_while_running_does_not_generate_twice(setup: tuple) -> None:
    database, settings, catalog = setup
    provider = BlockingReplay(catalog)
    service = WorkflowService(database, settings, provider)
    command = RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.runs.create, "V01", command, "same-key")
        try:
            assert provider.entered.wait(5)
            repeated, status = service.runs.create("V01", command, "same-key")
            assert status == 202 and repeated.status == "running"
            with pytest.raises(ApplicationError) as conflict:
                service.runs.create("V01", command, "another-key")
            assert conflict.value.code == "ANALYSIS_ALREADY_RUNNING"
            with pytest.raises(ApplicationError) as changed:
                service.runs.create(
                    "V01", command.model_copy(update={"expected_case_version": 2}), "same-key"
                )
            assert changed.value.code == "IDEMPOTENCY_CONFLICT"
            assert provider.calls == 1
        finally:
            provider.release.set()
        completed, status = first.result(timeout=5)
    assert status == 201 and completed.id == repeated.id
    retry, status = service.runs.create("V01", command, "same-key")
    assert status == 200 and retry.id == completed.id and provider.calls == 1


def test_edit_during_generation_keeps_a_stale_candidate(setup: tuple) -> None:
    database, settings, catalog = setup
    provider = BlockingReplay(catalog)
    service = WorkflowService(database, settings, provider)
    old = service.cases.get("V01")
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = pool.submit(
            service.runs.create,
            "V01",
            RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
            "analysis",
        )
        try:
            assert provider.entered.wait(5)
            edit = pool.submit(
                service.cases.update,
                "V01",
                CasePatch(
                    expected_case_version=1, user_text=old.user_text + " Additional information."
                ),
            )
            changed = edit.result(timeout=5)  # Would block if generation held the Case lock.
            assert changed.version == 2
        finally:
            provider.release.set()
        completed, _ = run.result(timeout=5)
    proposal = service.proposals.get(completed.proposal_revision_id)
    assert proposal.stale and not proposal.active
    assert proposal.snapshot.user_text == old.user_text
    assert service.cases.get("V01").current_proposal_revision_id is None
    with pytest.raises(ApplicationError) as error:
        service.proposals.activate(
            "V01", ActivateProposal(expected_case_version=2, proposal_revision_id=proposal.id)
        )
    assert error.value.code == "PROPOSAL_STALE"


def test_late_generation_does_not_replace_operator_approval(setup: tuple) -> None:
    database, settings, catalog = setup
    first_service = WorkflowService(database, settings, catalog)
    original, _ = first_service.runs.create(
        "V01", RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"), "first"
    )
    provider = BlockingReplay(catalog)
    service = WorkflowService(database, settings, provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            service.runs.create,
            "V01",
            RunCreate(expected_case_version=2, mode="replay", replay_id="v3-full-V01"),
            "new-analysis",
        )
        try:
            assert provider.entered.wait(5)
            decision, _ = service.proposals.decide(
                original.proposal_revision_id,
                DecisionCreate(expected_case_version=2, kind="approve", reason="Checked"),
                "review",
            )
            assert decision.result_case_version == 3
        finally:
            provider.release.set()
        completed, _ = pending.result(timeout=5)
    case = service.cases.get("V01")
    assert (
        case.current_proposal_revision_id == original.proposal_revision_id
        and case.status == "approved"
    )
    candidate = service.proposals.get(completed.proposal_revision_id)
    assert not candidate.active and not candidate.stale and candidate.decision is None


def test_restart_recovery_wins_over_late_completion(setup: tuple) -> None:
    database, settings, catalog = setup
    provider = BlockingReplay(catalog)
    old = WorkflowService(database, settings, provider, owner_instance_id="old-process")
    new = WorkflowService(database, settings, catalog, owner_instance_id="new-process")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            old.runs.create,
            "V01",
            RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
            "interrupted",
        )
        try:
            assert provider.entered.wait(5)
            assert new.recover_interrupted_runs() == 1
            assert new.recover_interrupted_runs() == 0
            fresh, _ = new.runs.create(
                "V01",
                RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
                "explicit-retry",
            )
        finally:
            provider.release.set()
        late, _ = pending.result(timeout=5)
    assert late.status == "failed" and late.error["code"] == "PROCESS_INTERRUPTED"
    assert late.proposal_revision_id is None
    assert new.cases.get("V01").current_proposal_revision_id == fresh.proposal_revision_id
    with database.sessions() as session:
        assert len(session.scalars(select(AnalysisRun)).all()) == 2
        assert len(session.scalars(select(ProposalRevision)).all()) == 1


def test_two_edits_with_one_expected_version_have_one_winner(setup: tuple) -> None:
    database, settings, catalog = setup
    service = WorkflowService(database, settings, catalog)
    barrier = Barrier(2)

    def edit(title: str) -> str:
        barrier.wait(timeout=5)
        try:
            service.cases.update("V01", CasePatch(expected_case_version=1, title=title))
            return "saved"
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["First tab", "Second tab"]))
    assert sorted(results) == ["CASE_VERSION_CONFLICT", "saved"]
    assert service.cases.get("V01").version == 2


def test_two_same_key_decisions_record_one_final_decision(setup: tuple) -> None:
    database, settings, catalog = setup
    service = WorkflowService(database, settings, catalog)
    run, _ = service.runs.create(
        "V01",
        RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
        "analysis",
    )
    barrier = Barrier(2)

    def decide() -> tuple:
        barrier.wait(timeout=5)
        return service.proposals.decide(
            run.proposal_revision_id,
            DecisionCreate(expected_case_version=2, kind="approve", reason="Checked"),
            "decision",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert {result[1] for result in results} == {200, 201}
    assert results[0][0] == results[1][0]
    with database.sessions() as session:
        assert len(session.scalars(select(ReviewDecision)).all()) == 1


def test_profile_change_requires_revalidation_and_a_new_revision_for_rereview(setup: tuple) -> None:
    database, settings, catalog = setup
    original = WorkflowService(database, settings, catalog)
    run, _ = original.runs.create(
        "V01",
        RunCreate(expected_case_version=1, mode="replay", replay_id="v3-full-V01"),
        "analysis",
    )
    changed = WorkflowService(
        database, settings.model_copy(update={"validation_profile": "v2"}), catalog
    )
    proposal = changed.proposals.get(run.proposal_revision_id)
    assert proposal.status == "needs_revalidation" and not proposal.validation_current
    with pytest.raises(ApplicationError) as error:
        changed.proposals.decide(
            proposal.id,
            DecisionCreate(expected_case_version=2, kind="approve", reason="Try"),
            "review",
        )
    assert error.value.code == "VALIDATION_PROFILE_CHANGED"
    checked = changed.proposals.validate(proposal.id, VersionCommand(expected_case_version=2))
    decision, _ = changed.proposals.decide(
        proposal.id,
        DecisionCreate(
            expected_case_version=checked.case_version, kind="approve", reason="Checked"
        ),
        "review",
    )
    newest = WorkflowService(
        database, settings.model_copy(update={"validation_profile": "v3"}), catalog
    )
    assert not newest.proposals.get(proposal.id).decision_current
    rechecked = newest.proposals.validate(
        proposal.id, VersionCommand(expected_case_version=decision.result_case_version)
    )
    assert rechecked.validation_current and not rechecked.decision_current
    assert rechecked.status == "needs_revalidation"
    with pytest.raises(ApplicationError) as reviewed:
        newest.proposals.decide(
            proposal.id,
            DecisionCreate(
                expected_case_version=rechecked.case_version, kind="approve", reason="Try again"
            ),
            "new-review",
        )
    assert reviewed.value.code == "ALREADY_REVIEWED"
