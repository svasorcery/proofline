# Groq Chat limited acceptance v3

Author approved 17 November 2026: only two requests, full V02 then full V04,
Groq Free, openai/gpt-oss-120b, $0, stop on first technical error, no retries,
repair or fallback. Unused budgets from previous trials are not reused. The
first condition is also the access/schema probe; there is no extra generation.
Free plan is an author attestation, not an API billing audit. Previously supplied
quotas: 30 RPM, 1000 RPD, 8000 TPM, 200000 TPD. Keep at least 65 seconds between
request starts. Timeout or uncertain transport outcome consumes a reservation;
use read-only reconciliation, never reissue the model command.

Fixed: Chat Completions response_format.json_schema.strict=true; n=1, no stream,
tools disabled, max_completion_tokens=4096, deadline 90 seconds, SDK max_retries=0.
Reasoning/sampling use provider defaults. Preserve policy v3.2, prompt v2 candidate,
schema v1 and their hashes. No policy/schema tuning during the trial.

Use a fresh Compose project proofline-live-chat-20261117 on loopback 8007 with
its own PostgreSQL volume. Preserve main proofline, previous acceptance instances,
all historical results and decisions. Read the protected backend key only to
configure this Groq runtime, never write it to source, artifacts or command args.
Disable Live and remove the runtime key after the trial; keep the new DB/history.

Reserve each attempt in ledger.json before its only POST /cases/{id}/runs with
the saved unique idempotency key and expected case version. Save case-before,
RunView including snapshot/raw/profile/usage, and ProposalView when present.
Only synthetic snapshots are sent; no reference answers, evaluator or reviews
are included in provider input. No operator decisions, customer messages or
follow-up actions are executed.

## Review criteria fixed before generation

Assess technical shape, literal fragments, semantic sufficiency of each field's
own evidence, chronology completeness/roles, readiness and follow-up separately.
Technical success alone is not acceptance. A semantic blocker is recorded as
quality failure; a failed run/provider/schema failure stops further calls.

- V02: cancellation_status=confirmed; withdrawal_timing=unconfirmed. The form
  claims an earlier email; its receipt alone does not prove receipt of that
  withdrawal. Include supplier cancellation 2026-09-18T10:20:00+03:00, form sent
  12:09:55 and received 12:10:00 with the same date/offset. Request original
  customer message and delivery receipt. Do not invent an earlier withdrawal.
- V04: alternative_acceptance=conditional; offer 2026-10-08T12:10:00+03:00,
  customer reply sent 12:25:00 and received 12:27:00. No unconditional
  alternative_accepted event. Request quiet_room AND courtyard_windows; do not
  reread already included text.

Review is an assistant assessment of synthetic development cases, not independent
human labels, held-out evaluation or an estimate of production reliability.
