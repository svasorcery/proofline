"""Commit an input snapshot, generate outside the transaction, then finish with CAS."""

import logging
from datetime import UTC, datetime
from threading import Lock

from pydantic import JsonValue, ValidationError
from sqlalchemy import select, update

from proofline.application.commands import RunCreate
from proofline.application.errors import ApplicationError
from proofline.application.read_models import run_view
from proofline.application.state import (
    case_or_404,
    check_limits,
    current_input,
    read_session,
    record_validation,
    require_key,
    require_version,
    touch,
)
from proofline.application.views import RunView
from proofline.config import Settings
from proofline.domain.assembly import AssemblyOutput
from proofline.domain.inputs import (
    AnalysisInput,
    canonical_json,
    compatibility_digest,
    json_digest,
    source_bindings,
)
from proofline.persistence.database import Database
from proofline.persistence.models import AnalysisRun, ProposalRevision
from proofline.providers.replay import (
    GenerationResult,
    PreparedReplay,
    ReplayOption,
    ReplayProvider,
)

logger = logging.getLogger(__name__)


class RunService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider: ReplayProvider,
        owner_instance_id: str,
    ) -> None:
        self.database, self.settings, self.provider = database, settings, provider
        self.owner_instance_id = owner_instance_id
        self._completion_lock = Lock()
        self._uncertain_completions: set[str] = set()

    def options(self, case_id: str) -> list[ReplayOption]:
        with read_session(self.database) as session:
            snapshot = current_input(session, case_or_404(session, case_id))
            check_limits(snapshot, self.settings)
            return self.provider.options(snapshot)

    def get(self, run_id: str) -> RunView:
        with read_session(self.database) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None:
                raise ApplicationError("RUN_NOT_FOUND", "Analysis run not found.", status_code=404)
            return run_view(session, run)

    def list(self, case_id: str) -> list[RunView]:
        with read_session(self.database) as session:
            case_or_404(session, case_id)
            runs = session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.case_id == case_id)
                .order_by(AnalysisRun.started_at, AnalysisRun.id)
            ).all()
            return [run_view(session, run) for run in runs]

    def create(self, case_id: str, command: RunCreate, idempotency_key: str) -> tuple[RunView, int]:
        require_key(idempotency_key)
        request_hash = json_digest(command.model_dump(mode="json"))
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            previous = session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.case_id == case_id, AnalysisRun.idempotency_key == idempotency_key
                )
            )
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise ApplicationError(
                        "IDEMPOTENCY_CONFLICT", "This key belongs to a different command."
                    )
                return run_view(session, previous), 202 if previous.status == "running" else 200
            if command.mode == "live":
                raise ApplicationError(
                    "LIVE_NOT_CONFIGURED",
                    "Live analysis is not configured; select replay explicitly.",
                    status_code=503,
                )
            if session.scalar(
                select(AnalysisRun.id).where(
                    AnalysisRun.case_id == case_id, AnalysisRun.status == "running"
                )
            ):
                raise ApplicationError(
                    "ANALYSIS_ALREADY_RUNNING", "Another analysis is already running for this case."
                )
            require_version(case, command.expected_case_version)
            snapshot = current_input(session, case)
            check_limits(snapshot, self.settings)
            if command.replay_id is None:
                raise ApplicationError(
                    "INVALID_REQUEST", "A replay ID is required.", status_code=422
                )
            prepared = self.provider.prepare(command.replay_id, snapshot)
            run = AnalysisRun(
                case_id=case_id,
                mode="replay",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                snapshot=snapshot.model_dump(mode="json"),
                input_digest=compatibility_digest(snapshot),
                content_revision=case.content_revision,
                base_case_version=case.version,
                owner_instance_id=self.owner_instance_id,
                status="running",
                generation_profile=prepared.generation_profile,
            )
            session.add(run)
            session.flush()
            run_id = run.id
        result, body, error = self._generate(prepared)
        try:
            return self._finish(case_id, run_id, result, body, error), 201
        except Exception:
            # The commit may have reached PostgreSQL even when its acknowledgement was lost.
            # Reconcile the stored outcome later; never generate the response again.
            with self._completion_lock:
                self._uncertain_completions.add(run_id)
            raise

    def reconcile_completions(self) -> int:
        with self._completion_lock:
            if not self._uncertain_completions:
                return 0
            pending = tuple(self._uncertain_completions)
            with self.database.sessions.begin() as session:
                interrupted = session.scalars(
                    update(AnalysisRun)
                    .where(
                        AnalysisRun.id.in_(pending),
                        AnalysisRun.owner_instance_id == self.owner_instance_id,
                        AnalysisRun.status == "running",
                    )
                    .values(
                        status="failed",
                        finished_at=datetime.now(UTC),
                        error={
                            "code": "RUN_INTERRUPTED",
                            "message": (
                                "Analysis completion was interrupted; start a new run explicitly."
                            ),
                        },
                    )
                    .returning(AnalysisRun.id)
                ).all()
            self._uncertain_completions.difference_update(pending)
            return len(interrupted)

    def _generate(
        self, prepared: PreparedReplay
    ) -> tuple[GenerationResult | None, AssemblyOutput | None, dict[str, JsonValue] | None]:
        result = None
        try:
            result = self.provider.generate(prepared)
            body = AssemblyOutput.model_validate_json(result.raw_output)
            canonical_json(result.generation_profile).encode("utf-8")
            canonical_json(result.usage).encode("utf-8")
            return result, body, None
        except ValidationError:
            return (
                result,
                None,
                {
                    "code": "INVALID_MODEL_OUTPUT",
                    "message": "The response does not match the output contract.",
                },
            )
        except TimeoutError:
            return (
                None,
                None,
                {
                    "code": "MODEL_TIMEOUT",
                    "message": "Analysis timed out; no automatic retry was made.",
                },
            )
        except Exception:
            logger.exception("Analysis provider failed")
            return (
                None,
                None,
                {
                    "code": "PROVIDER_ERROR",
                    "message": "Analysis failed; no automatic retry was made.",
                },
            )

    def _finish(
        self,
        case_id: str,
        run_id: str,
        result: GenerationResult | None,
        body: AssemblyOutput | None,
        error: dict[str, JsonValue] | None,
    ) -> RunView:
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            values: dict[str, object] = {
                "status": "succeeded" if body is not None else "failed",
                "finished_at": datetime.now(UTC),
                "error": error,
                "raw_output": result.raw_output if result else None,
                "usage": result.usage if result else None,
            }
            if result is not None:
                values["generation_profile"] = result.generation_profile
            updated = session.scalar(
                update(AnalysisRun)
                .where(
                    AnalysisRun.id == run_id,
                    AnalysisRun.status == "running",
                    AnalysisRun.owner_instance_id == self.owner_instance_id,
                )
                .values(**values)
                .returning(AnalysisRun.id)
            )
            run = session.get(AnalysisRun, run_id)
            if run is None:
                raise ApplicationError("RUN_NOT_FOUND", "Analysis run not found.", status_code=404)
            if updated is None:
                return run_view(session, run)
            if body is not None:
                snapshot = AnalysisInput.model_validate_json(canonical_json(run.snapshot))
                canonical_body = body.model_dump(mode="json")
                proposal = ProposalRevision(
                    case_id=case_id,
                    analysis_run_id=run.id,
                    origin="model",
                    content_revision=run.content_revision,
                    input_digest=run.input_digest,
                    snapshot=run.snapshot,
                    source_bindings=source_bindings(snapshot),
                    body=canonical_body,
                    body_sha256=json_digest(canonical_body),
                )
                session.add(proposal)
                session.flush()
                record_validation(session, proposal, self.settings.validation_profile)
                if case.version == run.base_case_version:
                    case.current_proposal_revision_id = proposal.id
                    touch(case)
                session.flush()
            return run_view(session, run)

    def recover_interrupted(self) -> int:
        with self.database.sessions.begin() as session:
            recovered = session.scalars(
                update(AnalysisRun)
                .where(
                    AnalysisRun.status == "running",
                    AnalysisRun.owner_instance_id != self.owner_instance_id,
                )
                .values(
                    status="failed",
                    finished_at=datetime.now(UTC),
                    error={
                        "code": "PROCESS_INTERRUPTED",
                        "message": "The owning process stopped before completion.",
                    },
                )
                .returning(AnalysisRun.id)
            ).all()
            return len(recovered)
