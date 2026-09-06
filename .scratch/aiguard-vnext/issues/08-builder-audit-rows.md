# 08: Persisted audit rows for builder runs

**What to build:** today builder runs are intentionally session-less and leave no trace; this ticket gives them a lightweight, clearly-separated audit record (who ran what SQL when, row count, verdict) so the dashboard can show builder usage without polluting agent metrics. Design choice to make: reuse the existing tool-call table with a synthetic session, or a dedicated table.

**Blocked by:** None (can start immediately).

**Status:** done (v1.6.2)

- [x] Every builder run persists an audit row including the generated SQL and verdict
- [x] Agent metrics (tool-usage counts, guardrail verdict breakdown) remain unaffected by builder runs
- [x] The decision (synthetic-session vs dedicated table) is recorded in the design doc — dedicated `app_builder_runs` table, rationale in the module docstring
- [x] Tests assert audit rows exist and that agent stats ignore them
