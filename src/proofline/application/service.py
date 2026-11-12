"""The per-process application facade; storage and providers remain explicit dependencies."""

from threading import Lock
from typing import get_args
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy.exc import SQLAlchemyError

from proofline.application.cases import CaseService
from proofline.application.proposals import ProposalService
from proofline.application.runs import RunService
from proofline.config import Settings
from proofline.domain.assembly import EventType, FactKey, RequirementKey
from proofline.persistence.database import Database
from proofline.persistence.migrate import schema_is_current
from proofline.providers.replay import ReplayProvider


class WorkflowService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider: ReplayProvider,
        *,
        owner_instance_id: str | None = None,
    ) -> None:
        self.database, self.settings, self.provider = database, settings, provider
        self.owner_instance_id = owner_instance_id or str(uuid4())
        self._recovery_lock = Lock()
        self._recovery_complete = False
        self.cases = CaseService(database, settings)
        self.runs = RunService(database, settings, provider, self.owner_instance_id)
        self.proposals = ProposalService(database, settings)

    def is_ready(self) -> bool:
        if not schema_is_current(self.database.engine):
            return False
        with self._recovery_lock:
            if not self._recovery_complete:
                try:
                    self.runs.recover_interrupted()
                except SQLAlchemyError:
                    return False
                self._recovery_complete = True
        return True

    def recover_interrupted_runs(self) -> int:
        with self._recovery_lock:
            recovered = self.runs.recover_interrupted()
            self._recovery_complete = True
            return recovered

    def capabilities(self) -> dict[str, JsonValue]:
        return {
            "replay_available": True,
            "live_available": False,
            "live_reason": "Live adapter is not implemented in this milestone.",
            "default_mode": "replay",
            "validation_profile": self.settings.validation_profile,
            "fact_keys": list(get_args(FactKey.__value__)),
            "event_types": list(get_args(EventType.__value__)),
            "requirement_keys": list(get_args(RequirementKey.__value__)),
            "limits": {
                "user_characters": self.settings.max_user_characters,
                "sources": self.settings.max_sources,
                "source_characters": self.settings.max_source_characters,
                "selected_characters": self.settings.max_selected_characters,
            },
        }
