# 07: Query builder FK-based joins

**What to build:** when two allow-listed tables are connected by a declared foreign key, the builder offers a second table + join column so a user can pull "order total next to customer name" visually. Join paths come from declared FK relationships only (never inferred), generated SQL still passes the guardrail, and PII masking applies to every selected column.

**Blocked by:** 06 (GROUP BY / aggregates) — both rework the same SQL-assembly contract; sequencing keeps those diffs clean.

**Status:** ready-for-agent

- [ ] A two-table join via a declared FK executes with correct joined rows
- [ ] Columns from both sides validate against their own table's schema
- [ ] Tables with no declared FK relationship cannot be joined (clear error)
- [ ] Guardrail + PII invariant tests extended to the join path
