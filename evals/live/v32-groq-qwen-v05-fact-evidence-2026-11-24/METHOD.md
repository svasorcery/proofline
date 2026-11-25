# V05 with field-local proof of receipt

Preflight compares V05 against v31. The only wire change is the system
instruction suffix about unique citation IDs within an evidence list
and the three lines needed to support `withdrawal_timing`. The snapshot,
citation IDs, strict provider schema and 1,000-token output cap match.
The other five prepared requests are regression-tested against v29.

The closed control uses the same-day Free and quota verification from
v31. The quota receipt retains the observed timestamp and budgets one
request and a full minute of tokens for v31 in its conservative estimate.
The guarded transport permits one V05, with no retries. The original
response and resulting application outcome are assessed against the
frozen V3 reference. Earlier archives and test databases are outside
the new isolated database.
