"""Create versioned cases, analysis runs, proposals, checks and decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_case_workflow"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("requested_fact_keys", postgresql.JSONB(), nullable=False),
        sa.Column("money", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("content_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_proposal_revision_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("content_revision > 0", name="ck_cases_content_revision"),
        sa.CheckConstraint("version > 0", name="ck_cases_version"),
        sa.PrimaryKeyConstraint("id", name="pk_cases"),
    )
    op.create_index("ix_cases_created_at", "cases", ["created_at", "id"])
    op.create_table(
        "sources",
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("trust", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.CheckConstraint("position >= 0", name="ck_sources_position"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_sources_case"),
        sa.PrimaryKeyConstraint("case_id", "source_id", name="pk_sources"),
        sa.UniqueConstraint("case_id", "locator", name="uq_sources_case_locator"),
    )
    op.create_index("ix_sources_case_position", "sources", ["case_id", "position"])
    op.create_table(
        "source_versions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("sequence > 0", name="ck_source_versions_sequence"),
        sa.ForeignKeyConstraint(
            ["case_id", "source_id"],
            ["sources.case_id", "sources.source_id"],
            name="fk_source_versions_source",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_versions"),
        sa.UniqueConstraint("case_id", "source_id", "sequence", name="uq_source_versions_sequence"),
        sa.UniqueConstraint("id", "case_id", "source_id", name="uq_source_versions_owner"),
    )
    op.create_foreign_key(
        "fk_sources_current_version",
        "sources",
        "source_versions",
        ["current_version_id", "case_id", "source_id"],
        ["id", "case_id", "source_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("base_case_version", sa.Integer(), nullable=False),
        sa.Column("owner_instance_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "generation_profile",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_analysis_runs_status"
        ),
        sa.CheckConstraint("mode IN ('replay', 'live')", name="ck_analysis_runs_mode"),
        sa.CheckConstraint("content_revision > 0", name="ck_analysis_runs_content_revision"),
        sa.CheckConstraint("base_case_version > 0", name="ck_analysis_runs_base_case_version"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_analysis_runs_case"),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_runs"),
        sa.UniqueConstraint("case_id", "idempotency_key", name="uq_analysis_runs_idempotency"),
        sa.UniqueConstraint("id", "case_id", name="uq_analysis_runs_owner"),
    )
    op.create_index(
        "uq_analysis_runs_running_case",
        "analysis_runs",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_analysis_runs_case_started", "analysis_runs", ["case_id", "started_at", "id"]
    )
    op.create_table(
        "proposal_revisions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("analysis_run_id", sa.Text(), nullable=True),
        sa.Column("parent_revision_id", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("source_bindings", postgresql.JSONB(), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("origin IN ('model', 'operator')", name="ck_proposal_revisions_origin"),
        sa.CheckConstraint("content_revision > 0", name="ck_proposal_revisions_content_revision"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_proposal_revisions_case"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id", "case_id"],
            ["analysis_runs.id", "analysis_runs.case_id"],
            name="fk_proposal_revisions_run",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "case_id"],
            ["proposal_revisions.id", "proposal_revisions.case_id"],
            name="fk_proposal_revisions_parent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal_revisions"),
        sa.UniqueConstraint("id", "case_id", name="uq_proposal_revisions_owner"),
    )
    op.create_index(
        "ix_proposal_revisions_case_created", "proposal_revisions", ["case_id", "created_at", "id"]
    )
    op.create_foreign_key(
        "fk_cases_current_proposal",
        "cases",
        "proposal_revisions",
        ["current_proposal_revision_id", "id"],
        ["id", "case_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "validation_checks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("proposal_revision_id", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("issues", postgresql.JSONB(), nullable=False),
        sa.Column("resolved_spans", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "result IN ('blocked_for_approval', 'needs_operator_review')",
            name="ck_validation_checks_result",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_revision_id"],
            ["proposal_revisions.id"],
            name="fk_validation_checks_proposal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_checks"),
        sa.UniqueConstraint("id", "proposal_revision_id", name="uq_validation_checks_proposal"),
    )
    op.create_index(
        "ix_validation_checks_proposal_created",
        "validation_checks",
        ["proposal_revision_id", "created_at", "id"],
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("proposal_revision_id", sa.Text(), nullable=False),
        sa.Column("validation_check_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("source_bindings_digest", sa.Text(), nullable=False),
        sa.Column("result_case_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('approve', 'reject')", name="ck_review_decisions_kind"),
        sa.CheckConstraint("result_case_version > 0", name="ck_review_decisions_case_version"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_review_decisions_case"),
        sa.ForeignKeyConstraint(
            ["proposal_revision_id", "case_id"],
            ["proposal_revisions.id", "proposal_revisions.case_id"],
            name="fk_review_decisions_proposal",
        ),
        sa.ForeignKeyConstraint(
            ["validation_check_id", "proposal_revision_id"],
            ["validation_checks.id", "validation_checks.proposal_revision_id"],
            name="fk_review_decisions_validation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_decisions"),
        sa.UniqueConstraint("proposal_revision_id", name="uq_review_decisions_proposal"),
        sa.UniqueConstraint("case_id", "idempotency_key", name="uq_review_decisions_idempotency"),
    )
    op.create_index(
        "ix_review_decisions_case_created", "review_decisions", ["case_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_table("review_decisions")
    op.drop_table("validation_checks")
    op.drop_constraint("fk_cases_current_proposal", "cases", type_="foreignkey")
    op.drop_table("proposal_revisions")
    op.drop_table("analysis_runs")
    op.drop_constraint("fk_sources_current_version", "sources", type_="foreignkey")
    op.drop_table("source_versions")
    op.drop_table("sources")
    op.drop_table("cases")
