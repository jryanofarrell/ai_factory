# Phases

This document summarizes the build phases of `ai_factory`. Phases are sequential; each phase builds on a working foundation from the previous one. Do not implement Phase N+1 behavior while working on Phase N.

---

## Phase 0 — Contracts and scaffolding `[IN PROGRESS]`

Produces no executable behavior beyond a `factory version` CLI command. Deliverables are the repo structure, design documents, and the memory/skills/rules scaffolding that every future agent session reads on entry. The docs are load-bearing: the ticket spec, manifest spec, and architectural decisions anchor both ends of every interface built in later phases. Phase 0 is complete when all acceptance criteria in `phases/PHASE_0_SPEC.md` are met.

---

## Phase 1 — Single-shot executor `[NOT STARTED]`

Implements `factory run <ticket-file>`. The command reads a ticket from disk (using the format defined in `docs/TICKET_SPEC.md`), clones or updates the target repo, invokes a Claude Code executor agent inside the target repo's working directory, and opens a GitHub PR. No Linear integration — the ticket is hand-written (e.g., `examples/tickets/hello-world.md`). The acceptance test is a successful PR on `toms-hms/thms-platform` that adds a CHANGELOG entry, with all tests passing and no files outside `scope_paths` modified. Phase 1 validates the core unknown: can the executor reliably produce a correct PR from a structured ticket?

---

## Phase 2 — Linear read path `[NOT STARTED]`

Implements `factory pull`. Connects to the Linear GraphQL API, queries for issues in the "Ready for Agent" workflow state across all registered teams, translates each issue into the on-disk ticket format, and writes them to a local queue directory. After this phase, the hello-world ticket can be driven entirely from Linear rather than a hand-written file. This phase also validates ADR-003 (that Linear's API exposes the team↔repo mapping in a usable shape).

---

## Phase 3 — Closed loop `[NOT STARTED]`

Connects the pull step (Phase 2) and the executor (Phase 1) into a single `factory work` command: pull ready tickets from Linear, run the executor on each, write results back to Linear (PR link as comment, ticket state transition to "In Review"). After this phase the factory can run end-to-end with a human only at the "mark ready" and "review PR" steps.

---

## Phase 4 — Hardening `[NOT STARTED]`

Adds the safety rails that make the factory safe to run on real work: scope checks (diff vs. `scope_paths` globs), budget cap enforcement (`budget_tokens` and `budget_minutes`), idempotency (detect already-open PRs for a ticket, skip re-running), pre-flight checks (branch protection rules exist, `.gitignore` covers secrets), a local Linear cache (ticket files survive a Linear outage), and improved error handling and retry logic with exponential backoff. After this phase the factory is safe to use on production repositories.

---

## Phase 5 — Ideation `[NOT STARTED]` ★ v1 stopping point

Implements `factory ideate`. Takes a free-form brain dump (from stdin or a file) and produces a well-structured Linear ticket with all required custom fields populated: title, description, `acceptance_criteria`, `scope_paths`, `budget_tokens`, `budget_minutes`. After this phase the full loop is human-operable without leaving the terminal: brain dump → `factory ideate` → review ticket in Linear → mark ready → factory runs → review PR. Implemented as the `ideation` skill under `.claude/skills/ideation/`. Uses Claude Sonnet (see ADR-002).

**This is the v1 stopping point.** Phases 6 and 7 are deferred until a concrete need arises.

---

## Phase 6 — Local scheduling `[DEFERRED — optional]`

Adds unattended local execution via launchd (macOS) or cron. Wraps `factory work` in a scheduled job that runs on a configurable interval (e.g., every 15 minutes during working hours) without manual invocation. This phase also adds a simple run log and per-ticket execution summaries. Deferred because the manual `factory work` workflow from Phase 3 is sufficient for personal use, and the factory must prove reliable in attended mode before running unattended.

---

## Phase 7 — Multi-repo `[DEFERRED — optional]`

Registers a second target repository in `manifest.yaml` and validates that the factory correctly routes tickets to the right repo based on their Linear team. This phase surfaces any assumptions baked into Phases 1–5 that only hold for a single repo. Expected work: fix any hardcoded repo assumptions, validate the team↔repo mapping for the second repo, add the second repo's `CLAUDE.md` to the context loading logic. Deferred until there is a second repo to register.
