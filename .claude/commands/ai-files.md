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
      <area>/<task>.md   ← procedural how-to guides; one per canonical file type
                          present in the codebase, populated from real code
```

### Canonical file types

For each `<area>` discovered in Step 3, scaffold a `.ai/skills/<area>/<task>.md` for every type below that has at least one matching file in the codebase. Skip the rest — never create a skill describing a pattern the repo doesn't have.

**Backend (`api/` or whatever the area key is — e.g. `backend`):**
- `model.md` — ORM table definitions (SQLAlchemy `Mapped`/`mapped_column`, Drizzle table specs, ActiveRecord classes, etc.). Triggered by files in `models.py` / `models/`, `db/schema.ts`, `app/models/*.rb`.
- `route.md` — HTTP route handlers (FastAPI `APIRouter`, Express handlers, Flask blueprints). Triggered by `routers/`, `routes/`, route decorators.
- `service.md` — service layer / business logic modules. Triggered by `services/`.
- `schema.md` — request/response validation schemas (Pydantic, Zod). Triggered by `schemas.py`, `schemas/`, Zod schema files.
- `testing.md` — test layout and conventions. Triggered by `tests/`, `*_test.py`, `*.test.ts`.
- `seed.md` — seed/fixture code that bootstraps DB state. Triggered by `seed.py`, `seeds/`.
- `migrations.md` — DB migration workflow. Triggered by `alembic/`, `migrations/`, `db/migrations/`.

**Frontend (`web/` or whatever the area key is):**
- `page.md` — top-level page components. Triggered by `app/**/page.tsx`, `pages/**/*.tsx`.
- `component.md` — reusable React components. Triggered by `components/`.
- `hook.md` — custom React hooks. Triggered by `hooks/` or `use*.{ts,tsx}` outside `components/`.
- `lib.md` — API client / shared utilities. Triggered by `lib/`.
- `form.md` — form handling pattern. Triggered by `*Form.tsx` or forms-specific modules.
- `types.md` — shared TypeScript types. Triggered by `types/`.

For shapes the codebase uses that aren't in this list, invent a sensible `<task>` name and skill — the list is the starting set, not the limit. Conversely, if you'd be writing TODOs, skip the file.

Not created at scaffold time:
- `.claude/commands/` — added per repo only when a slash command is justified
- `.claude/memory/` — written by the factory after each run

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

### Step 4 — Detect file types per area

For each area, glob the codebase for the canonical file types listed earlier. Print a per-area table of which types are present (so the user can see what skill files will be created) and which are absent (so they know what's deliberately being skipped).

Example output for the take-home starter:

```
backend/
  ✓ model       (backend/models.py — User)
  ✓ route       (backend/main.py — GET /users/me)
  ✗ service     (no services/ directory)
  ✗ schema      (no Pydantic models)
  ✗ testing     (no tests/ directory)
  ✓ seed        (backend/seed.py — seed_user_if_needed)
  ✗ migrations  (no alembic/)

frontend/
  ✓ page        (app/page.tsx — server component)
  ✗ component   (no components/ directory)
  ✗ hook        (no hooks/)
  ✗ lib         (no lib/)
  ✗ form        (no form modules)
  ✗ types       (no types/)
```

Only present file types get skills. Absent ones get skipped — they earn a skill the first time a ticket adds a matching file (see the `/ticket` rule on per-file-type skill updates).

### Step 5 — Draft content for missing files

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

- **.ai/skills/ai-structure.md** — a map of the `.ai/` layout (similar in spirit to the section above), tailored to what was actually created — including the file-type skills present.

- **.ai/skills/<area>/<task>.md** (per file type detected in Step 4) — procedural how-to guide for writing/modifying files of that type, **drawn from the patterns actually in use in the codebase**. Each skill should answer:
  - Where these files live (path conventions).
  - What shape they take (imports, base classes, decorators, structural rules).
  - What conventions are non-obvious (naming, fields that must always be present, ordering rules).
  - One worked reference to a file in the repo so the reader can see the pattern in context.

  Example shape for `.ai/skills/api/model.md` against this take-home:

  ```markdown
  ---
  name: model
  description: How to add or modify a SQLAlchemy ORM model in backend/models.py.
  ---

  # SQLAlchemy models

  Models live in `backend/models.py`. All inherit from `Base` (a `DeclarativeBase`).

  ## Conventions
  - Table names are singular (`"user"`, `"thread"`, `"message"`).
  - Columns use `Mapped[T]` + `mapped_column(...)`.
  - Foreign keys: `mapped_column(ForeignKey("<table>.id"))`.
  - Constrained string columns use a table-level `CheckConstraint` via `__table_args__`.
  - Timestamps come from `Base` (`created_at`, `updated_at` with `server_default=func.now()`).

  ## Reference
  - `User` (line ~10) — minimal model, plain columns.
  - `Thread` (line ~22) — adds FK + title.
  - `Message` (line ~32) — adds FK, CHECK constraint, composite index.
  ```

  Repeat the pattern for every detected file type. Each one is short (30-80 lines). Total scaffold for a typical repo: 5-12 small skill files. Empty skills are worse than no skill — if you cannot describe a real pattern, skip the file.

**Never write `# TODO` placeholders.** If a section would be empty, omit the section. If a file would be empty, do not create it.

### Step 6 — Confirm with the user

Show the proposed file list. For each missing file, show the drafted content. Then ask:

```
Create N file(s)? [y/N]
```

If the user says anything other than `y` or `yes`, stop. Print the drafted content so the user can copy or modify manually. Do not write anything.

If a canonical file already exists but looks stale (the user invoked `/ai-files` to refresh), propose a diff rather than blindly overwriting. The user accepts or rejects per-file.

### Step 7 — Create the files

Use the Write tool to create each confirmed file. Verify the resulting tree:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "AGENTS.md" -o -path "./.ai/*" \) | sort
```

Stage and commit in a single atomic commit:

```bash
git add CLAUDE.md AGENTS.md .ai/
git commit -m "Add canonical AI file layout"
```

### Step 8 — Wrap up

Print the final tree and remind the user:

> Keep `.ai/context/<area>.md` and `.ai/skills/<area>/<task>.md` files updated per-ticket. Each ticket that materially changes an area should update the area's context file. Each ticket that adds or changes a file of a canonical type should follow the type's skill — or update the skill if the pattern is new (see the `/ticket` skill's per-file-type rule).

## Rules

- **Never write TODO stubs.** Populate from real code or omit. This applies to per-file-type skills too: if a type has only one trivial file and no pattern worth describing, skip the skill — don't write a stub that just says "add models here."
- **Never overwrite a non-stale file without confirmation.** If a canonical file exists, propose a diff for review.
- **One commit per scaffold run** — keep the diff atomic.
- **Refuse to operate on a dirty working tree.** Resulting commit would mix concerns.
- **Scaffold a per-file-type skill only when at least one matching file exists.** No empty stubs. New types earn their skill the first time a ticket adds a matching file.
