# Proofline

Proofline is a personal portfolio project for preparing an operator's review of a customer case: facts, source evidence, a timeline and specific missing confirmations. It does not authorise refunds, execute payments or send messages to customers.

**Current status: specification and reproducible research baseline. Application implementation has not started.** The repository contains synthetic cases, recorded model responses and deterministic checks. A backend, UI, database and application replay provider are future milestones.

## Check the recorded baseline

Run from the repository root with Python 3.12+; verified here on Python 3.14.6. No third-party packages, API keys, network access or Codex CLI are required.

```sh
python -B -X utf8 -m unittest discover -s evals/baselines/v3 -p "test_*.py"
python -B -X utf8 evals/baselines/v3/verify.py
```

The suite includes the original 19 regressions and two relocation/integrity tests. The verifier checks exact artifact hashes, reference data, recorded inputs, outputs and scores. It reproduces **7/9 complete passes, 72 literal fragments and 13 correct requested fact values**. V02 retains its missing timeline event; V04 retains its contradiction and is blocked by a separate post-run guard. Successful checks still require operator review.

These commands make no model calls and do not rewrite recorded results. The corrected v3.1 policy is **unmeasured**. All business data is synthetic; reference answers and reviews were AI-produced, with no independent human labelling or demonstrated ROI.

## Repository map

| Location | Purpose |
| --- | --- |
| [docs/spec.md](docs/spec.md) | Current implementation contract |
| [docs/development.md](docs/development.md) | Working with the repository and the next milestone |
| [docs/research.md](docs/research.md) | Research decisions, evidence and limitations |
| [contracts](contracts/) | Exported output schema |
| [fixtures](fixtures/README.md) | Synthetic cases and recorded replay inputs/outputs |
| [evals/baselines/v3](evals/baselines/v3/README.md) | Frozen measured baseline and offline verification |
| `notes/` | Optional local import archives and working notes; ignored by Git |

The application stack is specified as Python/FastAPI/Pydantic, PostgreSQL and React/TypeScript/Vite. Development starts with typed contracts and validators, followed by a replay-backed operator workflow. Live access will be explicit and separate, with no silent fallback or automatic approval. See the [implementation order](docs/spec.md#порядок-реализации).
