"""Database-owned invariants are exercised against migrated PostgreSQL schemas."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from proofline.persistence.database import open_database
from proofline.persistence.migrate import SCHEMA_REVISION, schema_is_current, upgrade_database
from proofline.persistence.models import (
    AnalysisRun,
    Base,
    Case,
    ProposalRevision,
    ReviewDecision,
    Source,
    SourceVersion,
    ValidationCheck,
)

pytestmark = pytest.mark.postgres


def add_case(session: Session, case_id: str) -> None:
    session.add(
        Case(id=case_id, title="Synthetic case", user_text="Check evidence", requested_fact_keys=[])
    )
    session.flush()


def add_source(session: Session, case_id: str, source_id: str = "S01") -> str:
    session.add(
        Source(
            case_id=case_id,
            source_id=source_id,
            kind="document",
            locator=source_id,
            title="Receipt",
            trust="customer",
            position=0,
            included=True,
        )
    )
    session.flush()
    version_id = str(uuid4())
    session.add(
        SourceVersion(
            id=version_id,
            case_id=case_id,
            source_id=source_id,
            sequence=1,
            text="Evidence",
            text_sha256="a" * 64,
        )
    )
    session.flush()
    source = session.get(Source, (case_id, source_id))
    assert source is not None
    source.current_version_id = version_id
    return version_id


def add_run(session: Session, case_id: str, key: str, status: str = "running") -> str:
    run_id = str(uuid4())
    session.add(
        AnalysisRun(
            id=run_id,
            case_id=case_id,
            mode="replay",
            idempotency_key=key,
            request_hash="b" * 64,
            snapshot={},
            input_digest="c" * 64,
            content_revision=1,
            base_case_version=1,
            owner_instance_id="test",
            status=status,
        )
    )
    session.flush()
    return run_id


def add_proposal(
    session: Session, case_id: str, run_id: str | None = None, parent_id: str | None = None
) -> str:
    proposal_id = str(uuid4())
    session.add(
        ProposalRevision(
            id=proposal_id,
            case_id=case_id,
            analysis_run_id=run_id,
            parent_revision_id=parent_id,
            origin="operator",
            content_revision=1,
            input_digest="c" * 64,
            snapshot={},
            source_bindings={},
            body={},
            body_sha256="d" * 64,
        )
    )
    session.flush()
    return proposal_id


def add_check(session: Session, proposal_id: str) -> str:
    check_id = str(uuid4())
    session.add(
        ValidationCheck(
            id=check_id,
            proposal_revision_id=proposal_id,
            profile_version="v1",
            result="needs_operator_review",
            issues=[],
            resolved_spans=[],
        )
    )
    session.flush()
    return check_id


def add_decision(session: Session, case_id: str, proposal_id: str, check_id: str, key: str) -> None:
    session.add(
        ReviewDecision(
            case_id=case_id,
            proposal_revision_id=proposal_id,
            validation_check_id=check_id,
            kind="approve",
            actor="demo_operator",
            reason="Reviewed evidence",
            idempotency_key=key,
            request_hash="e" * 64,
            body_sha256="d" * 64,
            source_bindings_digest="f" * 64,
            result_case_version=2,
        )
    )
    session.flush()


def test_readiness_does_not_create_tables_and_upgrade_is_idempotent(
    postgres_engine: Engine,
) -> None:
    assert not schema_is_current(postgres_engine)
    assert inspect(postgres_engine).get_table_names() == []
    upgrade_database(postgres_engine)
    upgrade_database(postgres_engine)
    assert schema_is_current(postgres_engine)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION


def test_readiness_returns_false_when_database_is_unavailable() -> None:
    engine = create_engine("postgresql+psycopg://test:test@127.0.0.1:1/missing?connect_timeout=1")
    try:
        assert not schema_is_current(engine)
    finally:
        engine.dispose()


def test_database_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        open_database("sqlite://")


def test_database_sessions_retain_committed_values(postgres_engine: Engine) -> None:
    upgrade_database(postgres_engine)
    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    url = postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    database = open_database(url.render_as_string(hide_password=False))
    try:
        with database.sessions.begin() as session:
            case = Case(
                id="C01", title="Synthetic case", user_text="Review", requested_fact_keys=[]
            )
            session.add(case)
        assert case.title == "Synthetic case"
        with Session(postgres_engine) as session:
            assert session.get(Case, "C01") is not None
    finally:
        database.dispose()


def test_cli_upgrade_check_and_downgrade(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    url = postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = os.environ | {"DATABASE_URL": url.render_as_string(hide_password=False)}
    root = Path(__file__).resolve().parents[2]
    for arguments in (("upgrade", "head"), ("check",), ("downgrade", "base")):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini"), *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        if arguments[0] == "upgrade":
            assert schema_is_current(postgres_engine)
    assert not schema_is_current(postgres_engine)
    assert inspect(postgres_engine).get_table_names() == ["alembic_version"]


def test_current_pointers_are_deferred_until_commit(postgres_engine: Engine) -> None:
    upgrade_database(postgres_engine)
    proposal_id, version_id = str(uuid4()), str(uuid4())
    with Session(postgres_engine) as session, session.begin():
        session.add(
            Case(
                id="C01",
                title="Synthetic case",
                user_text="Review",
                requested_fact_keys=[],
                current_proposal_revision_id=proposal_id,
            )
        )
        session.flush()
        session.add(
            Source(
                case_id="C01",
                source_id="S01",
                kind="document",
                locator="receipt",
                title="Receipt",
                trust="customer",
                position=0,
                included=True,
                current_version_id=version_id,
            )
        )
        session.flush()
        session.add(
            SourceVersion(
                id=version_id,
                case_id="C01",
                source_id="S01",
                sequence=1,
                text="Receipt",
                text_sha256="a" * 64,
            )
        )
        session.add(
            ProposalRevision(
                id=proposal_id,
                case_id="C01",
                origin="operator",
                content_revision=1,
                input_digest="b" * 64,
                snapshot={},
                source_bindings={},
                body={},
                body_sha256="c" * 64,
            )
        )
    with Session(postgres_engine) as session:
        source = session.get(Source, ("C01", "S01"))
        case = session.get(Case, "C01")
        assert source is not None and source.current_version_id == version_id
        assert case is not None and case.current_proposal_revision_id == proposal_id


def test_migration_matches_models_and_utc_defaults(postgres_engine: Engine) -> None:
    upgrade_database(postgres_engine)
    with postgres_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []
    with Session(postgres_engine) as session, session.begin():
        add_case(session, "C01")
        add_source(session, "C01")
        case = session.get(Case, "C01")
        assert case is not None
        assert case.content_revision == case.version == 1
        assert case.context == {}
        assert case.money is None
        assert case.created_at.utcoffset().total_seconds() == 0


def test_source_ids_are_scoped_and_current_version_cannot_cross_owner(
    postgres_engine: Engine,
) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        add_case(session, "C01")
        add_case(session, "C02")
        first_version = add_source(session, "C01")
        add_source(session, "C02")
        other_source_version = add_source(session, "C01", "S02")
    for case_id, invalid_version in (("C02", first_version), ("C01", other_source_version)):
        with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
            source = session.get(Source, (case_id, "S01"))
            assert source is not None
            source.current_version_id = invalid_version


def test_only_one_running_run_and_case_scoped_idempotency(postgres_engine: Engine) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        add_case(session, "C01")
        add_case(session, "C02")
        run_id = add_run(session, "C01", "shared-key")
        add_run(session, "C02", "shared-key")
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_run(session, "C01", "another-key")
    with Session(postgres_engine) as session, session.begin():
        run = session.get(AnalysisRun, run_id)
        assert run is not None
        run.status = "succeeded"
        session.flush()
        add_run(session, "C01", "another-key")
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_run(session, "C01", "shared-key", "failed")


@pytest.mark.parametrize("foreign_reference", ["active", "parent", "run"])
def test_proposal_references_cannot_cross_cases(
    postgres_engine: Engine, foreign_reference: str
) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        add_case(session, "C01")
        add_case(session, "C02")
        run_id = add_run(session, "C01", "run", "succeeded")
        proposal_id = add_proposal(session, "C01", run_id)
        case = session.get(Case, "C01")
        assert case is not None
        case.current_proposal_revision_id = proposal_id
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        if foreign_reference == "active":
            case = session.get(Case, "C02")
            assert case is not None
            case.current_proposal_revision_id = proposal_id
        elif foreign_reference == "parent":
            add_proposal(session, "C02", parent_id=proposal_id)
        else:
            add_proposal(session, "C02", run_id)


def test_decision_is_unique_per_revision_and_bound_to_check(postgres_engine: Engine) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        add_case(session, "C01")
        add_case(session, "C02")
        proposal_id = add_proposal(session, "C01")
        check_id = add_check(session, proposal_id)
        add_decision(session, "C01", proposal_id, check_id, "shared-key")
        other_proposal_id = add_proposal(session, "C02")
        other_check_id = add_check(session, other_proposal_id)
        add_decision(session, "C02", other_proposal_id, other_check_id, "shared-key")
        new_proposal_id = add_proposal(session, "C01", parent_id=proposal_id)
        new_check_id = add_check(session, new_proposal_id)
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_decision(session, "C01", proposal_id, check_id, "new-key")
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_decision(session, "C01", new_proposal_id, check_id, "new-key")
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_decision(session, "C02", new_proposal_id, new_check_id, "new-key")
    with pytest.raises(IntegrityError), Session(postgres_engine) as session, session.begin():
        add_decision(session, "C01", new_proposal_id, new_check_id, "shared-key")
    with Session(postgres_engine) as session:
        assert len(session.scalars(select(ReviewDecision)).all()) == 2
