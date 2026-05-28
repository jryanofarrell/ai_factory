# Skill: /ticket

## Purpose

Read the current conversation (ideally one that followed `/ideate`) and turn it into one or more structured Linear tickets. Show the proposed tickets to the user, ask for confirmation, then create them in Linear.

## Behavior when invoked

### Step 1 — Draft tickets from conversation

Read the full conversation so far. Identify every distinct unit of work discussed. For each, draft a ticket with these sections:

- **title** — short imperative, max 70 chars.
- **target_repo** — the repo key from `manifest.yaml` (e.g. `thms-platform`). If not discussed, ask.
- **Context** — what currently exists in the codebase, what this ticket changes, and why it exists now. Reference earlier tickets or system state where relevant. Give the executor enough background to make local judgment calls without relitigating settled decisions.
- **Plan** — concrete step-by-step implementation outline, file-by-file when useful. Include sample shapes (model fields, function signatures, route specs) where they sharpen intent. Not pseudocode for everything — enough that the order, the file paths, and the interfaces are clear.
- **Design decisions** — for each non-obvious choice, state the decision and the alternative that was considered and rejected, and why. This is the durable record of *why* the code looks the way it does. The executor and the human reviewer both read this.
- **Acceptance Criteria** — bulleted list, each criterion specific and testable. This is what the PR must satisfy.
- **Scope Paths** — list of glob patterns for files that may be touched (e.g. `apps/api/src/**`, `CHANGELOG.md`). Use your best judgment from the discussion; leave empty if truly unclear.
- **Depends On** (optional) — list of ticket IDs (e.g. `HEL-3`) that must merge before this ticket can start. Empty for the first ticket in a chain. Future executor behavior: refuse to start a ticket whose dependencies are not merged. Today the human still gatekeeps by promoting to "Ready For AI" in dependency order — the convention captures intent so the factory can enforce it later without rewriting tickets.
- **Notes** (optional) — walkthrough angles, gotchas, executor cautions. Things that don't fit above but the executor or reviewer should see.

Budgets are soft planning notes only. Do not include a Budget section unless the user explicitly asks for one.

If the discussion covered one cohesive piece of work, produce one ticket. If it covered multiple distinct deliverables, produce one ticket per deliverable. When tickets build on each other, declare the dependency explicitly via `Depends On`.

**The detail level matters.** `Context`, `Plan`, `Design decisions`, and `Acceptance Criteria` are not optional — every ticket needs them. A ticket that reads as "do X" without explaining why X looks the way it does forces the executor to relitigate every choice and gives the human reviewer nothing to defend in a walkthrough. Be substantive — the goal is a self-contained planning doc, not a checklist.

### Step 2 — Select relevant skills

For each ticket, scan the target repo's `.ai/skills/` directory to find skills relevant to the work:

```bash
find repos/<target_repo>/.ai/skills -name "*.md" | sort
```

Read the `name` and `description` frontmatter from each skill file. Based on the ticket's scope_paths and acceptance_criteria, select the skill files the executor will need. Include their paths (relative to the repo root) as a `## Skills` section in the ticket description.

Example: a ticket touching `apps/api/src/**` that adds a new route would select `api/route.md`, `api/schema.md`, `api/service.md`, `api/permissioning.md`, and `api/testing.md`.

**Per-file-type skill rule.** When a ticket adds or substantially modifies a file of a canonical type — model, route, service, schema, testing, seed, migrations, page, component, hook, lib, form, types (see `/ai-files` for the full list) — the matching `.ai/skills/<area>/<task>.md` must appear in **both** `## Skills` (so the executor reads it before writing) **and** `## Scope Paths` (so the executor can update the skill if the pattern evolves). If the pattern is new and no skill exists yet for that type, the ticket creates the skill as part of its own scope. Either way: every new file of a canonical type ties to its skill file.

If no skills directory exists for the target repo, skip this step.

### Step 3 — Display for review

Print each proposed ticket as the full markdown body that will be sent to Linear, prefaced by a short header so the user can scan repo, scope, and dependencies at a glance:

```
════════════════════════════════════════════════════════════════════
Ticket 1 of N
Title:       <title>
Repo:        <target_repo>
Scope:       <scope paths, one per line or comma-separated>
Depends On:  <ticket IDs or "(none)">
Skills:      <skill paths or "(none)">
════════════════════════════════════════════════════════════════════

## Context

<context paragraphs>

## Plan

<numbered steps with file paths and concrete details>

## Design decisions

<for each non-obvious choice: decision, alternative rejected, why>

## Acceptance Criteria

- <criterion 1>
- <criterion 2>

## Notes

<walkthrough angles, gotchas>
```

The body shown here must be exactly the body that will be sent to `factory create-issue` — the user is approving the final content, not a summary.

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
- **Never create tickets without confirmation.** Always show the full proposed ticket body (the exact markdown that will be sent to `create-issue`) before calling it.
- **One unit of work per ticket.** If a ticket is too big to stay coherent, split it and say why.
- **If target_repo is ambiguous**, ask before drafting. Don't guess.
- **Always include `Context`, `Plan`, `Design decisions`, and `Acceptance Criteria`.** They are not optional. If those sections would be thin because the conversation didn't establish enough, gather more context (run `/ideate` or ask the user directly) before drafting — do not pad with generic prose.
- **Declare dependencies explicitly.** If ticket B requires ticket A's changes to land first, B's `Depends On` must list A's ID. Sequential implementation does not imply dependency — only declare it when the work cannot start without the prior ticket merged.
- **Tie new files of canonical types to their skill.** Any ticket that adds or substantially modifies a file of a canonical type (model, route, service, schema, testing, seed, migrations, page, component, hook, lib, form, types) must include the matching `.ai/skills/<area>/<task>.md` in both `## Skills` and `## Scope Paths`. If no such skill exists yet, the ticket creates it. See ADR-015.

## Description format

The description passed to `create-issue` is structured Markdown. Some sections are parsed by `factory pull-tickets` (per ADR-010); the rest are human / executor-facing context that the factory does not parse but the executor reads as part of the ticket prompt:

```markdown
## Context

<short paragraphs covering what exists, what this ticket changes, and why it exists now>

## Plan

<numbered, file-by-file implementation steps with sample shapes where useful>

## Design decisions

<for each non-obvious choice: decision, alternative rejected, rationale>

## Acceptance Criteria

- <criterion 1>
- <criterion 2>

## Scope Paths

<glob pattern>
<glob pattern>

## Depends On

<ticket-id>
<ticket-id>

## Skills

.ai/skills/api/route.md
.ai/skills/api/schema.md

## Notes

<walkthrough angles, executor cautions, gotchas>

## Target Repo

<repo_key>
```

**Parsed by the factory** (per ADR-010 + the `Depends On` convention added here): `Acceptance Criteria` (required), `Scope Paths`, `Depends On`, `Skills`, `Notes`, `Target Repo`, `Budget`.

**Executor-facing context, not parsed**: `Context`, `Plan`, `Design decisions`.

Always include `Context`, `Plan`, `Design decisions`, and `Acceptance Criteria`. Include `Scope Paths` when known (usually). Include `Depends On` when a dependency exists. Include `Skills` only if the target repo has a `.ai/skills/` directory and relevant skills were identified in Step 2. Include `Notes` when there's additional context worth surfacing. Include `Budget` only if the user explicitly asked for soft sizing notes.
