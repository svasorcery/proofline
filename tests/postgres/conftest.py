"""Real PostgreSQL isolation: each test owns one disposable schema."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "Set TEST_DATABASE_URL to the dedicated proofline_test database; "
            "see docs/development.md"
        )
    parsed = make_url(url)
    if parsed.database != "proofline_test" or parsed.get_backend_name() != "postgresql":
        pytest.fail("PostgreSQL tests require a database named proofline_test")
    schema = f"proofline_test_{uuid4().hex}"
    admin = create_engine(parsed)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        parsed, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
