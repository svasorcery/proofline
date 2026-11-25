"""Recorded live hashes must survive Git clean filters across operating systems."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("folder", ["evals/live", "evals/diagnostics"])
def test_recorded_artifact_bytes_are_not_normalised_by_git(tmp_path, folder):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    shutil.copyfile(ROOT / ".gitattributes", tmp_path / ".gitattributes")
    original = b'{\r\n  "recorded": true\r\n}\r\n'
    command = ["git", "-C", str(tmp_path), "hash-object", "--stdin"]
    raw = subprocess.check_output([*command, "--no-filters"], input=original)
    stored = subprocess.check_output(
        [*command, "--path", folder + "/version/run.json"], input=original
    )
    assert stored == raw
