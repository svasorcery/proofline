# Groq Free single-profile check

The isolated Compose project used a new seeded PostgreSQL database and one
in-process FastAPI application for the live sequence. The app had no published
port. V01 excluded the original reply through the ordinary source update route.
The full snapshots were kept for V02–V06. All cases used the same settings from
the repository profile, with source versions rebound to the fresh database.

Offline preflight pinned all six snapshots, case versions, system instructions,
provider schemas and complete request bodies. The live process compared those
receipts before reading the key from standard input. The ordinary run route
checked the case version and used one idempotency key per case. The transport
compared the prepared HTTP body to the preflight copy and reserved each send on
disk. It allowed only the configured Groq Chat Completions endpoint, with a
65-second minimum gap and zero retries.

The Free plan and visible request and token limits were checked before the run.
The published combined token allowance and response headers exposed only part
of the effective limits. The Groq request log identified a separate output
token limit after the V03 refusal. The exact rejection category and quantities
are recorded in the sanitized console receipt.

The frozen V3 evaluator grades the transformed cards against the corresponding
full or ablation references. The production workflow also checks marked events,
selected evidence and source versions. These are six synthetic conditions with
reviewed source-bound aids. Historical live results have their own settings and
are outside this run's quality count.
