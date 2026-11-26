from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from proofline.domain.case_evidence import ReviewedRefund
from proofline.domain.inputs import AnalysisInput, json_digest
from proofline.domain.present_confirmations import IncludedAcceptanceReply
from proofline.domain.required_events import RequiredEvent

GROQ_CITATION_IDS_PROFILE = "v3.3-citation-ids-test"
GROQ_CONFIRMATION_PROFILES = frozenset({"v3.3-confirmations-test", GROQ_CITATION_IDS_PROFILE})
GROQ_CHOICES_PROFILES = GROQ_CONFIRMATION_PROFILES | {"v3.3-choices-test"}
GUEST_GROQ_MODEL = "openai/gpt-oss-20b"
GUEST_MAX_COMPLETION_TOKENS = 1_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True, hide_input_in_errors=True
    )

    database_url: str = Field(repr=False)
    app_origin: str = "http://127.0.0.1:8000"
    dev_origins: tuple[str, ...] = ()
    fixtures_dir: Path = Field(default=Path("fixtures"), validation_alias="PROOFLINE_FIXTURES_DIR")
    ui_dir: Path = Field(default=Path("frontend/dist"), validation_alias="PROOFLINE_UI_DIR")
    live_enabled: bool = False
    guest_mode: bool = False
    llm_provider: Literal["openai", "groq", "deepseek", "anthropic"] = "openai"
    llm_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    llm_model: str = ""
    groq_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    groq_prompt_profile: Literal[
        "v3.2",
        "v3.3-test",
        "v3.3-choices-test",
        "v3.3-confirmations-test",
        "v3.3-citation-ids-test",
    ] = "v3.2"
    groq_test_lookup_sources: dict[str, tuple[str, ...]] | None = None
    groq_test_acceptance_replies: dict[str, IncludedAcceptanceReply] | None = None
    groq_test_required_events: dict[str, tuple[RequiredEvent, ...]] | None = None
    groq_test_case_evidence: dict[str, ReviewedRefund] | None = None
    groq_test_assemble_reviewed_evidence: bool = False
    openai_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    openai_model: str = ""
    validation_profile: str = Field(default="proofline-validation-v1", min_length=1)
    max_user_characters: int = Field(default=8000, gt=0)
    max_sources: int = Field(default=20, gt=0)
    max_source_characters: int = Field(default=20000, gt=0)
    max_selected_characters: int = Field(default=80000, gt=0)
    model_timeout_seconds: int = Field(default=90, gt=0)
    model_max_output_tokens: int = Field(default=4096, gt=0)

    @property
    def effective_validation_profile(self) -> str:
        if self.groq_prompt_profile not in GROQ_CONFIRMATION_PROFILES:
            return self.validation_profile
        event_bindings = {
            case_id: binding.model_dump(mode="json")
            for case_id, binding in (self.groq_test_acceptance_replies or {}).items()
            if binding.event_evidence is not None
        }
        version = self.validation_profile
        if event_bindings:
            version += f"+reply-events-test-v1:{json_digest(event_bindings)}"
        if self.groq_prompt_profile == GROQ_CITATION_IDS_PROFILE and self.groq_test_required_events:
            timeline = {
                case_id: [mark.model_dump(mode="json") for mark in marks]
                for case_id, marks in self.groq_test_required_events.items()
            }
            timeline_version = (
                4
                if any(
                    mark.event_type == "supplier_cancelled" and mark.time.role == "occurred"
                    for mark in self.groq_test_required_events.get("V05", ())
                )
                else 3
            )
            version += f"+required-timeline-test-v{timeline_version}:{json_digest(timeline)}"
        if self.groq_test_case_evidence is not None:
            reviewed = {
                case_id: binding.model_dump(mode="json")
                for case_id, binding in self.groq_test_case_evidence.items()
            }
            version += f"+case-evidence-test-v1:{json_digest(reviewed)}"
        if self.groq_test_assemble_reviewed_evidence:
            version += "+reviewed-evidence-test-v3"
        return version

    def required_events(self, snapshot: AnalysisInput) -> tuple[RequiredEvent, ...]:
        if (
            self.groq_prompt_profile != GROQ_CITATION_IDS_PROFILE
            or not self.groq_test_required_events
        ):
            return ()
        sources = {source.source_id: source for source in snapshot.catalog}
        return tuple(
            mark
            for mark in self.groq_test_required_events.get(snapshot.case_id, ())
            if mark.matches(sources.get(mark.source_id))
        )

    @property
    def selected_api_key(self) -> SecretStr | None:
        # Legacy OpenAI credentials must never cross a provider boundary.
        if self.llm_api_key is not None and self.llm_api_key.get_secret_value().strip():
            return self.llm_api_key
        return self.openai_api_key if self.llm_provider == "openai" else None

    @property
    def selected_model(self) -> str:
        return self.llm_model or (self.openai_model if self.llm_provider == "openai" else "")

    @property
    def live_unavailable_reason(self) -> str | None:
        if not self.live_enabled:
            return "LIVE_DISABLED"
        key = self.selected_api_key
        if key is None or not key.get_secret_value().strip():
            return "LIVE_KEY_MISSING"
        if not self.selected_model:
            return "LIVE_MODEL_MISSING"
        return None

    @field_validator("openai_model", "llm_model")
    @classmethod
    def trim_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("groq_reasoning_effort", mode="before")
    @classmethod
    def blank_effort_is_unset(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_groq_test_profile(self) -> Self:
        if (
            self.guest_mode
            and self.live_enabled
            and (
                self.llm_provider != "groq"
                or self.selected_model != GUEST_GROQ_MODEL
                or self.groq_reasoning_effort != "low"
                or self.model_max_output_tokens > GUEST_MAX_COMPLETION_TOKENS
            )
        ):
            raise ValueError(
                "GUEST_MODE Guest Live requires Groq GPT-OSS 20B, low reasoning, "
                "and at most 1000 completion tokens"
            )
        if (
            self.groq_test_assemble_reviewed_evidence
            and self.groq_prompt_profile != GROQ_CITATION_IDS_PROFILE
        ):
            raise ValueError(
                "GROQ_TEST_ASSEMBLE_REVIEWED_EVIDENCE requires the citation ID test profile"
            )
        if (
            self.groq_reasoning_effort == "none"
            and self.groq_prompt_profile != GROQ_CITATION_IDS_PROFILE
        ):
            raise ValueError(
                "groq_reasoning_effort=none requires the citation ID GROQ_PROMPT_PROFILE"
            )
        if self.groq_prompt_profile != "v3.2":
            qwen_setting = self.llm_model == "qwen/qwen3.8-27b" and (
                self.groq_reasoning_effort in {"medium", "none"}
            )
            gpt_oss_setting = (
                self.groq_prompt_profile == GROQ_CITATION_IDS_PROFILE
                and self.llm_model == "openai/gpt-oss-20b"
                and self.groq_reasoning_effort == "low"
            )
            if self.llm_provider != "groq" or not (qwen_setting or gpt_oss_setting):
                raise ValueError(
                    "The selected GROQ_PROMPT_PROFILE requires an approved Groq model "
                    "and reasoning effort for that test"
                )
        if self.groq_test_lookup_sources and self.groq_prompt_profile not in GROQ_CHOICES_PROFILES:
            raise ValueError("GROQ_TEST_LOOKUP_SOURCES requires an isolated Groq choices profile")
        if (
            self.groq_test_acceptance_replies
            and self.groq_prompt_profile not in GROQ_CONFIRMATION_PROFILES
        ):
            raise ValueError(
                "GROQ_TEST_ACCEPTANCE_REPLIES requires an isolated Groq confirmations profile"
            )
        if (
            self.groq_test_case_evidence is not None
            and self.groq_prompt_profile != GROQ_CITATION_IDS_PROFILE
        ):
            raise ValueError("GROQ_TEST_CASE_EVIDENCE requires the citation ID test profile")
        if self.groq_test_required_events:
            if self.groq_prompt_profile != GROQ_CITATION_IDS_PROFILE:
                raise ValueError("GROQ_TEST_REQUIRED_EVENTS requires the citation ID test profile")
            if any(
                len({mark.identity for mark in marks}) != len(marks)
                for marks in self.groq_test_required_events.values()
            ):
                raise ValueError("GROQ_TEST_REQUIRED_EVENTS has repeated event identities")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        return value

    @field_validator("app_origin")
    @classmethod
    def origin_only(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Expected an HTTP origin without credentials or a path")
        return value.rstrip("/")

    @field_validator("dev_origins")
    @classmethod
    def validate_dev_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(cls.origin_only(origin) for origin in value)
