# NetSentry Product Requirements

## Vision
NetSentry is a safety-first AI assistant that can answer questions and use tools in a real data environment without becoming destructive or careless. The core idea is simple: let the agent do useful work while enforcing explicit limits before anything risky reaches a database or a production workflow.

## Target users
- Students and portfolio reviewers evaluating agent safety patterns
- Developers prototyping AI tool-use systems with a real database
- Internal demos for showing how structured guardrails prevent harmful actions
- Teams exploring human-in-the-loop approval for high-impact operations

## Core user stories / requirements for a working version
1. As a user, I can ask the agent a question that requires a database lookup, and it should fetch the answer from the approved schema instead of guessing.
2. As a user, I can ask the agent to perform a safe write or query, and the system should block destructive or unsafe SQL before it runs.
3. As a user, I can approve a high-impact action when the system explicitly requires it, and the action should only execute after that decision.
4. As a user, I can inspect the session trace and understand what the agent planned, what tool it used, and how a decision was made.
5. As a developer, I can run the project locally, seed demo data, and validate guardrail behavior with automated tests.
6. As a system operator, I can monitor live session metrics and see recent guardrail verdicts in the dashboard.

## Stretch / post-MVP features
These are valuable, but not necessary for the core safety demo:
- Exportable trace reports for audit review
- More advanced table-level policy controls beyond static allow-lists
- Temporary approval escalation flows for multi-user environments
- Redis-backed session caching and distributed memory in production-like deployments
- Visual query builder: single-table, aggregates/group-by, and single declared-FK hops are shipped; multi-hop joins and joins×aggregates are an accepted limitation (see `design.md` known limitations), not stretch work
- Postgres migration tooling and production data sync workflow improvements

## Non-goals
This project deliberately does not try to be:
- A general-purpose enterprise BI platform
- A full SQL editor or database admin tool
- A multi-user production SaaS with OAuth, RBAC, and enterprise auditing
- A general autonomous agent that can safely act across arbitrary systems without explicit policy boundaries

The reason is that the project’s value is in proving safety controls and decision transparency, not in pretending to be a full database product.

## Success criteria
A version is considered successful for this project when all of the following are true:
- It can answer realistic database questions using the approved e-commerce schema.
- It blocks destructive or unauthorized SQL before execution with a clear verdict.
- It requires explicit approval for high-risk actions and records that decision.
- The app can run locally with seeded demo data and a working web UI.
- Automated tests cover the core safety logic, resilience, and database interaction flows.
- A reviewer can inspect the trace output and understand the full decision path.

If time is short, the minimum presentable version is the same: safe SQL tool use, approval gating, and a working demo with traceability.
