# Groq Free V06 control and stage 4 acceptance

- Run exactly one V06 control in its own database. Run V01–V06 once in a second isolated database only after the V06 card passes the frozen rubric and application checks.
- Use GPT-OSS 20B with low reasoning, strict Chat Completions and a 1,000 completion-token cap. Use the same request bodies as v51. Keep at least 65 seconds between sends. Stop after a technical or quota error. No retries.
- Use the current Groq Free plan with a USD 0 budget and verified quota. Send synthetic data only and inject the saved key through standard input.
- The V06 post-processing rule requires the reviewed snapshot and matching model facts, citations and events. It attaches the explicit no-charge record and removes a redundant sole bank-document request. Keep the raw answer separately from the assembled card.
- Preserve earlier run artifacts and the existing demo and history. Keep the two new trial databases after stopping their processes.
