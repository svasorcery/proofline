"""Persisted workflow records; ownership constraints also hold outside the API."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("content_revision > 0", name="ck_cases_content_revision"),
        CheckConstraint("version > 0", name="ck_cases_version"),
        # The active proposal belongs to this case, even under direct SQL writes.
        ForeignKeyConstraint(
            ["current_proposal_revision_id", "id"],
            ["proposal_revisions.id", "proposal_revisions.case_id"],
            name="fk_cases_current_proposal",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index("ix_cases_created_at", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text)
    user_text: Mapped[str] = mapped_column(Text)
    requested_fact_keys: Mapped[list[str]] = mapped_column(JSONB)
    money: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB(none_as_null=True))
    context: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    content_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_proposal_revision_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utc_now
    )


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("case_id", "locator", name="uq_sources_case_locator"),
        CheckConstraint("position >= 0", name="ck_sources_position"),
        ForeignKeyConstraint(
            ["current_version_id", "case_id", "source_id"],
            ["source_versions.id", "source_versions.case_id", "source_versions.source_id"],
            name="fk_sources_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index("ix_sources_case_position", "case_id", "position"),
    )

    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cases.id", name="fk_sources_case"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    trust: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    included: Mapped[bool] = mapped_column(Boolean)
    current_version_id: Mapped[str | None] = mapped_column(Text)


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "source_id"],
            ["sources.case_id", "sources.source_id"],
            name="fk_source_versions_source",
        ),
        UniqueConstraint("case_id", "source_id", "sequence", name="uq_source_versions_sequence"),
        UniqueConstraint("id", "case_id", "source_id", name="uq_source_versions_owner"),
        CheckConstraint("sequence > 0", name="ck_source_versions_sequence"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("case_id", "idempotency_key", name="uq_analysis_runs_idempotency"),
        UniqueConstraint("id", "case_id", name="uq_analysis_runs_owner"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_analysis_runs_status"
        ),
        CheckConstraint("mode IN ('replay', 'live')", name="ck_analysis_runs_mode"),
        CheckConstraint("content_revision > 0", name="ck_analysis_runs_content_revision"),
        CheckConstraint("base_case_version > 0", name="ck_analysis_runs_base_case_version"),
        Index(
            "uq_analysis_runs_running_case",
            "case_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_analysis_runs_case_started", "case_id", "started_at", "id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(Text, ForeignKey("cases.id", name="fk_analysis_runs_case"))
    mode: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    input_digest: Mapped[str] = mapped_column(Text)
    content_revision: Mapped[int] = mapped_column(Integer)
    base_case_version: Mapped[int] = mapped_column(Integer)
    owner_instance_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    generation_profile: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    raw_output: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB(none_as_null=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProposalRevision(Base):
    __tablename__ = "proposal_revisions"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_proposal_revisions_owner"),
        ForeignKeyConstraint(
            ["analysis_run_id", "case_id"],
            ["analysis_runs.id", "analysis_runs.case_id"],
            name="fk_proposal_revisions_run",
        ),
        ForeignKeyConstraint(
            ["parent_revision_id", "case_id"],
            ["proposal_revisions.id", "proposal_revisions.case_id"],
            name="fk_proposal_revisions_parent",
        ),
        CheckConstraint("origin IN ('model', 'operator')", name="ck_proposal_revisions_origin"),
        CheckConstraint("content_revision > 0", name="ck_proposal_revisions_content_revision"),
        Index("ix_proposal_revisions_case_created", "case_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cases.id", name="fk_proposal_revisions_case")
    )
    analysis_run_id: Mapped[str | None] = mapped_column(Text)
    parent_revision_id: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    content_revision: Mapped[int] = mapped_column(Integer)
    input_digest: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    source_bindings: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    body: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    body_sha256: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationCheck(Base):
    __tablename__ = "validation_checks"
    __table_args__ = (
        UniqueConstraint("id", "proposal_revision_id", name="uq_validation_checks_proposal"),
        CheckConstraint(
            "result IN ('blocked_for_approval', 'needs_operator_review')",
            name="ck_validation_checks_result",
        ),
        Index("ix_validation_checks_proposal_created", "proposal_revision_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    proposal_revision_id: Mapped[str] = mapped_column(
        Text, ForeignKey("proposal_revisions.id", name="fk_validation_checks_proposal")
    )
    profile_version: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    issues: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSONB)
    resolved_spans: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("proposal_revision_id", name="uq_review_decisions_proposal"),
        UniqueConstraint("case_id", "idempotency_key", name="uq_review_decisions_idempotency"),
        ForeignKeyConstraint(
            ["proposal_revision_id", "case_id"],
            ["proposal_revisions.id", "proposal_revisions.case_id"],
            name="fk_review_decisions_proposal",
        ),
        ForeignKeyConstraint(
            ["validation_check_id", "proposal_revision_id"],
            ["validation_checks.id", "validation_checks.proposal_revision_id"],
            name="fk_review_decisions_validation",
        ),
        CheckConstraint("kind IN ('approve', 'reject')", name="ck_review_decisions_kind"),
        CheckConstraint("result_case_version > 0", name="ck_review_decisions_case_version"),
        Index("ix_review_decisions_case_created", "case_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cases.id", name="fk_review_decisions_case")
    )
    proposal_revision_id: Mapped[str] = mapped_column(Text)
    validation_check_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(Text)
    source_bindings_digest: Mapped[str] = mapped_column(Text)
    result_case_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
