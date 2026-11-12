"""Operator edits and decisions bind to immutable bodies, inputs and checks."""

from sqlalchemy import select

from proofline.application.commands import (
    ActivateProposal,
    DecisionCreate,
    RevisionCreate,
    VersionCommand,
)
from proofline.application.errors import ApplicationError
from proofline.application.read_models import proposal_view
from proofline.application.state import (
    case_or_404,
    latest_check,
    proposal_or_404,
    read_session,
    record_validation,
    require_current,
    require_key,
    require_version,
    touch,
)
from proofline.application.views import DecisionView, ProposalView
from proofline.config import Settings
from proofline.domain.inputs import json_digest
from proofline.persistence.database import Database
from proofline.persistence.models import ProposalRevision, ReviewDecision


class ProposalService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def get(self, revision_id: str) -> ProposalView:
        with read_session(self.database) as session:
            proposal = proposal_or_404(session, revision_id)
            return proposal_view(
                session,
                proposal,
                case_or_404(session, proposal.case_id),
                self.settings.validation_profile,
            )

    def list(self, case_id: str) -> list[ProposalView]:
        with read_session(self.database) as session:
            case = case_or_404(session, case_id)
            proposals = session.scalars(
                select(ProposalRevision)
                .where(ProposalRevision.case_id == case_id)
                .order_by(ProposalRevision.created_at, ProposalRevision.id)
            ).all()
            return [
                proposal_view(session, proposal, case, self.settings.validation_profile)
                for proposal in proposals
            ]

    def revise(self, revision_id: str, command: RevisionCreate) -> ProposalView:
        with self.database.sessions.begin() as session:
            parent = proposal_or_404(session, revision_id)
            case = case_or_404(session, parent.case_id, lock=True)
            require_version(case, command.expected_case_version)
            require_current(session, case, parent)
            if command.body.case_id != case.id:
                raise ApplicationError(
                    "INVALID_REQUEST",
                    "An operator revision cannot change the case ID.",
                    status_code=422,
                )
            body = command.body.model_dump(mode="json")
            child = ProposalRevision(
                case_id=case.id,
                parent_revision_id=parent.id,
                origin="operator",
                content_revision=parent.content_revision,
                input_digest=parent.input_digest,
                snapshot=parent.snapshot,
                source_bindings=parent.source_bindings,
                body=body,
                body_sha256=json_digest(body),
                reason=command.reason,
            )
            session.add(child)
            session.flush()
            record_validation(session, child, self.settings.validation_profile)
            case.current_proposal_revision_id = child.id
            touch(case)
            session.flush()
            return proposal_view(session, child, case, self.settings.validation_profile)

    def activate(self, case_id: str, command: ActivateProposal) -> ProposalView:
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            require_version(case, command.expected_case_version)
            proposal = proposal_or_404(session, command.proposal_revision_id)
            if proposal.case_id != case_id:
                raise ApplicationError(
                    "PROPOSAL_NOT_FOUND", "Proposal not found in this case.", status_code=404
                )
            require_current(session, case, proposal)
            case.current_proposal_revision_id = proposal.id
            touch(case)
            session.flush()
            return proposal_view(session, proposal, case, self.settings.validation_profile)

    def validate(self, revision_id: str, command: VersionCommand) -> ProposalView:
        with self.database.sessions.begin() as session:
            proposal = proposal_or_404(session, revision_id)
            case = case_or_404(session, proposal.case_id, lock=True)
            require_version(case, command.expected_case_version)
            require_current(session, case, proposal)
            record_validation(session, proposal, self.settings.validation_profile)
            touch(case)
            session.flush()
            return proposal_view(session, proposal, case, self.settings.validation_profile)

    def decide(
        self, revision_id: str, command: DecisionCreate, idempotency_key: str
    ) -> tuple[DecisionView, int]:
        require_key(idempotency_key)
        request_hash = json_digest({"revision_id": revision_id, **command.model_dump(mode="json")})
        with self.database.sessions.begin() as session:
            proposal = proposal_or_404(session, revision_id)
            case = case_or_404(session, proposal.case_id, lock=True)
            previous = session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.case_id == case.id,
                    ReviewDecision.idempotency_key == idempotency_key,
                )
            )
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise ApplicationError(
                        "IDEMPOTENCY_CONFLICT", "This key belongs to a different command."
                    )
                return DecisionView.model_validate(previous), 200
            require_version(case, command.expected_case_version)
            require_current(session, case, proposal)
            if session.scalar(
                select(ReviewDecision.id).where(ReviewDecision.proposal_revision_id == revision_id)
            ):
                raise ApplicationError(
                    "ALREADY_REVIEWED",
                    "This revision already has a final decision; create a child revision.",
                )
            if command.kind == "approve" and case.current_proposal_revision_id != revision_id:
                raise ApplicationError(
                    "PROPOSAL_NOT_ACTIVE", "Activate this proposal before approving it."
                )
            previous_check = latest_check(session, revision_id)
            if command.kind == "approve" and (
                previous_check is None
                or previous_check.profile_version != self.settings.validation_profile
            ):
                raise ApplicationError(
                    "VALIDATION_PROFILE_CHANGED",
                    "Revalidate this revision with the current profile.",
                )
            check = record_validation(session, proposal, self.settings.validation_profile)
            if command.kind == "approve" and check.result == "blocked_for_approval":
                raise ApplicationError(
                    "VALIDATION_BLOCKED", "Resolve the blocking issues before approval."
                )
            touch(case)
            decision = ReviewDecision(
                case_id=case.id,
                proposal_revision_id=proposal.id,
                validation_check_id=check.id,
                kind=command.kind,
                actor="demo_operator",
                reason=command.reason,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                body_sha256=proposal.body_sha256,
                source_bindings_digest=json_digest(proposal.source_bindings),
                result_case_version=case.version,
            )
            session.add(decision)
            session.flush()
            return DecisionView.model_validate(decision), 201
