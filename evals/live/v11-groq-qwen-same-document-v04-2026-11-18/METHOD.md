# V04 with the single-document example

The author approved one synthetic V04 request on Groq Free with Qwen 3.8 27B
and medium reasoning. The approved budget was $0. The SDK and HTTP transport
had zero retries. Redirects, tools and provider fallback were disabled.

The test used a fresh Compose database and a one-shot app process. The app
invoked the regular runs route and received the existing credential on stdin.
The previous run was mounted read-only. The app had no published port.
Docker's automatic address pool was full during setup. A separate verified
private subnet was assigned before preflight.

Preflight verified that the request matched v10 apart from a single
synthetic timeline example appended to the system message and database
source-version IDs. The strict schema, limits and business input matched.
The app checked the exact source version and the reviewed fragments of the
outgoing header, reply body and receipt record. The transport reserved one
POST and compared the serialized request to the preflight before sending.

Review uses the original answer and its snapshot. It checks that sending
cites the Date header and a quote from the reply, and that receipt cites
the inbound log entry. It also checks the exact pair of outstanding room
conditions, conditional acceptance, and completeness of the three events.
The result is an evaluation of one synthetic case without independent
human labels. The operator was not asked to make a decision.
