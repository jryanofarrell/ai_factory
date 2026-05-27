# Skill: /ai-files

## Purpose

Ensure a target repository has the canonical AI-readable file layout so both Claude Code and Codex CLI sessions can work in it productively. Audits the repo, proposes any missing files, and creates them with content drawn from the actual codebase — never with TODO stubs.

## When to invoke

- **First action when starting work in any target repo that lacks `CLAUDE.md`, `AGENTS.md`, or `.ai/`.** The factory's orchestrator should run this before processing the first ticket against an unseen repo.
- Manually via `/ai-files [<repo-key>]` to re-audit and refresh after large changes (renames, new modules, framework upgrades).

## Canonical layout

Every target repo should have:

```
<repo-root>/
  CLAUDE.md              ← Claude-specific pointer (auto-loaded by Claude Code)
  AGENTS.md              ← Codex/agent pointer (auto-loaded by Codex CLI)
  .ai/
    rules/
      core.md            ← always-applicable constraints; read before writing any code
    context/
      <area>.md          ← codebase snapshot per major area (backend, frontend, db, …)
                          loaded on demand by the relevant task
    skills/
      ai-structure.md    ← map of the .ai/ system itself
      <area>/<task>.md   ← procedural how-to guides; added incrementally as patterns
                          repeat — NOT pre-scaffolded
```

Not created at scaffold time:
- `.claude/commands/` — added per repo only when a slash command is justified
- `.claude/memory/` — written by the factory after each run
- `.ai/skills/<area>/<task>.md` — added per ticket once a pattern has actually repeated

`CLAUDE.md` and `AGENTS.md` carry identical bodies. Both point at the same `.ai/` tree so neither provider sees a different view of the world.

## Behavior when invoked

### Step 1 — Resolve target repo

If a repo key is passed (`/ai-files <key>`), look it up in `manifest.yaml` and resolve its `local_path`. Otherwise operate on the current working directory.

`cd` into the target repo and confirm it is a git repo with a clean working tree. Refuse to scaffold into a dirty tree — the resulting commit would mix concerns.

### Step 2 — Audit existing files

Check which canonical files exist:

```bash
ls CLAUDE.md AGENTS.md 2>/dev/null
ls .ai/rules/core.md .ai/skills/ai-structure.md 2>/dev/null
ls .ai/context/*.md 2>/dev/null
```

Print a status table: file path, exists/missing, size if present.

### Step 3 — Discover major areas

Read the repo's top-level structure to decide context-file granularity:

- **Monorepo** with `apps/`, `packages/`, or workspace directories → one `.ai/context/<app>.md` per app
- **Backend + frontend at root** (e.g. `backend/` + `frontend/`) → `backend.md` + `frontend.md`
- **Single codebase** → one context file named after the repo or `core.md`

Take cues from the repo's existing README and framework files. List the proposed context files and the rationale for that granularity.

### Step 4 — Draft content for missing files

For each missing file, draft content by reading actual code in the target repo.

- **CLAUDE.md** — short pointer. Includes:
  - One-line project description
  - Key build/run commands as observed in the repo (`make`, `npm`, `uv`, `docker compose`, etc.)
  - Pointer to `.ai/rules/core.md` ("read before writing any code")
  - Per-area context links: "Backend work: read `.ai/context/<area>.md`"
  - Pointer to `.ai/skills/ai-structure.md`

- **AGENTS.md** — body mirrors CLAUDE.md verbatim. Same pointers, same prose.

- **.ai/rules/core.md** — concrete, codebase-specific constraints. Things that must not be violated. Examples (only include if they apply):
  - Language/runtime versions
  - Framework conventions ("AsyncSession only", "Drizzle migrations via generate")
  - Files or directories that must not be touched
  - Secrets/security rules
  - Testing requirements
  Avoid generic platitudes ("write clean code"). Every line must be actionable.

- **.ai/context/<area>.md** — describe what currently exists in that area. Format: short tables or bulleted lists covering major files, models, routes, components, key conventions, gotchas. Written so a fresh agent session can pick up cold.

- **.ai/skills/ai-structure.md** — a map of the `.ai/` layout (similar in spirit to the section above), tailored to what was actually created.

**Never write `# TODO` placeholders.** If a section would be empty, omit the section. If a file would be empty, do not create it.

### Step 5 — Confirm with the user

Show the proposed file list. For each missing file, show the drafted content. Then ask:

```
Create N file(s)? [y/N]
```

If the user says anything other than `y` or `yes`, stop. Print the drafted content so the user can copy or modify manually. Do not write anything.

If a canonical file already exists but looks stale (the user invoked `/ai-files` to refresh), propose a diff rather than blindly overwriting. The user accepts or rejects per-file.

### Step 6 — Create the files

Use the Write tool to create each confirmed file. Verify the resulting tree:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "AGENTS.md" -o -path "./.ai/*" \) | sort
```

Stage and commit in a single atomic commit:

```bash
git add CLAUDE.md AGENTS.md .ai/
git commit -m "Add canonical AI file layout"
```

### Step 7 — Wrap up

Print the final tree and remind the user:

> Keep `.ai/context/<area>.md` files updated per-ticket. Each ticket that materially changes an area should update that area's context file as part of its scope, so the snapshot stays fresh instead of rotting.

## Rules

- **Never write TODO stubs.** Populate from real code or omit.
- **Never overwrite a non-stale file without confirmation.** If a canonical file exists, propose a diff for review.
- **One commit per scaffold run** — keep the diff atomic.
- **Refuse to operate on a dirty working tree.** Resulting commit would mix concerns.
- **Do not scaffold `.ai/skills/<area>/<task>.md` files at bootstrap.** Those are earned per repeated pattern, not pre-created.
