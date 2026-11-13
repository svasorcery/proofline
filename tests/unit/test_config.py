from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from proofline.config import Settings


def test_configuration_rejects_a_silent_database_substitute() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///:memory:")


def test_settings_are_explicit_and_safe_for_replay() -> None:
    settings = Settings(database_url="postgresql+psycopg://demo:secret@localhost/proofline")
    assert not settings.live_enabled
    assert settings.fixtures_dir == Path("fixtures")
    assert "secret" not in repr(settings)
    assert settings.max_selected_characters == 80000


def test_live_configuration_is_explicit_and_secret_safe() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline",
        live_enabled=True,
        openai_api_key=SecretStr("private-key"),
        openai_model="selected-model",
    )
    assert settings.live_unavailable_reason is None
    assert "private-key" not in repr(settings)
    assert "private-key" not in settings.model_dump_json()
    assert settings.model_max_output_tokens == 4096


@pytest.mark.parametrize(
    "enabled,key,model,reason",
    [
        (False, "key", "model", "LIVE_DISABLED"),
        (True, "", "model", "LIVE_KEY_MISSING"),
        (True, "  ", "model", "LIVE_KEY_MISSING"),
        (True, "key", "", "LIVE_MODEL_MISSING"),
    ],
)
def test_missing_live_configuration_does_not_break_replay(enabled, key, model, reason) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline",
        live_enabled=enabled,
        openai_api_key=SecretStr(key),
        openai_model=model,
    )
    assert settings.live_unavailable_reason == reason


@pytest.mark.parametrize(
    "origin", ["https://example.test/path", "null", "https://a:b@example.test"]
)
def test_origin_must_be_an_origin_not_an_arbitrary_url(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://localhost/proofline", app_origin=origin)
