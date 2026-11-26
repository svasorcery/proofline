"""Commit an input snapshot, generate outside the transaction, then finish with CAS."""

import json
import logging
from dataclasses import replace
from threading import Lock

from pydantic import JsonValue, ValidationError
from sqlalchemy import func, select, text, update

from proofline.application.commands import RunCreate
from proofline.application.errors import ApplicationError
from proofline.application.guest_live import admit_guest_live
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
    validate_storage_json,
)
from proofline.persistence.database import Database
from proofline.persistence.models import AnalysisRun, Case, ProposalRevision
from proofline.providers.live import LiveProvider, PreparedLive
from proofline.providers.replay import (
    GenerationResult,
    PreparedReplay,
    ReplayOption,
    ReplayProvider,
    recorded_input,
)

logger = logging.getLogger(__name__)


class RunService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider: ReplayProvider,
        owner_instance_id: str,
        live_provider: LiveProvider | None = None,
    ) -> None:
        self.database, self.settings, self.provider = database, settings, provider
        self.owner_instance_id = owner_instance_id
        self.live_provider = live_provider
        self._completion_lock = Lock()
        self._uncertain_completions: set[str] = set()

    def options(self, case_id: str, *, guest_id: str | None = None) -> list[ReplayOption]:
        with read_session(self.database) as session:
            case = case_or_404(session, case_id, guest_id=guest_id)
            snapshot = current_input(session, case)
            check_limits(snapshot, self.settings)
            options = self.provider.options(recorded_input(snapshot, case.fixture_case_id))
            if case.fixture_case_id is None:
                return options
            digest = compatibility_digest(snapshot)
            return [
                option.model_copy(update={"case_id": case_id, "input_digest": digest})
                for option in options
            ]

    def get(self, run_id: str, *, guest_id: str | None = None) -> RunView:
        with read_session(self.database) as session:
            run = session.scalar(
                select(AnalysisRun)
                .join(Case, Case.id == AnalysisRun.case_id)
                .where(AnalysisRun.id == run_id, Case.guest_id == guest_id)
            )
            if run is None:
                raise ApplicationError("RUN_NOT_FOUND", "Analysis run not found.", status_code=404)
            return run_view(session, run)

    def list(self, case_id: str, *, guest_id: str | None = None) -> list[RunView]:
        with read_session(self.database) as session:
            case_or_404(session, case_id, guest_id=guest_id)
            runs = session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.case_id == case_id)
                .order_by(AnalysisRun.started_at, AnalysisRun.id)
            ).all()
            return [run_view(session, run) for run in runs]

    def create(
        self, case_id: str, command: RunCreate, idempotency_key: str, *, guest_id: str | None = None
    ) -> tuple[RunView, int]:
        require_key(idempotency_key)
        request_hash = json_digest(command.model_dump(mode="json"))
        run_id: str | None = None
        try:
            with self.database.sessions.begin() as session:
                if guest_id is not None and command.mode == "live":
                    # Admission must see the preceding transaction after waiting on its lock.
                    session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                case = case_or_404(session, case_id, lock=True, guest_id=guest_id)
                previous = session.scalar(
                    select(AnalysisRun).where(
                        AnalysisRun.case_id == case_id,
                        AnalysisRun.idempotency_key == idempotency_key,
                    )
                )
                if previous is not None:
                    if previous.request_hash != request_hash:
                        raise ApplicationError(
                            "IDEMPOTENCY_CONFLICT", "This key belongs to a different command."
                        )
                    return run_view(session, previous), 202 if previous.status == "running" else 200
                if command.mode == "live" and (
                    self.settings.live_unavailable_reason is not None or self.live_provider is None
                ):
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
                        "ANALYSIS_ALREADY_RUNNING",
                        "Another analysis is already running for this case.",
                    )
                require_version(case, command.expected_case_version)
                snapshot = current_input(session, case)
                check_limits(snapshot, self.settings)
                prepared: PreparedReplay | PreparedLive
                guest_admitted_at = None
                if command.mode == "live":
                    assert self.live_provider is not None
                    if self.settings.guest_mode:
                        if guest_id is None:
                            raise ApplicationError(
                                "GUEST_SESSION_REQUIRED",
                                "Open a guest workspace first.",
                                status_code=401,
                            )
                        if case.fixture_case_id is None or not self.provider.options(
                            recorded_input(snapshot, case.fixture_case_id)
                        ):
                            raise ApplicationError(
                                "GUEST_LIVE_INPUT_NOT_SUPPORTED",
                                "Guest Live accepts inputs from the demo recordings.",
                                status_code=422,
                            )
                    prepared = self.live_provider.prepare(snapshot)
                    if guest_id is not None:
                        guest_admitted_at = admit_guest_live(session, guest_id)
                        prepared = replace(
                            prepared,
                            generation_profile={**prepared.generation_profile, "guest_live": True},
                        )
                else:
                    assert command.replay_id is not None
                    replay = self.provider.prepare(
                        command.replay_id, recorded_input(snapshot, case.fixture_case_id)
                    )
                    if case.fixture_case_id is not None:
                        replay = replace(
                            replay,
                            target_case_id=case_id,
                            generation_profile={
                                **replay.generation_profile,
                                "recorded_case_id": replay.option.case_id,
                                "recorded_input_digest": replay.option.input_digest,
                            },
                        )
                    prepared = replay
                run = AnalysisRun(
                    case_id=case_id,
                    mode=command.mode,
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
                if guest_admitted_at is not None:
                    run.started_at = guest_admitted_at
                session.add(run)
                session.flush()
                run_id = run.id
            assert run_id is not None
            result, body, error = self._generate(prepared)
            return self._finish(case_id, run_id, result, body, error, guest_id=guest_id), 201
        except Exception:
            # Admission or completion may have committed before its acknowledgement
            # was lost. Repair an owned running row without another provider call.
            if run_id is not None:
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
                        finished_at=func.clock_timestamp(),
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
        self, prepared: PreparedReplay | PreparedLive
    ) -> tuple[GenerationResult | None, AssemblyOutput | None, dict[str, JsonValue] | None]:
        result = None
        try:
            if isinstance(prepared, PreparedLive):
                assert self.live_provider is not None
                result = self.live_provider.generate(prepared)
            else:
                result = self.provider.generate(prepared)
            if result.error is not None:
                return result, None, result.error
            if result.raw_output is None:
                return (
                    result,
                    None,
                    {"code": "INVALID_MODEL_OUTPUT", "message": "No output was returned."},
                )
            decode = prepared.decode_output if isinstance(prepared, PreparedLive) else None
            body = (
                decode(result.raw_output)
                if decode
                else AssemblyOutput.model_validate_json(result.raw_output)
            )
            if isinstance(prepared, PreparedReplay) and prepared.target_case_id is not None:
                if body.case_id != prepared.option.case_id:
                    raise ValueError("The recording belongs to a different fixture case")
                body = body.model_copy(update={"case_id": prepared.target_case_id})
            validate_storage_json(body.model_dump(mode="json"))
            canonical_json(result.generation_profile).encode("utf-8")
            canonical_json(result.usage).encode("utf-8")
            return result, body, None
        except (ValidationError, ValueError):
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
            logger.error("Analysis provider failed; exception details omitted")
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
        *,
        guest_id: str | None = None,
    ) -> RunView:
        if result is not None and result.raw_output is not None:
            try:
                validate_storage_json(result.raw_output)
            except ValueError:
                # Preserve otherwise unstorable raw text reversibly as a JSON
                # string. This is diagnostic data, never a repaired model body.
                result = replace(
                    result,
                    raw_output=json.dumps(result.raw_output, ensure_ascii=True),
                    generation_profile={
                        **result.generation_profile,
                        "raw_output_encoding": "json-string",
                    },
                )
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True, guest_id=guest_id)
            values: dict[str, object] = {
                "status": "succeeded" if body is not None else "failed",
                "finished_at": func.clock_timestamp(),
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
                record_validation(session, proposal, self.settings)
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
                    finished_at=func.clock_timestamp(),
                    error={
                        "code": "PROCESS_INTERRUPTED",
                        "message": "The owning process stopped before completion.",
                    },
                )
                .returning(AnalysisRun.id)
            ).all()
            return len(recovered)
