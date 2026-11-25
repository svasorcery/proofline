# V01 with the distinction between receipt and sending

One V01 with the original letter withheld, on Groq Free Qwen 3.8 27B with
medium reasoning, $0 and zero retries. Preflight retains the v17 input
and temporary schema after mapping the snapshot-specific citation IDs.

The v17 model requested the correct letter but used the receipt as evidence
of a sending event. This condition adds short role guidance to the v17
instruction and configures the app with the exact source-bound receipt.
The app checks independently that the receipt is present and that its time
is not used as the only evidence of sending. The raw answer and resolved
card are archived separately.
