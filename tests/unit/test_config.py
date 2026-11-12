from pathlib import Path

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    "origin", ["https://example.test/path", "null", "https://a:b@example.test"]
)
def test_origin_must_be_an_origin_not_an_arbitrary_url(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://localhost/proofline", app_origin=origin)
