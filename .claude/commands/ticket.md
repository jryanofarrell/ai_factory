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
- **Subtasks** — per-file decomposition of the work. Each subtask is one file (or a tightly grouped pair like a schema + its migration) with explicit fields (Files, Recipe, Tier, Depends on) and a short changes description. **This is the new contract** — the runner executes subtasks sequentially, one agent invocation per subtask. See "Subtask decomposition" below for the rules.
- **Depends On** (optional) — list of ticket IDs (e.g. `HEL-3`) that must merge before this ticket can start. Per ADR-019, when dependent tickets are both in the queue, the factory groups them into a chain: one shared branch, one PR, one commit per ticket. Cross-repo deps refuse to run. Cycles abort the whole run. Max chain depth 10 (ADR-022); past that the chain splits — avoid queueing past the cap, because a split tail chain branches from main before the head chain's PR merges. Empty for the first ticket in a chain.
- **Notes** (optional) — walkthrough angles, gotchas, executor cautions. Things that don't fit above but the executor or reviewer should see.

#### Subtask decomposition

Tickets are decomposed into per-file subtasks at ticket-write time. The runner executes them one at a time — design happens here, execution is mechanical.

Rules:

1. **Per-file is the floor.** Each subtask touches one file (sometimes a tightly-coupled pair like `schema.ts` + its migration). Going finer (per-function) does not work — the agent needs the file as a unit.
2. **Each subtask points at a recipe, not a file.** Recipe is canonical (`.ai/recipes/api/service.md`). Do not include exemplar file pointers — they drift and erode recipe quality. If the recipe doesn't have enough detail for an agent to follow, fix the recipe, don't paper over it with a file pointer.
3. **Missing recipe → write the recipe first.** If a subtask would need a recipe that doesn't exist, prepend a recipe-creation subtask (Files: `.ai/recipes/<area>/<task>.md`, Recipe: `.ai/recipes/recipe.md`, depends_on: none) before the consuming subtask. The recipe-creation subtask points at `.ai/recipes/recipe.md` — the meta-recipe describing how to author a recipe — so the executor follows the repo's recipe format. The recipe catalog grows organically as features are decomposed.
4. **Every subtask must be executable by a small local model.** Each subtask runs on the local executor by default — write the body assuming the executor cannot reason its way out of ambiguity. The recipe provides the pattern; the subtask body specifies what's variable for this case: exact column names, function signatures, branching algorithms in pseudocode, specific copy strings, layout grids. **If executing a subtask would require the agent to make a design decision** — choose between two algorithms, pick a layout, decide a naming convention — **the decomposition is not done.** Push the decision up into the subtask body until the agent's job is mechanical.
5. **Subtasks must follow their recipe, not redefine it.** The recipe is the source of truth for the pattern. Don't restate the recipe in the subtask body — assume the agent will read it. Use the subtask body only for what the recipe cannot know: this feature's specific columns, this function's exact signature, this route's specific handlers, this dedup algorithm's branching. If you find yourself contradicting the recipe in a subtask body, fix the recipe instead.
6. **Order matters.** Use `Depends on:` to declare ordering. The runner executes subtasks in document order but the explicit `Depends on:` is what humans and the runner both use to reason about correctness.
7. **Tests run once, at the end.** Subtasks intentionally leave the codebase in a partially-broken state. Do not write subtasks whose AC includes "tests pass" — that's the ticket-level AC.
8. **Don't decompose past meaningful work.** A subtask should accomplish something a reasonable engineer would call a unit of work. "Add the import statement" is not a subtask.

Subtask markdown format (each block is one subtask):

```
### 1. <short imperative title>
- Files: apps/api/src/myVendor/schema.ts
- Recipe: .ai/recipes/api/schema.md
- Depends on: (none)

<changes paragraph or code block: exactly what changes in those files.
Reference the recipe for the pattern, but state the specifics here —
column names, function signatures, branching algorithms in pseudocode,
exact JSX layout — that the agent can't derive from the recipe alone.
Aim for a body the local model can execute mechanically. If you'd be
making the decision while reading it, the agent will too.>
```

Fields in detail:

- **Files** — comma-separated relative paths. Usually one. The subtask agent is told it may only touch these files.
- **Recipe** — path to the recipe the subtask follows. Required.
- **Depends on** — comma-separated subtask ids (e.g. `1, 2`) or `(none)`.

If the discussion concluded that the ticket is small enough to be done in one shot (e.g. a typo fix in a single file), it is OK to omit Subtasks — the runner falls back to the legacy single-shot execution path. But for any ticket that touches more than one file or has more than a few ACs, write subtasks.

Budgets are soft planning notes only. Do not include a Budget section unless the user explicitly asks for one.

If the discussion covered one cohesive piece of work, produce one ticket. If it covered multiple distinct deliverables, produce one ticket per deliverable. When tickets build on each other, declare the dependency explicitly via `Depends On`.

**The detail level matters.** `Context`, `Plan`, `Design decisions`, and `Acceptance Criteria` are not optional — every ticket needs them. A ticket that reads as "do X" without explaining why X looks the way it does forces the executor to relitigate every choice and gives the human reviewer nothing to defend in a walkthrough. Be substantive — the goal is a self-contained planning doc, not a checklist.

### Step 2 — Select relevant recipes

For each ticket, scan the target repo's `.ai/recipes/` directory to find recipes relevant to the work:

```bash
find repos/<target_repo>/.ai/recipes -name "*.md" | sort
```

Read the `name` and `description` frontmatter from each recipe file. Based on the ticket's scope_paths and acceptance_criteria, select the recipe files the executor will need. Include their paths (relative to the repo root) as a `## Recipes` section in the ticket description.

Example: a ticket touching `apps/api/src/routers/**` that adds a new router would select `api/router.md`, `api/schema.md`, `api/service.md`, `api/permissioning.md`, and `api/testing.md`. (Recipe names follow the codebase's own vocabulary — `routers/` becomes `router.md`, not `endpoint.md`. See the naming rule in `/ai-files`.)

**Per-file-type recipe rule.** When a ticket adds or substantially modifies a file that matches a recurring file-type pattern in the repo — anything for which `.ai/recipes/<area>/<task>.md` already exists, or which the naming rule in `/ai-files` says should have one — the matching recipe path must appear in **both** `## Recipes` (so the executor reads it before writing) **and** `## Scope Paths` (so the executor can update the recipe if the pattern evolves). If no recipe exists yet for that type, the ticket creates it as part of its own scope, named per the rule. Either way: every new file of a recurring type ties to its recipe file.

If no recipes directory exists for the target repo, skip this step.

### Step 3 — Display for review

Print each proposed ticket as the full markdown body that will be sent to Linear, prefaced by a short header so the user can scan repo, scope, and dependencies at a glance:

```
════════════════════════════════════════════════════════════════════
Ticket 1 of N
Title:       <title>
Repo:        <target_repo>
Scope:       <scope paths, one per line or comma-separated>
Depends On:  <ticket IDs or "(none)">
Recipes:      <recipe paths or "(none)">
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
- **Tie new files of recurring types to their recipe.** Any ticket that adds or substantially modifies a file matching a recurring file-type pattern in the repo must include the matching `.ai/recipes/<area>/<task>.md` in both `## Recipes` and `## Scope Paths`. The recipe is named per the rule in `/ai-files` (directory-driven, codebase vocabulary). If no such recipe exists yet, the ticket creates it. See ADR-015 and ADR-016.

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

## Subtasks

### 1. <short imperative>
- Files: <path>
- Recipe: <.ai/recipes/...>
- Depends on: <(none)|ids>

<changes — concrete enough that a small local model can execute mechanically>

### 2. <short imperative>
...

## Depends On

<ticket-id>
<ticket-id>

## Recipes

.ai/recipes/api/route.md
.ai/recipes/api/schema.md

## Notes

<walkthrough angles, executor cautions, gotchas>

## Target Repo

<repo_key>
```

**Parsed by the factory** (per ADR-010 + the `Depends On` convention added here): `Acceptance Criteria` (required), `Scope Paths`, `Subtasks`, `Depends On`, `Recipes`, `Notes`, `Target Repo`, `Budget`.

**Executor-facing context, not parsed**: `Context`, `Plan`, `Design decisions`.

Always include `Context`, `Plan`, `Design decisions`, and `Acceptance Criteria`. Include `Scope Paths` when known (usually). Include `Depends On` when a dependency exists. Include `Recipes` only if the target repo has a `.ai/recipes/` directory and relevant recipes were identified in Step 2. Include `Notes` when there's additional context worth surfacing. Include `Budget` only if the user explicitly asked for soft sizing notes.
