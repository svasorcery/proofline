"""Persist opaque guest credentials and independent copies of the demo cases."""

import re
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import select

from proofline.application.seed import seed_cases
from proofline.persistence.database import Database
from proofline.persistence.models import GuestSession, new_id
from proofline.providers.replay import ReplayCatalog

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def _digest(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


class GuestSessions:
    def __init__(self, database: Database, catalog: ReplayCatalog) -> None:
        self.database, self.catalog = database, catalog

    def resolve(self, token: str | None) -> str | None:
        if token is None or not _TOKEN.fullmatch(token):
            return None
        with self.database.sessions() as session:
            return session.scalar(
                select(GuestSession.id).where(GuestSession.token_digest == _digest(token))
            )

    def create(self) -> str:
        token, guest_id = token_urlsafe(32), new_id()
        with self.database.sessions.begin() as session:
            session.add(GuestSession(id=guest_id, token_digest=_digest(token)))
            session.flush()
            seed_cases(session, self.catalog, guest_id=guest_id)
        return token
