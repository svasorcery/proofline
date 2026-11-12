"""Public read models; internal request hashes and idempotency keys stay private."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from proofline.domain.assembly import AssemblyOutput, FactKey
from proofline.domain.inputs import AnalysisInput, Money
from proofline.domain.sources import SourceKind

type CaseStatus = Literal[
    "unanalysed",
    "stale",
    "needs_revalidation",
    "approved",
    "rejected",
    "blocked_for_approval",
    "needs_operator_review",
]


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceVersionSummary(_View):
    id: str
    sequence: int
    text_sha256: str
    created_at: datetime


class SourceView(_View):
    case_id: str
    source_id: str
    kind: SourceKind
    locator: str
    title: str
    trust: str
    position: int
    included: bool
    current_version_id: str | None
    versions: list[SourceVersionSummary]


class SourceVersionView(SourceVersionSummary):
    case_id: str
    source_id: str
    text: str


class CaseSummary(_View):
    id: str
    title: str
    version: int
    content_revision: int
    current_proposal_revision_id: str | None
    status: CaseStatus
    analysis_running: bool
    created_at: datetime
    updated_at: datetime


class CaseDetail(CaseSummary):
    user_text: str
    requested_fact_keys: list[FactKey]
    money: Money | None
    context: dict[str, JsonValue]
    sources: list[SourceView]


class CasePage(_View):
    items: list[CaseSummary]
    total: int
    limit: int
    offset: int


class RunView(_View):
    id: str
    case_id: str
    mode: Literal["replay", "live"]
    status: Literal["running", "succeeded", "failed"]
    content_revision: int
    base_case_version: int
    input_digest: str
    snapshot: AnalysisInput
    started_at: datetime
    finished_at: datetime | None
    generation_profile: dict[str, JsonValue]
    raw_output: str | None
    usage: dict[str, JsonValue] | None
    error: dict[str, JsonValue] | None
    proposal_revision_id: str | None


class IssueView(_View):
    code: str
    path: list[str | int]
    message: str


class SpanView(_View):
    source_id: str
    source_version_id: str
    source_sha256: str
    start_cp: int
    end_cp: int
    fragment: str
    path: list[str | int]


class ValidationView(_View):
    id: str
    proposal_revision_id: str
    profile_version: str
    result: Literal["blocked_for_approval", "needs_operator_review"]
    issues: list[IssueView]
    resolved_spans: list[SpanView]
    created_at: datetime


class DecisionView(_View):
    id: str
    case_id: str
    proposal_revision_id: str
    validation_check_id: str
    kind: Literal["approve", "reject"]
    actor: str
    reason: str
    body_sha256: str
    source_bindings_digest: str
    result_case_version: int
    created_at: datetime


class ProposalView(_View):
    id: str
    case_id: str
    analysis_run_id: str | None
    parent_revision_id: str | None
    origin: Literal["model", "operator"]
    content_revision: int
    input_digest: str
    snapshot: AnalysisInput
    source_bindings: dict[str, JsonValue]
    body: AssemblyOutput
    body_sha256: str
    reason: str | None
    created_at: datetime
    active: bool
    stale: bool
    validation_current: bool
    status: CaseStatus
    latest_validation: ValidationView | None
    validations: list[ValidationView]
    blocking_issues: list[IssueView]
    decision: DecisionView | None
    decision_current: bool
    case_version: int
