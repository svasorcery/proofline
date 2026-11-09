# Synthetic cases and recorded replay data

[travel-cases.json](cases/travel-cases.json) contains six synthetic cases with source texts, a source catalogue, requested facts and monetary context. It is an unchanged copy of the v3 dataset. No real customer data is included.

[replay/v3/catalog.json](replay/v3/catalog.json) lists six full-context recordings and three source-removal conditions. Each entry identifies the case, removed source if any, exact prompt, raw response, canonical output and recorded execution metadata. Paths in the catalogue are relative to the repository root. Prompt and output hashes are explicit.

The exact model request payload is the final JSON line of each `prompt.txt`. The dataset plus the recorded removal identifies the source selection; monetary context remains in the case dataset and was not sent for model calculation. The verifier checks that every recorded payload matches its case, source selection and measured policy.

These are research fixtures for the future seed/replay implementation, not an implemented application provider. The catalogue does not yet implement the application `replay_compat_digest`, database IDs, source version bindings or API. Those belong to [the specification](../docs/spec.md).

Keep recorded outputs unchanged, including V02 and V04. Current checks may add a separately labelled assessment; they must not silently repair the response or imply a new model call. The [baseline manifest](../evals/baselines/v3/manifest.json) covers every recorded input/output file.
