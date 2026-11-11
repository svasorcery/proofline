# Proofline

Proofline is a personal portfolio project for preparing an operator's review of a customer case: facts, source evidence, a timeline and specific missing confirmations. It does not authorise refunds, execute payments or send messages to customers.

**Current milestone: typed validation library.** The Python package provides strict Pydantic contracts, immutable source-version inputs, literal evidence resolution, consistency checks and deterministic display values. Persistence, the HTTP API, operator UI and model providers are future milestones.

## Develop and check

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```sh
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
uv run --locked python -m proofline.schema --check contracts/assembly-output.schema.json
```

Python 3.12 is the local default; the package supports Python 3.12+. Dependencies are recorded in `uv.lock`. The [CI workflow](.github/workflows/checks.yml) runs the runtime and historical checks on Python 3.12/3.14 under Linux and Windows. No API credentials or model calls are part of these checks.

The public entry point is `validate_assembly(output, context)`. Shape errors raise Pydantic `ValidationError`; semantic inconsistencies return field-specific issues with machine codes and readable messages. Resolved fragments include the source version, SHA256 and Unicode code-point offsets. A result with no blocking issues is always `needs_operator_review`, never an approval. See the [runnable usage example](docs/validation.md).

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
| [docs/validation.md](docs/validation.md) | Library usage, validation boundaries and supported timestamp format |
| [docs/research.md](docs/research.md) | Research decisions, evidence and limitations |
| [contracts](contracts/) | Exported output schema |
| [src/proofline](src/proofline/) | Standalone typed runtime library |
| [tests](tests/) | Unit tests and recorded-output integration checks |
| [fixtures](fixtures/README.md) | Synthetic cases and recorded replay inputs/outputs |
| [evals/baselines/v3](evals/baselines/v3/README.md) | Frozen measured baseline and offline verification |
| `notes/` | Optional local import archives and working notes; ignored by Git |

The application stack is specified as Python/FastAPI/Pydantic, PostgreSQL and React/TypeScript/Vite. The next milestone adds persistence, versioning and a replay-backed HTTP workflow. Live access will be explicit and separate, with no silent fallback or automatic approval. See the [implementation order](docs/spec.md#порядок-реализации).
