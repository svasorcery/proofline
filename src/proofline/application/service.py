"""The per-process application facade; storage and providers remain explicit dependencies."""

from threading import Lock
from typing import get_args
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from proofline.application.cases import CaseService
from proofline.application.proposals import ProposalService
from proofline.application.runs import RunService
from proofline.application.views import Capabilities
from proofline.config import Settings
from proofline.domain.assembly import EventType, FactKey, RequirementKey
from proofline.persistence.database import Database
from proofline.persistence.migrate import schema_is_current
from proofline.providers.live_profile import POLICY_VERSION, PROMPT_VERSION, SCHEMA_VERSION
from proofline.providers.openai_responses import LiveProvider, OpenAIResponsesProvider
from proofline.providers.replay import ReplayProvider


class WorkflowService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider: ReplayProvider,
        *,
        owner_instance_id: str | None = None,
        live_provider: LiveProvider | None = None,
    ) -> None:
        self.database, self.settings, self.provider = database, settings, provider
        self.owner_instance_id = owner_instance_id or str(uuid4())
        self._recovery_lock = Lock()
        self._recovery_complete = False
        self.cases = CaseService(database, settings)
        if live_provider is None and settings.live_unavailable_reason is None:
            live_provider = OpenAIResponsesProvider(settings)
        self.runs = RunService(database, settings, provider, self.owner_instance_id, live_provider)
        self.proposals = ProposalService(database, settings)

    def is_ready(self) -> bool:
        if not schema_is_current(self.database.engine):
            return False
        with self._recovery_lock:
            try:
                if not self._recovery_complete:
                    self.runs.recover_interrupted()
                    self._recovery_complete = True
                self.runs.reconcile_completions()
            except SQLAlchemyError:
                return False
        return True

    def recover_interrupted_runs(self) -> int:
        with self._recovery_lock:
            recovered = self.runs.recover_interrupted()
            self._recovery_complete = True
            return recovered

    def capabilities(self) -> Capabilities:
        return Capabilities.model_validate(
            {
                "replay_available": True,
                "live_available": self.settings.live_unavailable_reason is None,
                "live_reason": self.settings.live_unavailable_reason,
                "live_model": self.settings.openai_model or None,
                "live_access_verified": False,
                "live_profile": {
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "policy_version": POLICY_VERSION,
                    "quality_measured": False,
                },
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
                    "model_output_tokens": self.settings.model_max_output_tokens,
                    "model_timeout_seconds": self.settings.model_timeout_seconds,
                },
            }
        )
