# 06: Query builder GROUP BY / aggregate columns

**What to build:** the visual builder gains an optional "aggregate" mode: pick one or more aggregate expressions (COUNT, SUM/AVG/MIN/MAX over numeric columns) plus optional grouping columns, and the resulting SELECT runs through the exact same validate → guardrail → PII-mask pipeline as today. A user can now answer "average order total per status" visually without hand-writing SQL.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Aggregate + group-by SELECTs execute through the builder with correct results
- [ ] Invalid combos (aggregate over non-numeric column, unknown group column) fail with operator-friendly 400s
- [ ] The always-guardrail invariant holds for every generated statement (existing invariant test extended)
- [ ] New tests mirror the existing builder suite's coverage style
