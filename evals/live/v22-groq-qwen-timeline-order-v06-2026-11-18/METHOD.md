# V06 with deterministic chronology order

One controlled V06 on Groq Free with Qwen 3.8 27B medium, budget $0,
and zero retries. The earlier Console check confirmed Free and room
in the daily limit. v21 subsequently returned HTTP 200. This run waits
past that receipt's short window.

Preflight matches v21 after mapping the new source-version and citation
IDs. Model parameters, the 3072 completion cap, system instruction,
temporary schema and the two reviewed events stay the same. The citation-ID
decoder now sorts canonical events by parsed UTC time before validation.
The original response is retained in the run. The recorded metadata
identifies the normalization as utc-v1. Review uses the frozen V3 rubric.
