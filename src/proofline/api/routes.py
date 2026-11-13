"""Synchronous endpoints keep PostgreSQL work in FastAPI's worker threads."""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Response
from pydantic import BaseModel

from proofline.application.commands import (
    ActivateProposal,
    CaseCreate,
    CasePatch,
    DecisionCreate,
    RevisionCreate,
    RunCreate,
    SourceCreate,
    SourcePatch,
    SourceVersionCreate,
    VersionCommand,
)
from proofline.application.errors import ApplicationError
from proofline.application.service import WorkflowService
from proofline.application.views import (
    Capabilities,
    CaseDetail,
    CasePage,
    CaseStatus,
    DecisionView,
    ProposalView,
    RunView,
    SourceVersionView,
)
from proofline.providers.replay import ReplayOption


class HealthView(BaseModel):
    status: str


def idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> str:
    if not value.strip() or value != value.strip():
        raise ApplicationError(
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must contain 1 to 128 characters.",
            status_code=422,
        )
    return value


type IdempotencyKey = Annotated[str, Depends(idempotency_key)]


def register_routes(app: FastAPI, service: WorkflowService) -> None:
    def require_ready() -> None:
        if not service.is_ready():
            raise ApplicationError(
                "DATABASE_UNAVAILABLE",
                "The database is unavailable or its schema is not ready.",
                status_code=503,
            )

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_ready)])
    health_router = APIRouter(prefix="/api/v1")

    @app.get("/health", response_model=HealthView, include_in_schema=False)
    @health_router.get("/health", response_model=HealthView)
    def health() -> HealthView:
        return HealthView(status="ok")

    @app.get("/ready", response_model=HealthView, include_in_schema=False)
    @health_router.get("/ready", response_model=HealthView)
    def ready() -> HealthView:
        require_ready()
        return HealthView(status="ready")

    @health_router.get("/capabilities", response_model=Capabilities)
    def capabilities() -> Capabilities:
        return service.capabilities()

    @router.get("/cases", response_model=CasePage)
    def list_cases(
        status: CaseStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> CasePage:
        return service.cases.list(status=status, limit=limit, offset=offset)

    @router.post("/cases", response_model=CaseDetail, status_code=201)
    def create_case(command: CaseCreate) -> CaseDetail:
        return service.cases.create(command)

    @router.get("/cases/{case_id}", response_model=CaseDetail)
    def get_case(case_id: str) -> CaseDetail:
        return service.cases.get(case_id)

    @router.patch("/cases/{case_id}", response_model=CaseDetail)
    def update_case(case_id: str, command: CasePatch) -> CaseDetail:
        return service.cases.update(case_id, command)

    @router.post("/cases/{case_id}/sources", response_model=CaseDetail, status_code=201)
    def add_source(case_id: str, command: SourceCreate) -> CaseDetail:
        return service.cases.add_source(case_id, command)

    @router.post(
        "/cases/{case_id}/sources/{source_id}/versions", response_model=CaseDetail, status_code=201
    )
    def add_version(case_id: str, source_id: str, command: SourceVersionCreate) -> CaseDetail:
        return service.cases.add_version(case_id, source_id, command)

    @router.patch("/cases/{case_id}/sources/{source_id}", response_model=CaseDetail)
    def update_source(case_id: str, source_id: str, command: SourcePatch) -> CaseDetail:
        return service.cases.update_source(case_id, source_id, command)

    @router.get(
        "/cases/{case_id}/sources/{source_id}/versions/{version_id}",
        response_model=SourceVersionView,
    )
    def get_version(case_id: str, source_id: str, version_id: str) -> SourceVersionView:
        return service.cases.get_version(case_id, source_id, version_id)

    @router.get("/cases/{case_id}/replay-options", response_model=list[ReplayOption])
    def replay_options(case_id: str) -> list[ReplayOption]:
        return service.runs.options(case_id)

    @router.post("/cases/{case_id}/runs", response_model=RunView, status_code=201)
    def create_run(
        case_id: str, command: RunCreate, key: IdempotencyKey, response: Response
    ) -> RunView:
        run, status_code = service.runs.create(case_id, command, key)
        response.status_code = status_code
        return run

    @router.get("/cases/{case_id}/runs", response_model=list[RunView])
    def list_runs(case_id: str) -> list[RunView]:
        return service.runs.list(case_id)

    @router.get("/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        return service.runs.get(run_id)

    @router.get("/cases/{case_id}/proposals", response_model=list[ProposalView])
    def list_proposals(case_id: str) -> list[ProposalView]:
        return service.proposals.list(case_id)

    @router.get("/proposals/{revision_id}", response_model=ProposalView)
    def get_proposal(revision_id: str) -> ProposalView:
        return service.proposals.get(revision_id)

    @router.post("/proposals/{revision_id}/revisions", response_model=ProposalView, status_code=201)
    def revise_proposal(revision_id: str, command: RevisionCreate) -> ProposalView:
        return service.proposals.revise(revision_id, command)

    @router.post("/cases/{case_id}/active-proposal", response_model=ProposalView)
    def activate_proposal(case_id: str, command: ActivateProposal) -> ProposalView:
        return service.proposals.activate(case_id, command)

    @router.post("/proposals/{revision_id}/validate", response_model=ProposalView)
    def validate_proposal(revision_id: str, command: VersionCommand) -> ProposalView:
        return service.proposals.validate(revision_id, command)

    @router.post("/proposals/{revision_id}/decision", response_model=DecisionView, status_code=201)
    def decide_proposal(
        revision_id: str, command: DecisionCreate, key: IdempotencyKey, response: Response
    ) -> DecisionView:
        decision, status_code = service.proposals.decide(revision_id, command, key)
        response.status_code = status_code
        return decision

    app.include_router(router)
    app.include_router(health_router)
