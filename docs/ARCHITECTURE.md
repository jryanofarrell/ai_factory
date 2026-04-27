# Architecture

## What it is

`ai_factory` is a personal AI factory that turns Linear tickets into GitHub pull requests, running entirely on the user's local machine. It sits between a Linear workspace (the queue) and one or more GitHub repositories (the targets). When a ticket is marked ready, the factory pulls its metadata, spins up a Claude Code executor in the target repository, waits for the PR to be opened, and writes the result back to Linear. The human reviews and merges the PR; the factory never merges automatically.

## The loop

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  Human                                                          │
  │                                                                 │
  │  1. Brain dump or idea                                          │
  │       │                                                         │
  │       ▼                                                         │
  │  2. Linear ticket created (with scope_paths,                    │
  │     acceptance_criteria, budget fields)                         │
  │       │                                                         │
  │       ▼                                                         │
  │  3. Human marks ticket "Ready for Agent"                        │
  └───────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  ai_factory (control plane)                                     │
  │                                                                 │
  │  4. Pull step: read ticket from Linear → write to disk          │
  │       │                                                         │
  │       ▼                                                         │
  │  5. Executor: Claude Code runs inside target repo,              │
  │     reads ticket from disk, produces a branch + PR             │
  │       │                                                         │
  │       ▼                                                         │
  │  6. Write-back: update Linear ticket state,                     │
  │     add PR link as comment                                      │
  └───────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  Human                                                          │
  │                                                                 │
  │  7. Review PR on GitHub → approve and merge                     │
  │       │                                                         │
  │       ▼                                                         │
  │  8. Linear ticket advances to Done                              │
  └─────────────────────────────────────────────────────────────────┘
```

## Components

**Linear (queue).** Linear is the single source of truth for what work needs to be done. Each target repository has a corresponding Linear team. Issues carry structured custom fields (`scope_paths`, `acceptance_criteria`, `budget_tokens`, `budget_minutes`) that the factory reads. The factory polls for issues in the "Ready for Agent" workflow state.

**GitHub (target repositories).** Target repos are where the code lives. The executor opens branches and pull requests here. Branch protection rules on the default branch require human approval before merge.

**The factory CLI (`factory`).** A small Python CLI (Typer) that orchestrates the loop, invoked manually on the user's local machine. Entry points added per phase: Phase 1 adds `factory run`, Phase 2 adds `factory pull`, Phase 3 adds `factory work`, Phase 5 adds `factory ideate`. In Phase 0 the only command is `factory version`.

**The local manifest (`manifest.yaml`).** A gitignored YAML file listing every registered target repo with host-specific configuration: local filesystem path, build/test commands, default branch, and the Linear team key. `manifest.example.yaml` is the checked-in template.

**Claude Code (executor).** The executor is a Claude Code agent that runs inside the target repository's working directory. It reads a ticket from disk, follows the target repo's `CLAUDE.md` for coding conventions, implements the changes, runs tests, and opens a PR. The executor is invoked as a subprocess by the factory CLI; it has no direct knowledge of Linear.

## Boundaries

| Concern | Lives in |
|---|---|
| What work needs doing | Linear (issue queue) |
| Team → repo mapping | Linear native GitHub integration |
| Ticket metadata (scope, criteria, budget) | Linear custom fields → ticket files on disk |
| Local filesystem paths | `manifest.yaml` (gitignored) |
| Build and test commands | `manifest.yaml` |
| Per-repo coding conventions | Target repo's `CLAUDE.md` |
| Factory agent conventions and rules | `ai_factory/CLAUDE.md` (this repo) |
| Architectural decisions and rationale | `docs/DECISIONS.md` |

## Trust model

**No auto-merge.** The factory opens pull requests; humans merge them. This is a hard constraint, not a configuration option (see ADR-006).

**Branch protection required.** Every registered target repository must have branch protection rules on its default branch that require at least one human approval before merge. The factory does not enforce this at setup time, but Phase 4 will add a pre-flight check.

**Budget caps.** Every ticket declares `budget_tokens` and `budget_minutes`. The executor respects these limits; a run that exceeds them is aborted and the ticket is marked failed. Budget enforcement is implemented in Phase 4.

**Secrets never in code.** The factory reads API keys and tokens from environment variables or `manifest.yaml` (gitignored). Nothing sensitive is committed to `ai_factory` or to any target repo as a result of a factory run. Pre-run checks (Phase 4) verify that the target repo's `.gitignore` covers common secret files.

**Scope checks.** If a ticket declares `scope_paths`, the executor's output diff is checked against those globs before the PR is opened. Out-of-scope changes cause the run to fail. Implemented in Phase 4.
