# Skill: /ticket

## Purpose

Read the current conversation (ideally one that followed `/ideate`) and turn it into one or more structured Linear tickets. Show the proposed tickets to the user, ask for confirmation, then create them in Linear.

## Behavior when invoked

### Step 1 — Draft tickets from conversation

Read the full conversation so far. Identify every distinct unit of work discussed. For each, produce a ticket draft with:

- **title** — short imperative, max 70 chars
- **acceptance_criteria** — bulleted list, each criterion specific and testable
- **scope_paths** — list of glob patterns for files that may be touched (e.g. `apps/api/src/**`, `CHANGELOG.md`). Use your best judgment from the discussion; leave empty if truly unclear.
- **target_repo** — the repo key from `manifest.yaml` (e.g. `thms-platform`). If not discussed, ask.

Budgets are soft planning notes only. Do not include a Budget section unless the user explicitly asks for one.

If the discussion covered one cohesive piece of work, produce one ticket. If it covered multiple distinct deliverables, produce one ticket per deliverable.

### Step 2 — Select relevant skills

For each ticket, scan the target repo's `.ai/skills/` directory to find skills relevant to the work:

```bash
find repos/<target_repo>/.ai/skills -name "*.md" | sort
```

Read the `name` and `description` frontmatter from each skill file. Based on the ticket's scope_paths and acceptance_criteria, select the skill files the executor will need. Include their paths (relative to the repo root) as a `## Skills` section in the ticket description.

Example: a ticket touching `apps/api/src/**` that adds a new route would select `api/route.md`, `api/schema.md`, `api/service.md`, `api/permissioning.md`, and `api/testing.md`.

If no skills directory exists for the target repo, skip this step.

### Step 3 — Display for review

Print each proposed ticket clearly:

```
────────────────────────────────────────────────
Ticket 1 of N
Title:    <title>
Repo:     <target_repo>
Scope:    <scope_paths or "(none specified)">
Skills:   <skill paths or "(none)">

Acceptance Criteria:
  - <criterion 1>
  - <criterion 2>
────────────────────────────────────────────────
```

Then ask:
```
Create these N ticket(s) in Linear? [y/N]
```

If the user says anything other than `y` or `yes`, stop. Do not create any tickets. Tell them what to change and invite them to run `/ticket` again.

### Step 4 — Create tickets in Linear

For each confirmed ticket, run:

```bash
uv run factory create-issue \
  --title "<title>" \
  --description "<full description markdown>" \
  --repo <target_repo>
```

The `create-issue` command creates the issue in the team's **Backlog** state. It will never set the state to "Ready For AI" — that is always a manual human action.

Print each created issue's identifier and URL as it's created.

### Step 5 — Wrap up

After all tickets are created, say something like:
```
Created N ticket(s). Open them in Linear, review, and mark "Ready For AI" when you're happy. Then run `factory run` to execute.
```

## Rules

- **Never create tickets in "Ready For AI" state.** Backlog only. The human promotes.
- **Never create tickets without confirmation.** Always show the full proposed ticket before calling `create-issue`.
- **One unit of work per ticket.** If a ticket is too big to stay coherent, split it and say why.
- **If target_repo is ambiguous**, ask before drafting. Don't guess.

## Description format

The description passed to `create-issue` must contain these sections so `factory pull-tickets` can parse it:

```markdown
## Acceptance Criteria

- <criterion 1>
- <criterion 2>

## Scope Paths

<glob pattern>
<glob pattern>

## Skills

.ai/skills/api/route.md
.ai/skills/api/schema.md

## Target Repo

<repo_key>
```

Only include `## Scope Paths` if scope paths are known. Only include `## Skills` if skills were identified. Only include `## Budget` if the user explicitly asked for soft sizing notes.
