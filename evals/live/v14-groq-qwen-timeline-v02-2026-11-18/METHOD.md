# V02 with a source-backed timeline floor

This is one V02 comparison under the author's standing approval of
zero-cost Groq Free research. It uses Qwen 3.8 27B, medium reasoning,
the citation IDs profile and zero transport and SDK retries.

Preflight matched the business snapshot, instruction, model parameters and
public contract to v13. The temporary schema requires three timeline items
because the current versions of the V02 sources contain the three independently
reviewed marks. After accounting for source-version and citation IDs, that
minimum is the only wire change. The marks themselves are used locally.

The app independently checks event type, role and time against the marks.
The original output, resolved card, chronology and missing confirmations
are assessed separately. The experiment used a dedicated app and database,
and the comparison archives were mounted read-only.
