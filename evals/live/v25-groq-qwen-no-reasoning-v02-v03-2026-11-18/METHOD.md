# Groq Free without reasoning

Two synthetic full snapshots went through one isolated Proofline instance.
V02 and V03 used the versioned stage 4 profile, a fresh seeded database,
the source-bound evidence available for each case and strict Chat Completions.

The offline preflight compared the complete requests with v24 after
normalizing newly generated source-version and citation IDs. The only
request settings that changed were `reasoning_effort=none` and
`max_completion_tokens=1000`. The guard checked the Free plan, the known
output-token limit, the current case version and the exact outgoing JSON
before each send. It reserved one send per case, with zero retries and at
least 65 seconds between requests.

The ordinary application validators produced and retained each proposal.
The separate frozen V3 reference checked facts, evidence coverage, chronology,
follow-up and readiness. Its assessment detects quality failures that do not
necessarily create a runtime blocker. This trial covers V02 and V03.
