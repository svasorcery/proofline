"""Explicit migrations and a read-only readiness probe."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_REVISION = "0001_case_workflow"


def upgrade_database(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    # Reuse the supplied connection, including any caller-owned PostgreSQL search_path.
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, SCHEMA_REVISION)


def schema_is_current(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads() == (SCHEMA_REVISION,)
    except SQLAlchemyError:
        return False
