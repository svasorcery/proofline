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


@pytest.mark.parametrize("provider", ["groq", "deepseek", "anthropic", "openai"])
def test_selected_provider_uses_explicit_generic_configuration(provider):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline",
        live_enabled=True,
        llm_provider=provider,
        llm_model=" selected-model ",
        llm_api_key=SecretStr("selected-secret"),
    )
    assert settings.live_unavailable_reason is None
    assert settings.selected_model == "selected-model"
    assert settings.selected_api_key.get_secret_value() == "selected-secret"
    assert "selected-secret" not in repr(settings)
    assert "selected-secret" not in settings.model_dump_json()


@pytest.mark.parametrize("provider", ["groq", "deepseek", "anthropic"])
def test_legacy_openai_credentials_never_configure_other_providers(provider):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline",
        live_enabled=True,
        llm_provider=provider,
        openai_model="legacy-model",
        openai_api_key=SecretStr("legacy-secret"),
    )
    assert settings.live_unavailable_reason == "LIVE_KEY_MISSING"
    assert settings.selected_api_key is None and settings.selected_model == ""


def test_unknown_provider_is_rejected_even_when_live_is_disabled():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://localhost/proofline",
            llm_provider="grok",
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("low", "low"),
        (" medium ", "medium"),
        ("high", "high"),
    ],
)
def test_groq_reasoning_effort_from_environment(monkeypatch, value, expected):
    monkeypatch.delenv("GROQ_REASONING_EFFORT", raising=False)
    if value is not None:
        monkeypatch.setenv("GROQ_REASONING_EFFORT", value)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://localhost/proofline")
    assert settings.groq_reasoning_effort == expected


@pytest.mark.parametrize("value", ["none", "default", "xhigh", "MEDIUM"])
def test_groq_reasoning_effort_rejects_values_outside_the_profile(monkeypatch, value):
    monkeypatch.setenv("GROQ_REASONING_EFFORT", value)
    with pytest.raises(ValidationError, match="groq_reasoning_effort"):
        Settings(_env_file=None, database_url="postgresql+psycopg://localhost/proofline")


@pytest.mark.parametrize(
    "provider,model,effort",
    [
        ("openai", "qwen/qwen3.8-27b", "medium"),
        ("groq", "another-model", "medium"),
        ("groq", "qwen/qwen3.8-27b", None),
        ("groq", "qwen/qwen3.8-27b", "none"),
    ],
)
def test_v33_test_requires_the_controlled_qwen_configuration(monkeypatch, provider, model, effort):
    monkeypatch.setenv("GROQ_PROMPT_PROFILE", "v3.3-test")
    with pytest.raises(ValidationError, match="GROQ_PROMPT_PROFILE"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://localhost/proofline",
            llm_provider=provider,
            llm_model=model,
            groq_reasoning_effort=effort,
        )


def test_unknown_groq_prompt_profile_is_rejected(monkeypatch):
    monkeypatch.setenv("GROQ_PROMPT_PROFILE", "v3.3")
    with pytest.raises(ValidationError, match="groq_prompt_profile"):
        Settings(_env_file=None, database_url="postgresql+psycopg://localhost/proofline")


@pytest.mark.parametrize(
    "profile,effort,allowed",
    [
        ("v3.3-citation-ids-test", "low", True),
        ("v3.3-citation-ids-test", "none", False),
        ("v3.3-citation-ids-test", "medium", False),
        ("v3.3-choices-test", "low", False),
    ],
)
def test_gpt_oss_can_run_only_the_citation_id_experiment(profile, effort, allowed):
    options = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://localhost/proofline",
        "llm_provider": "groq",
        "llm_model": "openai/gpt-oss-20b",
        "groq_prompt_profile": profile,
        "groq_reasoning_effort": effort,
    }
    if allowed:
        assert Settings(**options).selected_model == "openai/gpt-oss-20b"
    else:
        with pytest.raises(ValidationError, match="GROQ_PROMPT_PROFILE"):
            Settings(**options)


def test_empty_generic_key_in_compose_preserves_legacy_openai_configuration():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/proofline",
        live_enabled=True,
        llm_api_key=SecretStr(""),
        llm_model="",
        openai_api_key=SecretStr("legacy-secret"),
        openai_model="legacy-model",
    )
    assert settings.live_unavailable_reason is None
    assert settings.selected_api_key.get_secret_value() == "legacy-secret"
