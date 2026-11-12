"""Explicit PostgreSQL engine/session lifecycle; imports never connect."""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


@dataclass(slots=True)
class Database:
    engine: Engine
    sessions: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def open_database(url: str) -> Database:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("Proofline requires PostgreSQL")
    engine = create_engine(parsed, pool_pre_ping=True)
    return Database(engine=engine, sessions=sessionmaker(engine, expire_on_commit=False))
