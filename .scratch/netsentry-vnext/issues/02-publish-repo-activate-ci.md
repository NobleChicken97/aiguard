# 02: Publish repo to GitHub and activate CI

**What to build:** the repository lives on GitHub so the existing workflow actually runs on every push/PR. Once the remote exists and is pushed, open the Actions tab and confirm the full matrix is green — including the 8 PostgreSQL-gated tests that always skip locally — and record the run link in the project docs.

**Blocked by:** None (can start immediately; needs the owner to create the repo / provide the remote URL and credentials — that part is not agent-doable).

**Status:** ready-for-agent

- [ ] Remote added, history (baseline + v1.6.1 commits) pushed
- [ ] CI workflow run is green on the pushed commit
- [ ] PostgreSQL-gated tests execute (not skip) in the CI run
- [ ] README badge/links updated to the public repo
