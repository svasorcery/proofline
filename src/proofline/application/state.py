"""Shared aggregate invariants used inside explicit database transactions."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from proofline.application.errors import ApplicationError
from proofline.config import Settings
from proofline.domain.assembly import AssemblyOutput
from proofline.domain.inputs import (
    AnalysisInput,
    canonical_json,
    compatibility_digest,
    source_bindings,
)
from proofline.persistence.database import Database
from proofline.persistence.models import (
    Case,
    ProposalRevision,
    Source,
    SourceVersion,
    ValidationCheck,
)
from proofline.validation import validate_assembly


@contextmanager
def read_session(database: Database) -> Iterator[Session]:
    """A multi-query read view must not mix two aggregate revisions."""
    with database.sessions() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        yield session


def case_or_404(session: Session, case_id: str, *, lock: bool = False) -> Case:
    query = select(Case).where(Case.id == case_id)
    if lock:
        query = query.with_for_update()
    case = session.scalar(query)
    if case is None:
        raise ApplicationError("CASE_NOT_FOUND", "Case not found.", status_code=404)
    return case


def proposal_or_404(session: Session, revision_id: str) -> ProposalRevision:
    proposal = session.get(ProposalRevision, revision_id)
    if proposal is None:
        raise ApplicationError("PROPOSAL_NOT_FOUND", "Proposal not found.", status_code=404)
    return proposal


def require_version(case: Case, expected: int) -> None:
    if case.version != expected:
        raise ApplicationError(
            "CASE_VERSION_CONFLICT",
            "The case changed; reload before editing.",
            details={"expected_case_version": expected, "actual_case_version": case.version},
        )


def touch(case: Case, *, content: bool = False) -> None:
    case.version += 1
    if content:
        case.content_revision += 1
    case.updated_at = datetime.now(UTC)


def require_key(value: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise ApplicationError(
            "INVALID_REQUEST",
            "Idempotency-Key must be nonblank and at most 128 characters.",
            status_code=422,
        )


def current_input(session: Session, case: Case) -> AnalysisInput:
    rows = session.execute(
        select(Source, SourceVersion)
        .outerjoin(SourceVersion, Source.current_version_id == SourceVersion.id)
        .where(Source.case_id == case.id)
        .order_by(Source.position, Source.source_id)
    ).all()
    catalog = []
    for source, version in rows:
        if source.included and version is None:
            raise ApplicationError(
                "SOURCE_TEXT_REQUIRED", "An included source has no selected text version."
            )
        catalog.append(
            {
                "case_id": case.id,
                "source_id": source.source_id,
                "kind": source.kind,
                "locator": source.locator,
                "title": source.title,
                "trust": source.trust,
                "position": source.position,
                "included": source.included,
                "version_id": version.id if version is not None and source.included else None,
                "text": version.text if version is not None and source.included else None,
                "text_sha256": version.text_sha256
                if version is not None and source.included
                else None,
            }
        )
    return AnalysisInput.model_validate_json(
        canonical_json(
            {
                "case_id": case.id,
                "user_text": case.user_text,
                "requested_fact_keys": case.requested_fact_keys,
                "money": case.money,
                "context": case.context,
                "catalog": catalog,
            }
        )
    )


def check_limits(value: AnalysisInput, settings: Settings) -> None:
    limits = [
        (len(value.user_text), settings.max_user_characters, "user_text"),
        (len(value.catalog), settings.max_sources, "sources"),
        (
            sum(len(s.text or "") for s in value.catalog),
            settings.max_selected_characters,
            "selected_sources",
        ),
    ]
    limits += [
        (len(source.text or ""), settings.max_source_characters, "source_text")
        for source in value.catalog
    ]
    for actual, maximum, field in limits:
        if actual > maximum:
            raise ApplicationError(
                "INPUT_LIMIT_EXCEEDED",
                "Input exceeds the configured limit.",
                status_code=413,
                details={"field": field, "limit": maximum},
            )


def require_current(session: Session, case: Case, proposal: ProposalRevision) -> None:
    snapshot = current_input(session, case)
    if (
        proposal.content_revision != case.content_revision
        or proposal.input_digest != compatibility_digest(snapshot)
        or proposal.source_bindings != source_bindings(snapshot)
    ):
        raise ApplicationError("PROPOSAL_STALE", "The case input changed; create a new analysis.")


def latest_check(session: Session, proposal_id: str) -> ValidationCheck | None:
    return session.scalar(
        select(ValidationCheck)
        .where(ValidationCheck.proposal_revision_id == proposal_id)
        .order_by(ValidationCheck.created_at.desc(), ValidationCheck.id.desc())
        .limit(1)
    )


def record_validation(
    session: Session, proposal: ProposalRevision, profile: str
) -> ValidationCheck:
    snapshot = AnalysisInput.model_validate_json(canonical_json(proposal.snapshot))
    result = validate_assembly(
        AssemblyOutput.model_validate(proposal.body), snapshot.to_validation_context()
    )
    issues: list[dict[str, JsonValue]] = [
        {"code": issue.code, "path": list(issue.path), "message": issue.message}
        for issue in result.issues
    ]
    spans: list[dict[str, JsonValue]] = [asdict(span) for span in result.fragments]
    check = ValidationCheck(
        proposal_revision_id=proposal.id,
        profile_version=profile,
        result=result.state,
        issues=issues,
        resolved_spans=spans,
    )
    session.add(check)
    session.flush()
    return check
