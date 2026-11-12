"""Use an injected connection or explicitly configured DATABASE_URL."""

import os
from typing import cast

from alembic import context
from sqlalchemy import Connection

from proofline.persistence.database import open_database
from proofline.persistence.models import Base


def migrate_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        migrate_connection(cast(Connection, supplied))
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    database = open_database(url)
    try:
        with database.engine.connect() as connection:
            migrate_connection(connection)
    finally:
        database.dispose()


run_migrations()
