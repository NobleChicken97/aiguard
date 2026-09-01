# 03: Redis service in CI to exercise the distributed-memory path

**What to build:** the CI job gains a Redis service container and a run mode where the short-term memory sync path is actually exercised (a dedicated test suite that only runs when Redis is reachable, mirroring how the PostgreSQL-gated tests are gated). Today the Redis sync code has never run under test; this ticket makes its fallback and sync behavior verified rather than assumed.

**Blocked by:** 02 (publish repo to GitHub and activate CI) — CI must exist before a service can be added to it.

**Status:** ready-for-agent

- [ ] CI includes a Redis service and a gated test suite that runs against it
- [ ] Gated tests cover: messages sync to Redis when reachable, state restores on resume, and the graceful fallback when Redis disappears mid-session
- [ ] The suite skips cleanly (like the PG tests) when Redis is unavailable
