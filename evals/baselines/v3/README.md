# Recorded v3 baseline

This directory preserves the measured v3 evaluator and its data dependencies in a standalone layout. It is a research baseline, not application runtime. Reference answers and the evaluator must never supply missing model answers in the application.

From the repository root:

```sh
python -B -X utf8 -m unittest discover -s evals/baselines/v3 -p "test_*.py"
python -B -X utf8 evals/baselines/v3/verify.py
```

Only the Python standard library is required. Verification reads files, makes no network/model calls and writes no results. The tests additionally create and remove temporary standalone copies. The expected recorded outcome is 7/9 complete passes, with V02 and V04 still failing; the separate post-run guard blocks V04 and sends eight cards for operator review.

## Files and boundaries

- [contract.py](contract.py), [evaluate.py](evaluate.py), [approval_gate.py](approval_gate.py), the two original contract/evaluator test files, references and policies retain their original bytes.
- [test_approval_gate.py](test_approval_gate.py) retains the original three scenarios with relocated file paths; its original bytes are preserved under provenance for the post-run hash check.
- [reference_checks.py](reference_checks.py) extracts the deterministic reference-validation functions from the original runner, with no model transport. [verify.py](verify.py) checks hashes, validates the six full and three ablation references, matches recorded prompts to inputs and reproduces every original score and guard result.
- [test_relocation.py](test_relocation.py) verifies operation without local archives and rejects a modified recorded answer.
- [recorded/scores.json](recorded/scores.json) and [recorded/summary.json](recorded/summary.json) are unchanged historical results. The summary explicitly separates the later guard from the measured generation.
- [manifest.json](manifest.json) maps current repository-relative paths to original source paths and SHA256 values. `source_path` is provenance, not a file dependency. The original measurement manifest remains unchanged in [provenance/original-manifest.json](provenance/original-manifest.json); the verifier resolves its old Windows paths through the new mapping.
- [provenance/METHOD.md](provenance/METHOD.md) is the original methodology. `run_v3.py.txt` and `run.py.txt` retain exact original runner/transport bytes as inert source text, solely to check the historical input hashes. They are not imported or executed. Their historical invocation budget grants no permission for new calls.
- [policy_v3.json](policy_v3.json) was measured. [policy_v3_1_unmeasured.json](policy_v3_1_unmeasured.json) is a later candidate with `generation_tested=false`; it has no new model quality measurement.

The cases and replay records live in [fixtures](../../../fixtures/README.md). The [research explanation](../../../docs/research.md) states the limitations; [operator cards](../../../docs/research/v3-operator-cards.md) show the recorded responses. Full earlier experiments and migration paperwork are optional local archives and are not needed here.

For new work, copy and adapt components into application modules or a new versioned evaluation. Preserve this baseline and its byte-preservation rules in `.gitattributes`.
