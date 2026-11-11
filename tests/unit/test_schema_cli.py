import json
from pathlib import Path

import pytest

from proofline.schema import main


def test_cli_exports_generated_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["proofline.schema"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["properties"]["case_id"]["type"] == "string"


def test_cli_checks_frozen_contract_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = Path(__file__).resolve().parents[2] / "contracts/assembly-output.schema.json"
    original = contract.read_bytes()
    monkeypatch.setattr("sys.argv", ["proofline.schema", "--check", str(contract)])
    assert main() == 0
    assert contract.read_bytes() == original


def test_cli_reports_incompatible_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text('{"type": "array"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["proofline.schema", "--check", str(contract)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
