# 02: Publish repo to GitHub and activate CI

**What to build:** the repository lives on GitHub so the existing workflow actually runs on every push/PR. Once the remote exists and is pushed, open the Actions tab and confirm the full matrix is green — including the 8 PostgreSQL-gated tests that always skip locally — and record the run link in the project docs.

**Blocked by:** None (can start immediately; needs the owner to create the repo / provide the remote URL and credentials — that part is not agent-doable).

**Status:** done (all required items; only the optional secret remains)

- [x] Remote added, history pushed (`origin` = NobleChicken97/agentic_guardrails, `main` in sync)
- [x] CI workflow run is green on the pushed commit (all five gates + release-gate — verified in Actions, incl. the 8 PostgreSQL-gated tests executing)
- [x] PostgreSQL-gated tests execute (not skip) in the CI run
- [x] README badge/links updated to the public repo
- [ ] Optional: set the `LLM_API_KEY` repo secret (free Groq key) to enable the manual live-smoke gate
