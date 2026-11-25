# Groq Free compact V04 acceptance

- Model `openai/gpt-oss-20b`, low reasoning, strict Chat Completions and 1,000 maximum completion tokens.
- First exactly one V04 control in a fresh isolated database. Run V01–V06 once in a second isolated database only if the V04 control passes application and frozen rubric checks. No retries. Stop on a technical error or insufficient quota. Keep at least 65 seconds between sends.
- Current Free plan and quota are recorded in `quota-console.json`. The budget is USD 0. Keep the provider key in DPAPI storage and inject it through stdin only.
- Keep the test profile from the preceding run. On V04 with verified linked evidence, ask the model for facts, readiness and follow-up only. Assemble the offer, response sent and receipt from the original included sources. Preserve the raw response and the reconstructed card separately. Leave full response format available when the evidence check fails.
- Preserve the application schema, replay demo, operator history, immutable rubric and prior run archives. Use local Docker images and an isolated application instance.
