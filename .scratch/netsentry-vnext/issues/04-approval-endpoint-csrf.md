# 04: CSRF defense for approval endpoints

**What to build:** the Approve/Deny form posts gain a double-submit-cookie CSRF token: the approval-queue page sets a random token cookie (SameSite=Lax) and embeds the same value as a hidden field; the approve/deny endpoints reject mismatches. Full authentication stays a documented non-goal — this ticket only closes the cross-site form-post hole while keeping the demo zero-login.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Approve/deny POSTs without a valid token are rejected (4xx) and the denial is logged
- [ ] The normal queue flow (open page → decide) works unchanged in the browser
- [ ] Existing webapp tests updated to fetch the token; new tests cover the reject path
