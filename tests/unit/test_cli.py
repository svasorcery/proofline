import pytest

from proofline.cli import main


def test_help_does_not_require_database_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["proofline", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert "migrate" in capsys.readouterr().out


def test_invalid_configuration_does_not_print_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://user:sensitive-password@localhost/db")
    monkeypatch.setattr("sys.argv", ["proofline", "migrate"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "database_url" in error
    assert "sensitive-password" not in error
