"""Read projections keep input freshness, validation and human decisions separate."""

from collections import defaultdict
from typing import Any

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.orm import Session, aliased

from proofline.application.views import (
    CaseDetail,
    CaseStatus,
    CaseSummary,
    DecisionView,
    ProposalView,
    RunView,
    SourceVersionSummary,
    SourceView,
    ValidationView,
)
from proofline.domain.assembly import AssemblyOutput
from proofline.domain.inputs import AnalysisInput, canonical_json, json_digest
from proofline.persistence.models import (
    AnalysisRun,
    Case,
    ProposalRevision,
    ReviewDecision,
    Source,
    SourceVersion,
    ValidationCheck,
)


def case_summary_query(profile: str) -> Select[Case, str, bool]:
    latest = select(
        ValidationCheck.id.label("id"),
        ValidationCheck.proposal_revision_id.label("proposal_id"),
        func.row_number()
        .over(
            partition_by=ValidationCheck.proposal_revision_id,
            order_by=(ValidationCheck.created_at.desc(), ValidationCheck.id.desc()),
        )
        .label("rank"),
    ).subquery()
    reviewed_check = aliased(ValidationCheck)
    status = sql_case(
        (ProposalRevision.id.is_(None), "unanalysed"),
        (ProposalRevision.content_revision != Case.content_revision, "stale"),
        (
            or_(ValidationCheck.id.is_(None), ValidationCheck.profile_version != profile),
            "needs_revalidation",
        ),
        (
            and_(ReviewDecision.id.is_not(None), reviewed_check.profile_version != profile),
            "needs_revalidation",
        ),
        (ReviewDecision.kind == "reject", "rejected"),
        (ValidationCheck.result == "blocked_for_approval", "blocked_for_approval"),
        (ReviewDecision.kind == "approve", "approved"),
        else_="needs_operator_review",
    ).label("display_status")
    running = exists(
        select(AnalysisRun.id).where(
            AnalysisRun.case_id == Case.id, AnalysisRun.status == "running"
        )
    )
    return (
        select(Case, status, running)
        .select_from(Case)
        .outerjoin(ProposalRevision, Case.current_proposal_revision_id == ProposalRevision.id)
        .outerjoin(latest, and_(latest.c.proposal_id == ProposalRevision.id, latest.c.rank == 1))
        .outerjoin(ValidationCheck, ValidationCheck.id == latest.c.id)
        .outerjoin(ReviewDecision, ReviewDecision.proposal_revision_id == ProposalRevision.id)
        .outerjoin(reviewed_check, ReviewDecision.validation_check_id == reviewed_check.id)
    )


def _case_fields(case: Case, status: str, running: bool) -> dict[str, Any]:
    return {
        "id": case.id,
        "title": case.title,
        "version": case.version,
        "content_revision": case.content_revision,
        "current_proposal_revision_id": case.current_proposal_revision_id,
        "status": status,
        "analysis_running": running,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def case_summary(case: Case, status: str, running: bool) -> CaseSummary:
    return CaseSummary.model_validate(_case_fields(case, status, running))


def case_detail(session: Session, case: Case, profile: str) -> CaseDetail:
    _, status, running = session.execute(
        case_summary_query(profile).where(Case.id == case.id)
    ).one()
    versions: dict[str, list[SourceVersionSummary]] = defaultdict(list)
    for version in session.scalars(
        select(SourceVersion)
        .where(SourceVersion.case_id == case.id)
        .order_by(SourceVersion.source_id, SourceVersion.sequence)
    ):
        versions[version.source_id].append(SourceVersionSummary.model_validate(version))
    sources = [
        SourceView.model_validate(
            {
                "case_id": source.case_id,
                "source_id": source.source_id,
                "kind": source.kind,
                "locator": source.locator,
                "title": source.title,
                "trust": source.trust,
                "position": source.position,
                "included": source.included,
                "current_version_id": source.current_version_id,
                "versions": versions[source.source_id],
            }
        )
        for source in session.scalars(
            select(Source).where(Source.case_id == case.id).order_by(Source.position)
        )
    ]
    return CaseDetail.model_validate(
        {
            **_case_fields(case, status, running),
            "user_text": case.user_text,
            "requested_fact_keys": case.requested_fact_keys,
            "money": case.money,
            "context": case.context,
            "sources": sources,
        }
    )


def run_view(session: Session, run: AnalysisRun) -> RunView:
    proposal_id = session.scalar(
        select(ProposalRevision.id).where(ProposalRevision.analysis_run_id == run.id)
    )
    return RunView.model_validate(
        {
            "id": run.id,
            "case_id": run.case_id,
            "mode": run.mode,
            "status": run.status,
            "content_revision": run.content_revision,
            "base_case_version": run.base_case_version,
            "input_digest": run.input_digest,
            "snapshot": AnalysisInput.model_validate_json(canonical_json(run.snapshot)),
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "generation_profile": run.generation_profile,
            "raw_output": run.raw_output,
            "usage": run.usage,
            "error": run.error,
            "proposal_revision_id": proposal_id,
        }
    )


def proposal_view(
    session: Session, proposal: ProposalRevision, case: Case, profile: str
) -> ProposalView:
    checks = session.scalars(
        select(ValidationCheck)
        .where(ValidationCheck.proposal_revision_id == proposal.id)
        .order_by(ValidationCheck.created_at, ValidationCheck.id)
    ).all()
    latest = checks[-1] if checks else None
    decision = session.scalar(
        select(ReviewDecision).where(ReviewDecision.proposal_revision_id == proposal.id)
    )
    decision_check = next(
        (check for check in checks if decision and check.id == decision.validation_check_id), None
    )
    active = case.current_proposal_revision_id == proposal.id
    stale = case.content_revision != proposal.content_revision
    validation_current = latest is not None and latest.profile_version == profile
    decision_profile_current = (
        decision_check is not None and decision_check.profile_version == profile
    )
    status: CaseStatus
    if stale:
        status = "stale"
    elif not validation_current or (decision is not None and not decision_profile_current):
        status = "needs_revalidation"
    elif decision is not None and decision.kind == "reject":
        status = "rejected"
    elif latest is not None and latest.result == "blocked_for_approval":
        status = "blocked_for_approval"
    elif decision is not None:
        status = "approved"
    else:
        status = "needs_operator_review"
    decision_current = (
        decision is not None
        and active
        and not stale
        and validation_current
        and decision_profile_current
        and decision.body_sha256 == proposal.body_sha256
        and decision.source_bindings_digest == json_digest(proposal.source_bindings)
        and status in {"approved", "rejected"}
    )
    return ProposalView.model_validate(
        {
            "id": proposal.id,
            "case_id": proposal.case_id,
            "analysis_run_id": proposal.analysis_run_id,
            "parent_revision_id": proposal.parent_revision_id,
            "origin": proposal.origin,
            "content_revision": proposal.content_revision,
            "input_digest": proposal.input_digest,
            "snapshot": AnalysisInput.model_validate_json(canonical_json(proposal.snapshot)),
            "source_bindings": proposal.source_bindings,
            "body": AssemblyOutput.model_validate(proposal.body),
            "body_sha256": proposal.body_sha256,
            "reason": proposal.reason,
            "created_at": proposal.created_at,
            "active": active,
            "stale": stale,
            "validation_current": validation_current,
            "status": status,
            "latest_validation": ValidationView.model_validate(latest) if latest else None,
            "validations": [ValidationView.model_validate(check) for check in checks],
            "blocking_issues": latest.issues if latest else [],
            "decision": DecisionView.model_validate(decision) if decision else None,
            "decision_current": decision_current,
            "case_version": case.version,
        }
    )
