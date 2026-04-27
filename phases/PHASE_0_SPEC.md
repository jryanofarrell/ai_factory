# Phase 0 — Contracts and Scaffolding

## Brief for Claude Code

You are building Phase 0 of `ai_factory`, a personal AI factory that turns Linear tickets into GitHub PRs on the user's local machine. The owner is `jryanofarrell`; the first target repo will be `toms-hms/thms-platform` (handled in Phase 1, not this phase).

The factory runs entirely locally and uses Claude Sonnet for both ideation and execution. There is no remote runner, no model-routing logic, and no auto-merge.

**Phase 0 produces no executable behavior.** Its deliverables are (a) a minimal CLI scaffold that prints help, (b) a set of design documents that downstream phases will reference, and (c) the memory, skills, and rules scaffolding that every future agent session will read on entry. The docs and memory are load-bearing: every later phase will be briefed by handing you these files plus the next phase spec.

Your job in this phase:

1. Set up the repo structure described below.
2. Write the design docs with real content (not stubs).
3. Stop. Do not implement any Phase 1+ functionality even if it seems easy.

If you finish with time left, the only acceptable next action is to refine the docs based on inconsistencies you noticed while writing them.

## Stack decisions (already made — do not relitigate)

- Python 3.12+
- `uv` for environment and dependency management
- Typer for the CLI
- `ruff` for lint and format
- `pytest` for tests
- `pyyaml` for the manifest

The repo gets `pyproject.toml`, `.python-version` (`3.12`), `.gitignore` (standard Python plus `.env`, `manifest.yaml`, `.factory/`), and a `uv.lock` once you `uv sync`.

## Repo structure to create

```
ai_factory/
├── CLAUDE.md                    # Project memory — auto-loaded by Claude Code
├── README.md
├── pyproject.toml
├── .python-version
├── .gitignore
├── manifest.example.yaml
├── .claude/
│   └── skills/
│       └── README.md            # Index of factory skills (none implemented yet)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PHASES.md
│   ├── DECISIONS.md             # Architectural decisions and their rationale
│   ├── LINEAR_SCHEMA.md
│   ├── MANIFEST.md
│   └── TICKET_SPEC.md
├── examples/
│   └── tickets/
│       └── hello-world.md
├── src/
│   └── factory/
│       ├── __init__.py
│       └── cli.py
└── tests/
    └── __init__.py
```

## CLI scaffold

`src/factory/cli.py` should expose a Typer app with one placeholder command:

```python
import typer

app = typer.Typer(help="Personal AI factory CLI.")


@app.command()
def version() -> None:
    """Print the factory version."""
    typer.echo("ai_factory 0.0.0 (phase 0 — scaffolding)")


if __name__ == "__main__":
    app()
```

`pyproject.toml` should expose this as a console script named `factory`. After `uv sync`, `uv run factory --help` and `uv run factory version` must both work.

## Memory, skills, and rules — the convention

The factory keeps durable agent context in three places, following Claude Code conventions:

**Memory** (persistent context any agent reads on entry): `CLAUDE.md` at repo root holds working context for any agent operating on `ai_factory` itself. `docs/DECISIONS.md` holds architectural decisions and the reasoning behind them. Together these are what an agent reads first to understand what this repo is and why it's shaped the way it is. Without these, future sessions will relitigate decisions that have already been made.

**Skills** (reusable capabilities): `.claude/skills/<skill-name>/` holds factory-level skills. Skills are added in later phases — Phase 0 only creates the directory and an index README. The convention: each skill is a directory with a `SKILL.md` plus any helper scripts or templates.

**Rules** (guardrails on agent behavior): inline in `CLAUDE.md` under a "Rules" section. The factory's own development rules live here. Rules for working *inside* a target repo (like `thms-platform`) live in that repo's own `CLAUDE.md` and are loaded by the executor in Phase 1 via the working directory.

This phase creates the scaffolding for all three. Skills are deferred; only the index and one decisions doc are written here.

### CLAUDE.md (project memory)

This file is auto-loaded by Claude Code whenever an agent works inside `ai_factory`. Required sections:

- **What this is** — one paragraph. Example: "ai_factory is a personal AI factory that turns Linear tickets into GitHub PRs, running locally on the user's machine. See `docs/ARCHITECTURE.md` for the system design and `docs/DECISIONS.md` for why it's shaped the way it is."
- **Read these first** — pointer list to `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PHASES.md`, and the current phase spec under `phases/`.
- **Stack** — Python 3.12+, uv, Typer, ruff, pytest, pyyaml.
- **Conventions** — small modules; type hints on public functions; no unnecessary dependencies; no secrets in code; gitignored manifest.
- **Rules** — explicit guardrails:
  - Work on one phase at a time. Respect the current phase spec's "Out of scope" list as a hard boundary.
  - Do not modify `docs/DECISIONS.md` to revise an existing decision. Append a new decision that supersedes the old one, or pause and ask.
  - Do not run `git push --force` on shared branches.
  - Do not commit `manifest.yaml`, `.env`, anything in target repos' `local_path` directories, or any secrets.
  - When the right shape of a thing is unclear, read `docs/DECISIONS.md` before introducing a new pattern.
- **Skills** — pointer to `.claude/skills/README.md`.

### .claude/skills/README.md

A short index. State that no skills are implemented yet — future phases add them. List the planned skills as placeholders only:

- `ideation` — converts a brain dump into a Linear ticket. Ships in Phase 5.
- `work` — orchestrates a single ticket → PR run. Implemented inline as part of Phase 1's executor; may be promoted to a formal skill once the shape stabilizes.
- `scope-check` — verifies a diff stays within a ticket's `scope_paths`. Ships in Phase 4.

Document the convention (each skill is a directory with `SKILL.md` plus optional scripts/templates) and explicitly note that Phase 0 does not implement any skills.

### docs/DECISIONS.md (architectural decisions)

This file is the durable record of *why* the factory is shaped the way it is. Future agents read it to avoid relitigating decisions that have already been settled.

Format: ADR-lite. Each decision has Status, Context, Decision, Consequences. Append-only — superseded decisions get a new entry, not an in-place edit.

Write the file with exactly the following nine decisions, in this order, expanding each into the four-section format:

**ADR-001: Workspace pattern, not nested repos.** Context: an early sketch had target repos as subfolders of `ai_factory`; submodules are operationally painful, subtrees are lossy, plain nesting breaks git. Decision: `ai_factory` is a control plane that references target repos by URL and local path; target repos are cloned as siblings on disk (e.g., `~/factory/repos/<repo>`). Consequences: factory repo stays small; target repos can be updated independently; the factory must run git operations across multiple working trees.

**ADR-002: Single model (Claude Sonnet) for both ideation and execution in v1.** Context: an earlier sketch considered cheap-on-ideation, strong-on-execution model routing, but for a personal factory the operational complexity outweighs the savings; coding execution especially benefits from a capable model. Decision: use Claude Sonnet for both ideation and execution in v1; do not build model-routing logic. Consequences: simpler implementation; higher per-ticket cost than a routing strategy could achieve; revisit only if specific repetitive ticket patterns make a cheaper executor obviously worth it.

**ADR-003: Linear team-per-repo via native GitHub integration.** Context: need a "this ticket → this repo" mapping; could live in the manifest, in Linear, or both. Decision: use Linear's native GitHub integration with one team per target repo, team key conventionally matching repo name; the factory reads team↔repo from Linear, not the manifest. Consequences: user manages mapping in one place (Linear UI); manifest stays minimal; depends on Linear's GraphQL exposing team↔repo metadata in a usable shape — verify in Phase 2.

**ADR-004: Custom fields on Linear issues for ticket metadata.** Context: a "Ready" boolean is not enough metadata for safe execution. Decision: required custom fields on Linear issues — `scope_paths`, `acceptance_criteria`, `budget_tokens`, `budget_minutes`; optional `target_repo` for overrides. Consequences: marking a ticket ready is a deliberate act with structured intent the agent can act on safely.

**ADR-005: Phased build order, contracts first.** Context: risk of building plumbing before validating the core unknown ("can an agent reliably ship a PR end-to-end?"). Decision: phases 0 contracts → 1 executor → 2 Linear read → 3 closed loop → 4 hardening → 5 ideation → 6 local scheduling (optional) → 7 multi-repo (optional). Consequences: no working system until Phase 1; ideation comes late despite being conceptually first, because it can't produce good tickets until the schema is validated by the executor.

**ADR-006: No auto-merge; branch protection on target repos required.** Context: auto-merging maximizes throughput but turns rare bad outputs into compounding damage. Decision: never auto-merge; branch protection on every target repo's default branch must require human PR approval; the factory writes back to Linear ("PR opened, awaiting review") but never advances a ticket to Done on its own. Consequences: human in the loop on every change; throughput bounded by review capacity — this is the trade we want.

**ADR-007: Manifest holds host-specific config only.** Context: an early design had the manifest holding team↔repo mapping and other metadata that arguably belongs in Linear. Decision: manifest holds only what cannot live in Linear — local filesystem paths, build/test commands, per-repo overrides; everything ticket-shaped lives in Linear; manifest is gitignored. Consequences: where a ticket goes and what its scope is is edited in Linear's UI, not YAML; setting up the factory on a new machine is small (clone, copy `manifest.example.yaml`, edit paths).

**ADR-008: Tickets-on-disk format mediates Linear and the executor.** Context: the executor needs a stable input format; Linear is not stable enough to be a direct dependency of the executor. Decision: tickets are markdown files with YAML frontmatter (see `TICKET_SPEC.md`); Phase 2 builds a "pull from Linear → write tickets to disk" step; the executor reads from disk only. Consequences: tickets can be hand-written for testing without Linear; the executor is testable in isolation; one extra layer (disk) sits between Linear and execution, which is the point.

**ADR-009: Local-only execution in v1.** Context: an earlier sketch considered GitHub Actions on cron for an unattended overnight runner, requiring secrets management and remote execution. Decision: v1 runs entirely on the user's local machine; `factory run` is invoked manually or via local scheduling (launchd/cron) in a deferred Phase 6. Consequences: no remote secrets to manage; auth is whatever the local user has set up (`gh auth`, `claude` login, Linear API key in `.env`); the local machine must be running for scheduled invocations; remote execution can be added later as an alternative path if needed.

After the nine ADRs, add a final section titled **"Pitfalls noted during design"** listing risks identified but not turned into ADRs because they're addressed in specific phases. Include at least: scope creep within a single ticket (Phase 4), secret leakage (Phase 4), cost runaway from retries (Phase 4), cross-repo dependencies (deferred), Linear-as-source-of-truth fragility (Phase 4 local cache), re-run determinism (uuid suffixes in Phase 1), and context bloat from loading every sub-repo's context per run (resolved by loading only target repo's `CLAUDE.md`).

## Documents — required content

### docs/ARCHITECTURE.md

Describe the system at a high level. Required sections:

- **What it is** — one paragraph.
- **The loop** — ideation → Linear ticket → human marks ready → factory runs locally → PR opens → human reviews. Show this as a short ASCII or mermaid diagram.
- **Components** — Linear (queue), GitHub (target repos), the factory CLI running locally, the local manifest, Claude Code (executor).
- **Boundaries** — what lives in Linear (per-issue metadata, team↔repo mapping via Linear's GitHub integration), what lives in the manifest (local filesystem paths, build/test commands), what lives in each target repo's `CLAUDE.md` (per-repo coding rules).
- **Trust model** — agents never auto-merge; branch protection on target repos requires human review; budget caps prevent runaway costs (enforcement comes in Phase 4).

Length: roughly one to two pages of prose. Don't overspecify behavior that hasn't been built.

### docs/PHASES.md

Reference doc listing phases 0–7 with a one-paragraph summary each. Mark Phase 0 as in progress; everything else as not started. Phases:

0. Contracts and scaffolding
1. Single-shot executor (one repo, no Linear)
2. Linear read path (pull ready tickets to local queue)
3. Closed loop (read, execute, write back to Linear)
4. Hardening (scope checks, budget caps, idempotency)
5. Ideation (factory ideate command) — **stopping point for v1**
6. Local scheduling — optional, deferred (launchd/cron for unattended runs)
7. Multi-repo — optional, deferred (add a second registered repo)

State explicitly that v1 stops at Phase 5; Phases 6 and 7 are deferred until a concrete need arises.

### docs/LINEAR_SCHEMA.md

Specify the Linear configuration the factory expects. Phase 0 only documents this; nothing reads it yet.

- **Teams**: one team per target repo. Team key conventionally matches repo name (e.g., `THMS` for `thms-platform`).
- **Workflow states**: standard Linear states plus a "Ready for Agent" state in the "Started" category. The factory polls for issues in this state.
- **Custom fields on issues** (define each with type and purpose):
  - `target_repo` (text, optional override of team default)
  - `scope_paths` (multiline text, glob patterns, one per line, optional)
  - `acceptance_criteria` (multiline text, required — may be sourced from a structured section in the issue description)
  - `budget_tokens` (number, optional, default 50000)
  - `budget_minutes` (number, optional, default 30)
- **Labels**: `factory:ready` (alternative trigger if not using a workflow state), `factory:in-progress`, `factory:failed`.
- **Branch name template** (workspace-level Linear setting): `{username}/{issue.identifier}-{issue.title-kebab}`.

### docs/MANIFEST.md

Specify the shape of `manifest.yaml`. The manifest lives at the root of `ai_factory` and lists every registered target repo. Because `local_path` is host-specific, `manifest.yaml` is gitignored; `manifest.example.yaml` is the checked-in template.

```yaml
version: 1
queue_dir: .factory/queue
repos:
  thms-platform:
    github: toms-hms/thms-platform
    local_path: ~/factory/repos/thms-platform
    default_branch: main
    test_command: "npm test"
    build_command: "npm run build"
    linear_team: THMS
```

Document every field: type, required vs. optional, what it's used for, and which phase first reads it. `queue_dir` is where pulled tickets land (Phase 2 first reads it); default `.factory/queue` is gitignored.

### docs/TICKET_SPEC.md

Define the on-disk ticket format that the executor consumes. This is the contract between the (future) Linear-pull layer and the executor. Defining it now anchors both ends.

A ticket is a markdown file with YAML frontmatter:

```markdown
---
id: THMS-42
title: Add CHANGELOG entry for AI factory test
target_repo: thms-platform
scope_paths:
  - CHANGELOG.md
budget_tokens: 10000
budget_minutes: 5
linear_url: https://linear.app/<workspace>/issue/THMS-42  # populated by Phase 2; absent for hand-written tickets
---

## Acceptance Criteria

- A new line is added to `CHANGELOG.md` under an "Unreleased" section.
- The line reads "Add AI factory test entry".
- All existing tests still pass.
- No other files are modified.

## Notes

This is a hello-world ticket to verify the executor pipeline works.
```

Document every frontmatter field, the required body sections (`## Acceptance Criteria` is required; `## Notes` is optional), and how each field maps to its source field in `LINEAR_SCHEMA.md`.

### examples/tickets/hello-world.md

Create the example ticket above as a real file (omit `linear_url` since it's hand-written). This is the input Phase 1 will run against.

### manifest.example.yaml

Create with one entry for `thms-platform` matching the schema in `MANIFEST.md`.

### README.md

Short. What this is, how to set up (`uv sync`, copy `manifest.example.yaml` to `manifest.yaml` and edit `local_path`), pointer to `docs/`. Do not document Phase 1+ behavior.

## Acceptance criteria

- [ ] `uv sync` succeeds with no errors.
- [ ] `uv run factory --help` prints the help text.
- [ ] `uv run factory version` prints the version line.
- [ ] `CLAUDE.md` exists at repo root with all required sections, including the Rules block.
- [ ] `.claude/skills/README.md` exists and lists the planned skills as placeholders.
- [ ] `docs/DECISIONS.md` exists with all nine ADRs (ADR-001 through ADR-009) in the four-section format, plus the "Pitfalls noted during design" section.
- [ ] All five docs in `docs/` (ARCHITECTURE, PHASES, LINEAR_SCHEMA, MANIFEST, TICKET_SPEC) exist with real prose, not placeholder text.
- [ ] `examples/tickets/hello-world.md` exists and conforms to `TICKET_SPEC.md`.
- [ ] `manifest.example.yaml` exists and conforms to `MANIFEST.md`.
- [ ] `manifest.yaml` and `.factory/` are in `.gitignore`.
- [ ] Repo is committable cleanly (no extraneous files, no secrets).

## Out of scope — do not build

- Reading the manifest from disk (Phase 1).
- Parsing tickets (Phase 1).
- Any git, GitHub, Linear, or Claude Code subprocess invocation.
- Implementing any actual skills under `.claude/skills/` — only the README index is in scope.
- Tests beyond a smoke test that imports the CLI module.
- Any CI configuration.
