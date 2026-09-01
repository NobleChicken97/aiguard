# 09: Session lifecycle semantics (active vs ended)

**What to build:** a conversation currently marks its session "ended" after every message, which makes the dashboard's "active sessions" stat read ~zero and resume silently reopen an ended row. Pick and implement one coherent model — e.g. sessions stay active until an idle timeout, with resume records reopening ended sessions explicitly — and make the dashboard stat meaningful again. Includes an ADR-style note in the design doc for the chosen model.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The dashboard's active-session count reflects reality during and shortly after a conversation
- [ ] Resuming a session never leaves contradictory status states
- [ ] Chosen lifecycle model documented alongside the existing design trade-offs
- [ ] Tests cover the new status transitions
