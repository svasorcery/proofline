# V04 field diagnostics

One new V04 control uses the exact sealed v48 request, including the selected snapshot, GPT-OSS 20B low, strict JSON schema, instructions and output limit. The only application change is safe diagnostic handling of a Groq rejection. The application may record missing required names from its own schema and count undeclared keys. Raw rejected text, values and unknown key names stay out of the archive. This experiment has one provider request, budget $0 and no retries or full-series continuation. A successful response cannot reconstruct v48's rejected keys.
