from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    def live_unavailable_reason(self) -> str | None:
        if not self.live_enabled:
            return "LIVE_DISABLED"
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            return "LIVE_KEY_MISSING"
        if not self.openai_model.strip():
            return "LIVE_MODEL_MISSING"
        return None

    @field_validator("openai_model")
    @classmethod
    def trim_model(cls, value: str) -> str:
        return value.strip()

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
