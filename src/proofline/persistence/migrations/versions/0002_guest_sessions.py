"""Add independent guest workspaces to the local operator schema."""

import sqlalchemy as sa
from alembic import op

revision = "0002_guest_sessions"
down_revision = "0001_case_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("token_digest", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guest_sessions"),
        sa.UniqueConstraint("token_digest", name="uq_guest_sessions_token_digest"),
    )
    op.add_column("cases", sa.Column("guest_id", sa.Text(), nullable=True))
    op.add_column("cases", sa.Column("fixture_case_id", sa.Text(), nullable=True))
    op.create_foreign_key("fk_cases_guest", "cases", "guest_sessions", ["guest_id"], ["id"])
    op.create_unique_constraint("uq_cases_guest_fixture", "cases", ["guest_id", "fixture_case_id"])
    op.create_check_constraint(
        "ck_cases_fixture_guest", "cases", "fixture_case_id IS NULL OR guest_id IS NOT NULL"
    )
    op.create_index("ix_cases_guest_updated_at", "cases", ["guest_id", "updated_at", "id"])


def downgrade() -> None:
    # Do not turn private guest cases into local operator cases when downgrading.
    op.execute("LOCK TABLE guest_sessions, cases IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM guest_sessions)")):
        raise RuntimeError("Cannot downgrade a database that contains guest sessions")
    op.drop_index("ix_cases_guest_updated_at", table_name="cases")
    op.drop_constraint("ck_cases_fixture_guest", "cases", type_="check")
    op.drop_constraint("uq_cases_guest_fixture", "cases", type_="unique")
    op.drop_constraint("fk_cases_guest", "cases", type_="foreignkey")
    op.drop_column("cases", "fixture_case_id")
    op.drop_column("cases", "guest_id")
    op.drop_table("guest_sessions")
