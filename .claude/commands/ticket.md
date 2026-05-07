# Skill: /ticket

## Purpose

Read the current conversation (ideally one that followed `/ideate`) and turn it into one or more structured Linear tickets. Show the proposed tickets to the user, ask for confirmation, then create them in Linear.

## Behavior when invoked

### Step 1 — Draft tickets from conversation

Read the full conversation so far. Identify every distinct unit of work discussed. For each, produce a ticket draft with:

- **title** — short imperative, max 70 chars
- **acceptance_criteria** — bulleted list, each criterion specific and testable
- **scope_paths** — list of glob patterns for files that may be touched (e.g. `apps/api/src/**`, `CHANGELOG.md`). Use your best judgment from the discussion; leave empty if truly unclear.
- **budget_tokens** — integer. Default 10000 for small tasks, 30000 for medium, 50000 for large.
- **budget_minutes** — integer. Default 5 for small, 15 for medium, 30 for large.
- **target_repo** — the repo key from `manifest.yaml` (e.g. `thms-platform`). If not discussed, ask.

If the discussion covered one cohesive piece of work, produce one ticket. If it covered multiple distinct deliverables, produce one ticket per deliverable.

### Step 2 — Display for review

Print each proposed ticket clearly:

```
────────────────────────────────────────────────
Ticket 1 of N
Title:    <title>
Repo:     <target_repo>
Scope:    <scope_paths or "(none specified)">
Budget:   <budget_tokens> tokens / <budget_minutes> min

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

### Step 3 — Create tickets in Linear

For each confirmed ticket, run:

```bash
uv run factory create-issue \
  --title "<title>" \
  --description "<full description markdown>" \
  --repo <target_repo>
```

The `create-issue` command creates the issue in the team's **Backlog** state. It will never set the state to "Ready For AI" — that is always a manual human action.

Print each created issue's identifier and URL as it's created.

### Step 4 — Wrap up

After all tickets are created, say something like:
```
Created N ticket(s). Open them in Linear, review, and mark "Ready For AI" when you're happy. Then run `factory run` to execute.
```

## Rules

- **Never create tickets in "Ready For AI" state.** Backlog only. The human promotes.
- **Never create tickets without confirmation.** Always show the full proposed ticket before calling `create-issue`.
- **One unit of work per ticket.** If a ticket is too big (budget > 50000 tokens or > 30 minutes), split it and say why.
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

## Target Repo

<repo_key>

## Budget

tokens: <N>
minutes: <N>
```

Only include `## Scope Paths` and `## Budget` if they have non-default values.
