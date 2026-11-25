# Exact Groq V03 control

One V03 request uses a newly seeded isolated PostgreSQL database. The
runner restores only the published V03 fixture's source-version IDs from
v26 inside that fresh database. It first checks compatible snapshot data
and then requires both the restored snapshot and complete JSON request to
match v26 exactly. Work on the seeded versions precedes any run or review.

The runner checks the current Free-plan allowances, sets a 1,000-token
completion limit and reserves one send. It restricts transmission to the
usual Groq Chat Completions endpoint. The isolated transport reports
exception class names and whether HTTP headers were received on network
failure. It omits exception messages and request headers. Simulated
connection and response-body read failures exercised those paths offline.

The frozen V3 reference and the opt-in workflow validation determine
quality. The full V01–V06 series is conditional on a complete pass.
Files and container states for earlier evidence are compared before and
after. The v26 database volume is checked by content hash.
