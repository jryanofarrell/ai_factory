# Architectural decisions

This file is the durable record of why `ai_factory` is shaped the way it is. It is the distilled output of the design conversation that produced the phase specs. Future agents read it to avoid relitigating settled decisions.

**Format:** ADR-lite. Each decision has Status, Context, Decision, Consequences.
**Rule:** Append-only. Do not edit an existing ADR. If a decision is superseded, add a new entry referencing the old one.

---

## ADR-001: Workspace pattern, not nested repos

**Status:** Accepted

**Context:** The original sketch had target repositories (e.g., `thms-platform`) as subfolders inside `ai_factory`. Git submodules are operationally painful — they require coordinated updates and create friction on every clone. Subtrees are lossy and hard to keep in sync. Plain directory nesting breaks Git: you cannot meaningfully `git add` files inside a nested `.git` directory without turning it into a submodule.

**Decision:** `ai_factory` is a control plane that references target repositories by GitHub URL and local filesystem path. Target repos are cloned as siblings on disk (e.g., `~/factory/repos/<repo-name>`). The `manifest.yaml` records these paths; `ai_factory` itself never contains the target repo's content.

**Consequences:** The `ai_factory` repository stays small and focused on orchestration. Target repos can be updated independently without touching the factory. The factory must perform git operations across multiple working directories (managed via the local path in the manifest), but this is straightforward and explicit. Running the factory on a new machine means cloning `ai_factory`, copying `manifest.example.yaml`, and editing the `local_path` fields to point at wherever the target repos live locally.

---

## ADR-002: Single model (Claude Sonnet) for both ideation and execution in v1

**Status:** Accepted

**Context:** An earlier sketch considered cheap-on-ideation, strong-on-execution model routing. For a personal factory running locally, the operational complexity of routing logic outweighs the savings — there is no billing dashboard to optimize against, and the volume is low. Coding execution especially benefits from a capable model; getting a PR right on the first pass matters more than minimizing per-ticket token cost.

**Decision:** Use Claude Sonnet for both ideation and execution in v1. Do not build model-routing logic. This is a deliberate simplification: one model, one configuration, no conditional dispatch.

**Consequences:** Simpler implementation — no model parameter threading, no per-command defaults to configure. Higher per-ticket cost than a routing strategy could achieve, but the absolute cost is low at personal-factory volumes. Revisit only if specific repetitive ticket patterns make a cheaper executor obviously worth the complexity.

---

## ADR-003: Linear team-per-repo via native GitHub integration

**Status:** Accepted

**Context:** The factory needs a stable mapping from "this ticket belongs to this repository." Options considered: (a) explicit mapping in `manifest.yaml`, (b) a custom field on every Linear issue, (c) Linear's native GitHub integration, which associates a Linear team with a GitHub repo. Option (a) duplicates information. Option (b) requires the user to fill in a field on every ticket. Option (c) leverages existing Linear infrastructure the user will set up anyway.

**Decision:** Use Linear's native GitHub integration with one Linear team per target repository. By convention the team key matches the repo name (e.g., team key `THMS` for repo `thms-platform`). The factory reads the team↔repo mapping from Linear's GraphQL API, not from the manifest. The manifest records the `linear_team` key as a local override for cases where the convention doesn't hold.

**Consequences:** The user manages the mapping in one place (Linear's workspace settings UI). The manifest stays minimal. This depends on Linear's GraphQL API exposing team↔repo metadata in a usable shape — this assumption must be validated in Phase 2 before the read path is built. If the API does not expose this, the fallback is an explicit field in the manifest.

---

## ADR-004: Custom fields on Linear issues for ticket metadata

**Status:** Accepted

**Context:** A simple "Ready" boolean is not enough for the executor to run safely. The executor needs to know where it is allowed to make changes (`scope_paths`), what counts as done (`acceptance_criteria`), and when to stop (`budget_tokens`, `budget_minutes`). Putting this in the ticket body as prose would make it hard to parse reliably. Linear supports custom fields (properties) on issues, which are structured and queryable via GraphQL.

**Decision:** The factory requires the following custom fields on every Linear issue that the executor will process:
- `scope_paths` (multiline text, optional) — glob patterns restricting which files the executor may touch.
- `acceptance_criteria` (multiline text, required) — structured criteria the PR must satisfy.
- `budget_tokens` (number, optional, default 50 000) — max Claude tokens for this run.
- `budget_minutes` (number, optional, default 30) — max wall-clock minutes for this run.
- `target_repo` (text, optional) — explicit repo override, used when the team-default mapping is insufficient.

**Consequences:** Marking a ticket "Ready for Agent" becomes a deliberate act. The engineer must have filled in structured metadata, not just flipped a state. This is a friction point but it is load-bearing: the executor relies on these fields to run safely. The Linear workspace must be configured with these custom fields before Phase 2 can proceed.

---

## ADR-005: Phased build order, contracts first

**Status:** Accepted

**Context:** There is a risk of building a lot of plumbing (Linear read path, GitHub integration, cron runner) before validating the core unknown: can a Claude Code agent reliably produce a correct PR from a structured ticket on the first or second attempt? If the answer is "not reliably," the whole system's value proposition is in question and the architecture may need to change.

**Decision:** Build in phases that front-load validation of the core unknown:
- Phase 0: contracts and scaffolding (this phase)
- Phase 1: single-shot executor against a hand-written ticket, no Linear
- Phase 2: Linear read path (pull ready tickets to local queue)
- Phase 3: closed loop (read, execute, write back to Linear)
- Phase 4: hardening (scope checks, budget caps, idempotency)
- Phase 5: ideation (`factory ideate` command) — **v1 stopping point**
- Phase 6: local scheduling — optional, deferred (launchd/cron for unattended runs)
- Phase 7: multi-repo — optional, deferred (add a second registered repo)

**Consequences:** There is no working end-to-end system until Phase 1. The ideation command ships at Phase 5 despite being conceptually the "front" of the loop, because it cannot produce good tickets until the ticket schema has been validated by the executor in Phase 1. Phases 6 and 7 are optional: they add convenience but the factory is fully usable without them. Each phase builds on a working foundation, which means integration bugs surface early.

---

## ADR-006: No auto-merge; branch protection on target repos required

**Status:** Accepted

**Context:** Auto-merging PRs maximizes throughput but turns rare bad outputs into compounding damage — an agent that modifies the wrong files or introduces a subtle bug merges before anyone notices. The risk is asymmetric: the cost of a human review step is low (a few minutes) and the cost of a bad merge is high (broken main, rollback, incident).

**Decision:** The factory never auto-merges a PR. Every target repo's default branch must have branch protection rules requiring at least one human approval before merge. After opening a PR, the factory writes back to Linear ("PR opened, awaiting review") and transitions the ticket to an appropriate "In Review" state, but it never advances a ticket to Done on its own.

**Consequences:** Every ticket requires a human in the loop before it lands. Throughput is bounded by the reviewer's capacity, not the agent's speed. This is an intentional trade: safety over throughput. If the agent's output quality proves high enough over time, the branch protection rules can be relaxed by the human — but this is a manual decision, not a system default.

---

## ADR-007: Manifest holds host-specific config only

**Status:** Accepted

**Context:** An early design had the manifest holding the team↔repo mapping, per-ticket overrides, and other metadata that arguably belongs in Linear. This created a split-brain problem: information about a ticket's target and scope would live partly in Linear and partly in a local YAML file, making neither source authoritative.

**Decision:** The manifest holds only what cannot live in Linear: local filesystem paths (`local_path`), build and test commands (`build_command`, `test_command`), the default branch name, and a `linear_team` key for the team↔repo override. Everything ticket-shaped (scope, acceptance criteria, budget) lives in Linear. Everything repo-shaped but not host-specific (GitHub URL, default branch intent) lives in the manifest only as a convenience — the authoritative source is GitHub. The manifest is gitignored because `local_path` is inherently host-specific.

**Consequences:** A ticket's target repo and scope are edited in Linear's UI, not in YAML. Setting up the factory on a new machine is small: clone `ai_factory`, copy `manifest.example.yaml` to `manifest.yaml`, edit the `local_path` fields. The manifest does not grow as more tickets are added — only as more repos are registered.

---

## ADR-008: Tickets-on-disk format mediates Linear and the executor

**Status:** Accepted

**Context:** The executor (Phase 1) needs a stable, testable input format. Linear's API and data model are not stable enough to be a direct dependency of the executor — field names, GraphQL schema, and rate limits can change, and the executor should be testable without a live Linear connection. Coupling the executor directly to Linear also makes local testing harder.

**Decision:** Tickets are Markdown files with YAML frontmatter (format specified in `docs/TICKET_SPEC.md`). Phase 2 builds a "pull from Linear → write tickets to disk" step that translates Linear issues into this format. The executor reads from disk only and has no knowledge of Linear. The ticket files live in `examples/tickets/` for local testing; the pull step writes them to a queue directory at runtime.

**Consequences:** Tickets can be hand-written for testing the executor without a Linear connection (as in Phase 1's hello-world test). The executor is fully testable in isolation. There is one extra layer (disk) between Linear and execution, which adds a small amount of latency (negligible) and a clear seam for debugging (the ticket file is inspectable before and after the run). If Linear's schema changes, only the pull step needs updating; the executor is unaffected.

---

## ADR-009: Local-only execution in v1

**Status:** Accepted

**Context:** An earlier sketch included a Phase 5 GitHub Actions cron runner for unattended overnight execution. This requires storing secrets remotely (Linear API key, Anthropic API key, GitHub token) in Actions secrets, adds a CI/CD surface to maintain, and introduces remote execution complexity before the factory's core behavior is validated. For a personal factory owned by a single developer, the machine is almost always available during working hours.

**Decision:** v1 runs entirely on the user's local machine. `factory run` (and later `factory work`) is invoked manually or via local scheduling (launchd or cron) in a deferred optional phase. Auth is whatever the local user has configured: `gh auth login`, `claude` CLI login, and a Linear API key in `.env`. No remote execution infrastructure in v1.

**Consequences:** No remote secrets to manage. Setup on a new machine is a clone plus env vars — no CI configuration. The local machine must be running for scheduled invocations, which is an acceptable constraint for a personal tool. Remote execution (GitHub Actions or similar) can be added as an alternative path if the need arises, without changing the core executor interface.

---

## Pitfalls noted during design

These risks were identified during the design conversation but are not turned into ADRs because they are addressed in specific phases or deferred with a clear rationale.

- **Scope creep within a single ticket.** An agent may touch files outside the declared `scope_paths`, either by accident or because a dependency requires it. Addressed in Phase 4 via a `scope-check` skill that diffs the branch and rejects out-of-scope changes before the PR is opened.

- **Secret leakage.** The executor has filesystem access to the target repo. If the repo contains `.env` files, credentials, or keys, the agent could log or include them in the PR. Addressed in Phase 4 via pre-run checks (confirm `.gitignore` covers secrets) and post-run diff inspection. `manifest.yaml` and `.env` are gitignored by convention.

- **Cost runaway from retries.** A ticket that repeatedly fails (broken tests, wrong output) will burn tokens on each retry. Budget caps (`budget_tokens`, `budget_minutes`) are declared on the ticket in Phase 0 but enforcement is not implemented until Phase 4. Until then, manual oversight is required.

- **Cross-repo dependencies.** A ticket may require changes in two repos simultaneously (e.g., a shared library and a consumer). The factory does not support this; each run targets exactly one repo. Cross-repo work must be split into separate tickets and coordinated manually. Deferred indefinitely — the complexity is not worth it for the initial use case.

- **Linear-as-source-of-truth fragility.** If Linear is unavailable or the API schema changes, the pull step breaks. Addressed in Phase 4 via a local cache: tickets are written to disk on pull and the executor reads from disk, so a transient Linear outage does not interrupt in-flight runs.

- **Re-run determinism.** Running the executor twice on the same ticket should not produce conflicting branches or PRs. Addressed in Phase 1 by appending a short UUID suffix to branch names (`{identifier}-{title}-{uuid[:8]}`), so each run produces a distinct branch regardless of whether a previous run's branch was merged or deleted.

- **Context bloat from loading every sub-repo's CLAUDE.md per run.** If the executor loads `CLAUDE.md` files from all registered repos, the context window fills with irrelevant rules. Resolved by loading only the target repo's `CLAUDE.md` — the executor is told which repo it is working in, and it loads only that repo's context.

---

## ADR-010: Ticket metadata in issue description sections, not Linear custom properties

**Status:** Accepted — supersedes ADR-004

**Context:** ADR-004 specified that `scope_paths`, `acceptance_criteria`, `budget_tokens`, and `budget_minutes` would live as custom properties on Linear issues, queryable via GraphQL. During the Phase 2 spike, introspecting Linear's GraphQL schema revealed that custom properties are not exposed on the `Issue` type at all — `customFields`, `customProperties`, and `IssuePropertyValue` do not exist in the API. There is no way to read custom fields via GraphQL with the current API version.

**Decision:** All ticket metadata is embedded in the issue description as structured Markdown sections. The factory parses these sections at pull time. The required and optional sections are:

- `## Acceptance Criteria` (required) — bulleted list of success conditions
- `## Scope Paths` (optional) — one glob pattern per line; blank lines and `#` comments stripped
- `## Budget` (optional) — `tokens: N` and `minutes: N` on separate lines; defaults apply if absent
- `## Target Repo` (optional) — single line with the manifest repo key; overrides team-default resolution
- `## Notes` (optional) — freeform context for the executor

**Consequences:** Users write structured Markdown in the Linear description field, which is readable and editable in the Linear UI. No workspace-level custom property setup is required. The section names are case-sensitive and must appear exactly as above. This is simpler to set up than custom properties and works within the current API constraints. If Linear exposes custom properties in a future API version, this decision can be revisited — but the description format is not worse from a usability standpoint.
