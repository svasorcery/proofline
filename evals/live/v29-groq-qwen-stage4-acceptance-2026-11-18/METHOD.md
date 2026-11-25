# Single-profile Stage 4 acceptance

The closed acceptance runs after the separate V03 passes the frozen V3
reference and application validation. It uses one new seeded PostgreSQL
database, one application instance and one profile. V01 withholds the
original reply. V02–V06 use full snapshots. V02 reproduces v26 exactly.
V03 reproduces the control request including the timeline minimum.
The other four cases reproduce the pinned profile with the new seed IDs,
scoped follow-up requirements and the shared completion settings.

The provider is Groq Free with Qwen, reasoning disabled and a 1,000-token
completion limit. One attempt is permitted per case with at least
65 seconds between sends and no retries. The run stops on a technical
failure. Model responses with citation IDs and resolved proposal bodies
are stored separately. The frozen benchmark assesses facts, evidence,
chronology, readiness and follow-up. The live workflow retains a separate
explicit operator decision gate.
