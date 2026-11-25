# V05 and V06 with reviewed chronology enabled

Both v19 cases had correct and supported facts and an empty timeline.
This condition uses the existing timeline example from v15 in the same
citation ID profile. Two exact event marks per case are bound to the
selected source versions and used in application validation. The marks
are not part of the messages sent to the model.

One Groq Free request for each case, Qwen 3.8 27B medium, $0, no retries,
and at least 65 seconds between sends. This run sets the completion cap
to 3072 to leave room in the token window. Preflight checks business data
and the temporary schema against v19 after mapping the new citation IDs.
The offline review uses the same frozen v3 checks as v19.
