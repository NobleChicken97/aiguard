# 05: Memory fact management surface

**What to build:** from the memory inspector, a signed-in operator can delete a wrong or stale long-term fact (the deletion already exists at the data layer; it just has no API or UI). The inspector lists facts with a delete control per fact, deletion goes through a JSON endpoint, and the list refreshes. Deleting is confirmed inline since facts feed future prompts.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A fact can be deleted from the inspector and disappears from future sessions' context
- [ ] Deleting a nonexistent/already-deleted fact returns 404, not a silent success
- [ ] Webapp tests cover the endpoint's happy path and 404 path
