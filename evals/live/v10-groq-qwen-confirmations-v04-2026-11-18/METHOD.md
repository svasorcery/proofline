# V04 on the Groq confirmations test profile

The author approved one synthetic V04 request on the Groq Free account with
Qwen 3.8 27B and medium reasoning. The budget was $0. SDK retries, HTTP
transport retries, redirects, tools and fallback were disabled.

The app used the new Compose profile and a fresh database in a dedicated
project. A one-shot application process invoked the normal runs route via
FastAPI TestClient. The saved Groq credential was passed on stdin to that
process. The application container had no published port and was removed on
exit. The database was stopped and kept for inspection.

Preflight matched the current source version and exact text of the attached
reply. After accounting for database source-version IDs, the only change
from the previous V04 request was the removal of
`original_acceptance_reply` from the strict schema's requirement enum.
The guard recorded a single reservation before the POST and matched the SDK
body against the preflight. Quota headers came from that same request.

The raw model output is in the run record and the application card is
recorded separately. Review checks for conditional acceptance, all three
events with literal evidence, and only the quiet-room and courtyard-window
confirmations. The review uses the synthetic case rubric without independent
human labels. The run records no operator decision.
