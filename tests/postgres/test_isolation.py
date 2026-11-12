from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.postgres


def test_test_schema_cannot_fall_back_to_public_tables(postgres_engine: Engine) -> None:
    name = f"proofline_isolation_probe_{uuid4().hex}"
    # DDL is transactional: a failed assertion rolls back this uniquely owned probe.
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE TABLE public."{name}" (id integer)'))
        assert connection.scalar(text("SELECT to_regclass(:name)"), {"name": name}) is None
        connection.execute(text(f'DROP TABLE public."{name}"'))
