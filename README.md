# Proofline

Proofline is a personal portfolio project for preparing an operator's review of a customer case: facts, source evidence, a timeline and specific missing confirmations. It does not authorise refunds, execute payments or send messages to customers.

**Current milestone: PostgreSQL-backed replay API.** The application stores source versions, analysis runs, proposal revisions, validation checks and explicit operator decisions. Replay uses exact recorded inputs; live model access and the operator UI are future milestones.

## Run the local demo

Docker Compose is the complete application path. From the repository root:

```sh
docker compose -f infrastructure/compose.yml up -d --wait db
docker compose -f infrastructure/compose.yml run --build --rm app migrate
docker compose -f infrastructure/compose.yml run --rm app seed
docker compose -f infrastructure/compose.yml up -d --wait app
python examples/replay_walkthrough.py
```

Open [the API documentation](http://127.0.0.1:8000/docs). The walkthrough demonstrates the recorded V04 contradiction, an explicit operator correction and an explicit review decision, while preserving the original response. It uses synthetic data, makes no model calls and sends no customer messages. Re-running seed preserves existing edits.

Only the API is published on loopback; the application database has no host port. The demo has one local operator and one API process. See [API usage and boundaries](docs/api.md) before changing its deployment or data.

## Develop and check

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```sh
uv sync --locked
uv run --locked pytest -m "not postgres"
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
uv run --locked python -m proofline.schema --check contracts/assembly-output.schema.json
```

Python 3.12 is the local default; the package supports Python 3.12+. Dependencies are recorded in `uv.lock`. PostgreSQL integration and concurrency tests require the dedicated test database; [development instructions](docs/development.md) give the commands. The [CI workflow](.github/workflows/checks.yml) checks Python 3.12/3.14 on Linux and Windows, real PostgreSQL on Linux, and the Compose walkthrough. No model credentials or calls are part of these checks.

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
| [docs/api.md](docs/api.md) | Replay HTTP workflow, versions, conflicts and local configuration |
| [docs/validation.md](docs/validation.md) | Library usage, validation boundaries and supported timestamp format |
| [docs/research.md](docs/research.md) | Research decisions, evidence and limitations |
| [contracts](contracts/) | Exported output schema |
| [src/proofline](src/proofline/) | Typed library, transactional services and HTTP API |
| [tests](tests/) | Unit, PostgreSQL, HTTP and concurrency checks |
| [infrastructure](infrastructure/) | Pinned container images and local Compose paths |
| [fixtures](fixtures/README.md) | Synthetic cases and recorded replay inputs/outputs |
| [evals/baselines/v3](evals/baselines/v3/README.md) | Frozen measured baseline and offline verification |
| `notes/` | Optional local import archives and working notes; ignored by Git |

The application uses Python/FastAPI/Pydantic, SQLAlchemy and PostgreSQL. The next milestone is a React/TypeScript/Vite operator UI. Live access will be explicit and separate, with no silent fallback or automatic approval. See the [implementation order](docs/spec.md#порядок-реализации).
