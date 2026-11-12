from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True, hide_input_in_errors=True
    )

    database_url: str = Field(repr=False)
    app_origin: str = "http://127.0.0.1:8000"
    dev_origins: tuple[str, ...] = ()
    fixtures_dir: Path = Field(default=Path("fixtures"), validation_alias="PROOFLINE_FIXTURES_DIR")
    live_enabled: bool = False
    validation_profile: str = Field(default="proofline-validation-v1", min_length=1)
    max_user_characters: int = Field(default=8000, gt=0)
    max_sources: int = Field(default=20, gt=0)
    max_source_characters: int = Field(default=20000, gt=0)
    max_selected_characters: int = Field(default=80000, gt=0)
    model_timeout_seconds: int = Field(default=90, gt=0)

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
