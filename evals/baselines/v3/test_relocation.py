"""The relocated baseline must work without local archives and reject data drift."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[3]


class BaselineRelocationTests(unittest.TestCase):
    def isolated_copy(self, parent):
        target = Path(parent) / "standalone"
        target.mkdir()
        for name in ("contracts", "fixtures", "evals", "docs"):
            shutil.copytree(PROJECT / name, target / name)
        return target

    def run_verifier(self, root):
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "evals/baselines/v3/verify.py"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_standalone_copy_preserves_measured_failures_and_operator_gate(self):
        with tempfile.TemporaryDirectory(prefix=".proofline-check-", dir=PROJECT) as folder:
            root = self.isolated_copy(folder)
            result = self.run_verifier(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["recorded_conditions"], 9)
            self.assertEqual(report["pack_pass"], 7)
            self.assertEqual(report["failed_conditions"], ["full/V02", "full/V04"])
            self.assertEqual(report["literal_fragments"], 72)
            self.assertEqual(report["fact_values_correct"], 13)
            self.assertEqual(report["blocked_for_approval"], ["full/V04"])
            self.assertEqual(report["needs_operator_review"], 8)
            self.assertEqual(report["new_model_calls"], 0)

    def test_changed_recording_fails_instead_of_silently_rebasing_metrics(self):
        with tempfile.TemporaryDirectory(prefix=".proofline-check-", dir=PROJECT) as folder:
            root = self.isolated_copy(folder)
            recording = root / "fixtures/replay/v3/full/V04/pack.json"
            pack = json.loads(recording.read_text(encoding="utf-8"))
            pack["facts"][0]["value"] = "confirmed"
            recording.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            result = self.run_verifier(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA256 mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
