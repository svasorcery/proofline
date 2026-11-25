# Groq Free stage 4 acceptance

- Run V01 through V06 once, in that order, in one new closed instance. Make at most six generation requests, with no retries and a USD 0 budget. Stop immediately on a technical error or quota guard.
- Use GPT-OSS 20B with low reasoning, strict Chat Completions and 1,000 maximum completion tokens. Keep at least 65 seconds between sends. V54 sends the same request bodies as v53 and uses the tested source checks for V02, V05 and V06.
- Grade each assembled card against the frozen rubric and application checks. Require six full matches for stage acceptance. Keep the raw provider output separately. Never record operator approval automatically.
- Use synthetic sources, the verified Free plan and a key supplied only through standard input. Preserve the default demo database, older archives and test history. Stop the new instance after collection.
