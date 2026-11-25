# Qwen medium with case-specific choices, V01 only

Author approved one request on Groq Free with a $0 budget, no retries or fallback.
Use a fresh loopback Compose project on 8012 and its own database. Preserve prior
archives, databases and containers. Disable Live and remove the runtime key after
the trial. Free tier is confirmed by the author. Billing was not audited.

Seed the synthetic cases, then exclude the original customer reply from V01.
Confirm exact business input against v3-ablation-V01. Only the known reply is
eligible for lookup, with metadata but no text or version binding in the request.
Use Qwen 3.8 27B, medium, Chat Completions, strict assembly-choices-v1, 4096
completion tokens, 90 seconds and SDK retries zero. Save the actual planned
request of this snapshot and match its hash against the completed run.
Reserve the only attempt before the POST. Reconcile uncertain outcomes with GET
only. No other generation or operator decisions are authorised.

Success criteria fixed before sending. Groq accepts this schema and returns a
response that passes the isolated profile decoder. The model chooses read_source
for locator 1618ae5e5b894145 and requests original_acceptance_reply. The requested
fact remains alternative_acceptance=unconfirmed. Compare the stored raw answer
and the converted assembly-output-v1 card. Review chronology and evidence locally.
This is a compatibility and selection check on one development case.
