# Skills & MCP Reference — Unified Toolset, All 4 Surfaces
Compiled Aug 20, 2026. Run `process.md` first — this file describes the *target state*
after that rollout, not what's live today until you've done the copy steps.

---

## 0. How to read this file

✅ = tool is present and working on that surface. Blank = not applicable / not being
mirrored there (with a reason given).

**Mechanism reminder:** Antigravity, Copilot, and OpenCode read skills the same way — a
folder with `SKILL.md` frontmatter (`name:` + `description:`), auto-triggered when your
prompt matches the description. Continue has no such mechanism; where the table shows
Continue with a note instead of ✅, that means the *effect* is approximated via
`~/.continue/rules/design-and-review.md`, not the literal skill running.

MCP tools are a real shared protocol — a ✅ for Continue on an MCP row means it's actually
the same tool, just callable only in **Agent mode**, not plain chat.

---

## 1. MCP Tools (canonical set — same across all 4)

| Tool | Copilot | Continue | Antigravity | OpenCode | Use when |
|---|:---:|:---:|:---:|:---:|---|
| **context7** | ✅ | ✅ (agent mode) | ✅ | ✅ | Any question about a library's current API — pulls live docs instead of relying on training-data memory |
| **serena** | ✅ | ✅ (agent mode) | ✅ | ✅ | The actual file/symbol editing backbone — reads, edits, renames symbols, runs shell commands. This is what makes an agent's code edits precise instead of blind text replacement |
| **motion** | ✅ | ✅ (agent mode) | ✅ | ✅ | Animation-library questions/best practices. Free tier only — don't expect `motion-plus`-gated features (performance-audit, transition-preview) to work anywhere |

**StitchMCP** — Antigravity only, intentionally not mirrored. It's Google's own
screen-generation tool; not confirmed to work as a portable endpoint outside Antigravity.
Use it there for full frontend/screen generation — Antigravity's actual strength.

---

## 2. Design / UI / Frontend Skills (canonical set — Antigravity + Copilot + OpenCode)

| Skill | Copilot | Continue | Antigravity | OpenCode | Use when | How |
|---|:---:|:---:|:---:|:---:|---|---|
| **impeccable** | ✅ | rules file (partial) | ✅ | ✅ | Any UI polish task — 22 sub-commands (craft, critique, polish, audit, harden, delight, etc.) | Auto-triggers on general UI requests; name a specific sub-command explicitly (e.g. "run impeccable's audit") for that one |
| **design-taste-frontend v2** | ✅ | rules file (partial) | ✅ | ✅ | Building a new frontend from scratch, want a real design system rather than generic-looking output | Auto-triggers on "build me a [UI thing]" |
| **watermelon-ui** | ✅ | — | ✅ | ✅ | Pulling a component from the watermelon.sh registry instead of hand-writing it | Auto-triggers on requests for common components |
| **animejs** | ✅ | rules file (partial) | ✅ | ✅ | Adding a specific micro-interaction using Anime.js v4.5.0 | Auto-triggers, or name it explicitly |
| **react-spring** | ✅ | rules file (partial) | ✅ | ✅ | Spring-physics motion — pick over animejs when it should feel physically responsive rather than eased/timed | Auto-triggers, or name it explicitly |
| **motion** (skill, distinct from MCP tool above) | ✅ | rules file (partial) | ✅ | ✅ | General animation best-practices guidance, library-agnostic | Auto-triggers on animation requests |

**Deliberately not mirrored (redundant with the above, left where they already are, not
deleted):** `design-taste-frontend-v1`, `gpt-taste`, `huashu-design`,
`stitch-design-taste`, `ui-ux-pro-max` — all Antigravity-only. If after a few weeks on the
new setup you never reach for these, that's your own signal to prune them later.

---

## 3. Research, Spec-Writing & Review Skills + Codebase Analysis (canonical set)

| Skill | Copilot | Continue | Antigravity | OpenCode | Use when | How |
|:---|:---:|:---:|:---:|:---:|---|---|
| **graphify** | ✅ | — | ✅ | ✅ | Understanding a codebase before architecting, refactoring, or debugging — maps code, docs, PDFs, and images into a queryable knowledge graph with community detection and an audit trail. | Type `/graphify .` in the assistant (or `graphify .` in PowerShell). Install: `graphify <platform> install` (global) or `graphify install --project <platform>` (per-repo). |
| **grill-me** | ✅ | rules file (partial) | ✅ | ✅ | Stress-testing a design/architecture decision before committing | Auto-triggers on "should I do X or Y" style prompts, or name it |
| **to-spec** | ✅ | — | ✅ | ✅ | Converting a rough idea into a written spec | Name it explicitly — it's a conversion tool, not something that should auto-fire |
| **to-tickets** | ✅ | — | ✅ | ✅ | Converting a spec into individual dev tickets | Name it explicitly |
| **shadcn** | ✅ | — | ✅ | ✅ | Pulling from the shadcn/ui component registry | Auto-triggers on common component requests |

Note: your original task-assignment plan routes "ideation/scoping" and
"architecture/DB schema" to **Claude free web chat**, which reads none of these local
skill folders — these 3 apply when you're doing that same kind of work *inside* one of
your 4 active coding surfaces instead.

---

## 4. GSD Workflow System (Antigravity + Copilot only — not extended in this pass)

| Item | Copilot | Continue | Antigravity | OpenCode | Use when |
|---|:---:|:---:|:---:|:---:|---|
| GSD skills (82) + agents (33) + hooks (11) | ✅ | | ✅ | | Structured, multi-phase project needing enforced state management / context hygiene |

**Not extended to OpenCode or Continue in this pass** — it's a full workflow-management
layer (not a single-purpose skill), and it's 4 months stale. Update it first (check for a
`gsd-update` command or the project's current docs) before deciding whether it's worth
spreading further.

---

## 5. Activating something new into the canonical set

1. Pick the skill from `~/.agents/skills/` (or wherever it currently lives).
2. Copy it into **all 3** skill-reading surfaces — Antigravity, Copilot, OpenCode — not
   just one:
   ```powershell
   $name = "skill-folder-name"
   Copy-Item "$env:USERPROFILE\.agents\skills\$name" "$env:USERPROFILE\.gemini\config\skills\$name" -Recurse
   Copy-Item "$env:USERPROFILE\.agents\skills\$name" "$env:USERPROFILE\.copilot\skills\$name" -Recurse
   Copy-Item "$env:USERPROFILE\.agents\skills\$name" "$env:USERPROFILE\.opencode\skills\$name" -Recurse
   ```
3. If it's something Continue's rules file should also reflect, add a short bullet to
   `~/.continue/rules/design-and-review.md` (or a new topic-specific rules file) condensing
   the skill's core instruction into plain guidance.
4. If it's an MCP-capable tool rather than a skill, add it to all 4 configs per the
   patterns in `process.md` Section 3.
5. Restart the relevant surface(s).
6. **Add a row to the correct table above, immediately** — use this template:
   ```
   | **<name>** | <✅/blank per surface> | <use-when> | <auto/explicit> |
   ```
   Batching this "for later" is exactly how the setup ended up with an untracked
   308-skill registry the first time — don't repeat that.

---

## 6. Quick decision guide — which surface for which task

| Task | Surface | Backing tools |
|---|---|---|
| Ideation, scoping, architecture | Claude free web chat (outside all 4 surfaces, no local skills) | — |
| Frontend / UI / components | **Antigravity** | Built-in agent + StitchMCP + `impeccable`/`design-taste-frontend v2` |
| Backend logic, hard bugs | **Copilot** or **Continue** (Agent mode) | OpenRouter → DeepSeek V4 Pro; `serena` + `context7` |
| CRUD boilerplate, scaffolds | **Terminal → OpenCode** | Zen (free DeepSeek V4 Flash) |
| Docs / README / commits | **Terminal → OpenCode** | Zen (free) |
| Pre-submission review / polish | **Copilot** or **Continue** | OpenRouter → Claude Sonnet 5; `impeccable` audit sub-command |
| 2nd opinion / budget-tight backup | Any of the 4 | OpenRouter → GLM-5.2:free |
