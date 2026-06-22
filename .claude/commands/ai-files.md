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
    recipes/
      ai-structure.md    ← map of the .ai/ system itself
      <area>/<task>.md   ← procedural how-to guides; one per canonical file type
                          present in the codebase, populated from real code
```

### Canonical file types

For each `<area>` discovered in Step 3, scan the codebase for the recurring file-type patterns that area actually contains, and scaffold one `.ai/recipes/<area>/<task>.md` per detected pattern. The list of patterns is **discovery-driven** — derive it from what the repo has, not from a fixed taxonomy.

#### Naming rule

Recipes are named after **what the file IS** in the codebase's own structural vocabulary, not what the file contains.

1. **Take the directory name first.** A pluralized directory becomes the singular recipe name. `routers/` → `router.md`, `services/` → `service.md`, `models/` → `model.md`, `components/` → `component.md`, `sprites/` → `sprite.md`, `dags/` → `dag.md`.
2. **For a single-purpose file inside a generic directory**, name the recipe after the file's role, not the directory. `frontend/lib/chatApi.ts` as the only file in `lib/` → `api-client.md`, not `lib.md` (which would be too generic).
3. **Match the framework's own terminology when no directory signals it.** Drizzle calls them "tables" — use `table.md`. SQLAlchemy calls them "models" — use `model.md`. Phaser scenes — `scene.md`. The codebase's own word wins.
4. **Reject the urge to rename based on contents.** Routers contain endpoints; the file is still a router (`router.md`, not `endpoint.md`). Models contain fields; the file is still a model. The recipe describes the unit the developer thinks in.

Apply in that order. The first rule that fires wins.

#### Examples by repo shape

Each example shows the pattern set a `/ai-files` run would scaffold for that kind of repo. They are illustrative — the actual set comes from what's on disk, named per the rule above.

**Web app (FastAPI + Next.js, e.g. the take-home / `thms-platform`):**
- Backend area: `model.md` (`models.py` / `models/`), `router.md` (`routers/`), `service.md` (`services/`), `schema.md` (Pydantic), `testing.md` (`tests/`), `seed.md` (`seed.py`), `migrations.md` (`alembic/`).
- Frontend area: `page.md` (`app/**/page.tsx`), `component.md` (`components/`), `hook.md` (`hooks/`, `use*.tsx`), `api-client.md` (single-purpose file inside `lib/` — not `lib.md`), `form.md` (`*Form.tsx`), `types.md` (`types/`).

**Game (Unity / Phaser / Godot):**
- `scene.md` (`scenes/` or `Scenes/`), `sprite.md` (`assets/sprites/`), `prefab.md` (`prefabs/`), `behavior.md` or `component.md` (`scripts/behaviors/`, `components/`), `system.md` (ECS systems), `testing.md`.

**Data pipeline (Airflow / dbt / Dagster):**
- `dag.md` (`dags/`), `source.md` (`sources/` or extractor modules), `transform.md` (`transforms/`), `model.md` (dbt `models/`), `test.md` (dbt tests, `tests/`), `seed.md` (dbt `seeds/`).

**Infra (Terraform / Pulumi):**
- `module.md` (`modules/`), `stack.md` (`stacks/` or `environments/`), `policy.md` (OPA/Rego, `policies/`), `variable.md` (root variables files), `testing.md` (`tests/`, `terratest/`).

These four are not exhaustive. A CLI tool, a library, a mobile app each have their own vocabulary — scaffold per the rule and the codebase's words. Skip any type that has zero matching files: empty stubs are worse than no recipe.

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
ls .ai/rules/core.md .ai/recipes/ai-structure.md 2>/dev/null
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

For each area, glob the codebase for the canonical file types listed earlier. Print a per-area table of which types are present (so the user can see what recipe files will be created) and which are absent (so they know what's deliberately being skipped).

Example output for the take-home starter:

```
backend/
  ✓ model       (backend/models.py — User)
  ✓ router      (backend/routers/users.py — GET /users/me)
  ✗ service     (no services/ directory)
  ✗ schema      (no Pydantic models)
  ✗ testing     (no tests/ directory)
  ✓ seed        (backend/seed.py — seed_user_if_needed)
  ✗ migrations  (no alembic/)

frontend/
  ✓ page        (app/page.tsx — server component)
  ✗ component   (no components/ directory)
  ✗ hook        (no hooks/)
  ✗ api-client  (no lib/<single-purpose>.ts)
  ✗ form        (no form modules)
  ✗ types       (no types/)
```

Names follow the rule above — `routers/` → `router`, single-purpose file in `lib/` → `api-client` (not `lib`). Only present file types get recipes. Absent ones get skipped — they earn a recipe the first time a ticket adds a matching file (see the `/ticket` rule on per-file-type recipe updates).

### Step 5 — Draft content for missing files

For each missing file, draft content by reading actual code in the target repo.

- **CLAUDE.md** — short pointer. Includes:
  - One-line project description
  - Key build/run commands as observed in the repo (`make`, `npm`, `uv`, `docker compose`, etc.)
  - Pointer to `.ai/rules/core.md` ("read before writing any code")
  - Per-area context links: "Backend work: read `.ai/context/<area>.md`"
  - Pointer to `.ai/recipes/ai-structure.md`

- **AGENTS.md** — body mirrors CLAUDE.md verbatim. Same pointers, same prose.

- **.ai/rules/core.md** — concrete, codebase-specific constraints. Things that must not be violated. Examples (only include if they apply):
  - Language/runtime versions
  - Framework conventions ("AsyncSession only", "Drizzle migrations via generate")
  - Files or directories that must not be touched
  - Secrets/security rules
  - Testing requirements
  Avoid generic platitudes ("write clean code"). Every line must be actionable.

- **.ai/context/<area>.md** — describe what currently exists in that area. Format: short tables or bulleted lists covering major files, models, routes, components, key conventions, gotchas. Written so a fresh agent session can pick up cold.

- **.ai/recipes/ai-structure.md** — a map of the `.ai/` layout (similar in spirit to the section above), tailored to what was actually created — including the file-type recipes present.

- **.ai/recipes/<area>/<task>.md** (per file type detected in Step 4) — procedural how-to guide for writing/modifying files of that type, **drawn from the patterns actually in use in the codebase**. Each recipe should answer:
  - Where these files live (path conventions).
  - What shape they take (imports, base classes, decorators, structural rules).
  - What conventions are non-obvious (naming, fields that must always be present, ordering rules).
  - One worked reference to a file in the repo so the reader can see the pattern in context.

  Example shape for `.ai/recipes/api/model.md` against this take-home:

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

  Repeat the pattern for every detected file type. Each one is short (30-80 lines). Total scaffold for a typical repo: 5-12 small recipe files. Empty recipes are worse than no recipe — if you cannot describe a real pattern, skip the file.

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

> Keep `.ai/context/<area>.md` and `.ai/recipes/<area>/<task>.md` files updated per-ticket. Each ticket that materially changes an area should update the area's context file. Each ticket that adds or changes a file of a canonical type should follow the type's recipe — or update the recipe if the pattern is new (see the `/ticket` recipe's per-file-type rule).

## Rules

- **Never write TODO stubs.** Populate from real code or omit. This applies to per-file-type recipes too: if a type has only one trivial file and no pattern worth describing, skip the recipe — don't write a stub that just says "add models here."
- **Never overwrite a non-stale file without confirmation.** If a canonical file exists, propose a diff for review.
- **One commit per scaffold run** — keep the diff atomic.
- **Refuse to operate on a dirty working tree.** Resulting commit would mix concerns.
- **Scaffold a per-file-type recipe only when at least one matching file exists.** No empty stubs. New types earn their recipe the first time a ticket adds a matching file.
