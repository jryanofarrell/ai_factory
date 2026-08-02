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

---

## ADR-011: Multi-provider executor with quota-aware fallback

**Status:** Accepted — supersedes ADR-002

**Context:** ADR-002 chose a single model (Claude Sonnet) and explicitly deferred model-routing logic as unjustified complexity at personal-factory volumes. The constraint has changed: both Claude Pro and OpenAI's Codex CLI include free usage credits with a $20/month subscription, but each has a rolling quota window (Claude ~5 hours, Codex ~4 hours). When one provider hits its quota mid-batch, the factory previously stopped entirely. Adding a second provider as a fallback doubles throughput without adding paid API costs — making the routing complexity worthwhile at current usage patterns.

**Decision:** The executor supports an ordered list of providers (`executor_providers` in `manifest.yaml`, default `["claude"]`). When `run_ticket` invokes the executor, it tries providers in order via `_run_with_fallback`. If a provider returns `usage_limit_hit=True`, the factory records the exhaustion timestamp to `.factory/quota_state.json` via `QuotaTracker` and tries the next provider. Subsequent tickets skip exhausted providers until the reset window has elapsed. Supported providers: `claude` (wraps `claude` CLI), `codex` (wraps `codex exec --json`). The local LLM provider (LM Studio) is deferred to a later phase; `codex exec` already supports `--local-provider lmstudio` natively when that time comes.

**Consequences:** Operators who want fallback add `executor_providers: [claude, codex]` to `manifest.yaml` and install `codex` (`npm install -g @openai/codex`). No other configuration is required. The quota state file is gitignored (it is under `.factory/`). Quota detection for `codex` relies on parsing JSONL events and stderr for OpenAI's 429/402 error codes; if OpenAI changes its error format, the keyword list in `providers/codex.py` may need updating. The `stop_on_usage_limit` manifest flag now triggers only when all configured providers are exhausted, not just the first one.

---

## ADR-012: Budgets are soft planning guidance, not executor kill switches

**Status:** Accepted — supersedes the hard-budget portions of ADR-004 and ADR-010

**Context:** Hard `budget_minutes` enforcement caused useful work to be killed mid-run, leaving otherwise salvageable branches to be finished manually. In practice, budgets are more useful as ticket-sizing guidance than as strict execution limits. The human can decide whether to split work or let an overrun continue after reviewing progress and PR scope.

**Decision:** `budget_tokens` and `budget_minutes` remain parseable metadata for old tickets and optional planning notes for new tickets, but the executor does not abort solely because a ticket exceeds them. Ticket creation recipes should omit `## Budget` by default and include it only when the user explicitly asks for soft sizing notes. Runtime safety continues to rely on scope checks, tests, quota-aware provider fallback, secret scanning, and human PR review.

**Consequences:** Long-running tickets can finish instead of being terminated at an arbitrary minute mark. Operators must use judgment when a ticket is clearly too broad; agents should preserve coherent partial work and report overruns instead of sprawling indefinitely. Historical tickets with `## Budget` sections still parse, but those values are informational.

---

## ADR-013: Canonical AI file layout in target repositories

**Status:** Accepted

**Context:** Each target repository needs AI-readable files for both Claude Code and Codex CLI agents (the factory uses both — see ADR-011) to work productively: rules, codebase context, and procedural how-to guides. Without a shared convention, every repo ends up with ad-hoc files in different locations and the executor cannot rely on finding context where it expects it. Both providers need their own auto-loaded pointer file at the repo root (Claude Code reads `CLAUDE.md`, Codex CLI reads `AGENTS.md`), but the substantive content should live in one shared tree to avoid duplication and drift between providers.

**Decision:** Every target repository follows a single canonical AI file layout:

```
<repo-root>/
  CLAUDE.md          - Claude Code pointer, auto-loaded
  AGENTS.md          - Codex CLI pointer, auto-loaded
  .ai/
    rules/core.md       - always-applicable constraints
    context/<area>.md   - codebase snapshot per area, loaded on demand
    recipes/
      ai-structure.md   - map of the .ai/ layout
      <area>/<task>.md  - procedural how-to guides, added incrementally
```

`CLAUDE.md` and `AGENTS.md` carry identical bodies and both point at the same `.ai/` tree. Per-ticket work updates the relevant `.ai/context/<area>.md` as part of its scope so the context stays fresh rather than rotting.

Bootstrapping and re-audit are handled by the `/ai-files` slash command (`.claude/commands/ai-files.md` in this repo). The factory's first action when entering a target repo missing these files is to invoke `/ai-files`. The command populates content by reading actual code, never with TODO stubs.

`.ai/recipes/<area>/<task>.md` files are added incrementally as patterns repeat, not pre-scaffolded as empty stubs.

**Consequences:** New target repos can be onboarded with a single command, with content drawn from actual code rather than placeholders. Both providers see the same context — no provider-specific drift. The `/ticket` recipe already scans `.ai/recipes/` and pins relevant recipe paths into each ticket description, so once a repo's recipes tree grows, the ticket flow automatically picks up the new guidance. The convention is enforced at the command level rather than as a hard runtime check; a repo without the layout is not blocked from being worked on, but the factory's quality will degrade without it.

---

## ADR-014: Linear-ready tickets are authoritative for normal runs

**Status:** Accepted — supersedes the runtime authority portion of ADR-008

**Context:** ADR-008 introduced on-disk Markdown tickets as the contract between Linear and the executor. That made the executor easy to test and made pulled tickets inspectable, but it also created a stale second source of truth: a ticket file left in `.factory/queue/` could be executed even after the Linear issue had moved out of Ready For AI.

**Decision:** For normal `factory run` execution, Linear is authoritative. The run command pulls current Ready For AI issues and executes only the `Ticket` objects returned by that pull. It does not execute pre-existing `.factory/queue/*.md` files. The `pull-tickets` command may still write Markdown snapshots for inspection, and `factory run --no-pull` remains the explicit offline/manual mode that executes local queue files.

**Consequences:** A stale local ticket file cannot cause completed or non-ready Linear work to run again. Operators who need offline/manual reruns can still opt into local-file execution with `--no-pull`. The executor remains testable with hand-written ticket files via `factory run-ticket` and the local queue mode, but the default path now matches the architecture: Linear decides what is runnable.

---

## ADR-015: Per-file-type recipes are scaffolded at bootstrap when a matching file exists

**Status:** Accepted — supersedes the "recipes added incrementally, not pre-scaffolded" portion of ADR-013

**Context:** ADR-013 specified that `.ai/recipes/<area>/<task>.md` files be added incrementally as patterns repeat, to avoid empty stubs polluting agent context. The Hello Patient take-home was scaffolded under that rule (HEL-1) with only `.ai/recipes/ai-structure.md`. Subsequent tickets (HEL-2 added models; HEL-3 added routes/services/schemas/tests; HEL-4 added components/lib/types) each created new instances of canonical file types without any recipe to read or update. The result: no enforcement of conventions across tickets, no place to record the pattern decisions made along the way, and a self-review pass (HEL-6) that retroactively had to scaffold ten recipe files at once.

The original concern that led to ADR-013 — empty stubs polluting context — turned out to be the wrong concern. The actual failure mode is: when a recipe doesn't exist, the convention isn't documented anywhere, and the next ticket reinvents it. Empty stubs are bad; **no recipe at all is worse**.

**Decision:** When `/ai-files` runs on a repo, it scaffolds `.ai/recipes/<area>/<task>.md` for every canonical file type that has at least one matching file already in the codebase. Recipes are populated from the patterns actually in use — never with TODO stubs. The "no empty stubs" rule from ADR-013 remains in force; it's now expressed as "skip recipes for absent file types," not "defer all per-file-type recipes."

Future tickets that add a file of a canonical type that doesn't yet have a recipe create that recipe as part of the ticket's own scope. The `/ticket` command enforces this: any ticket adding or substantially modifying a file of a canonical type must include the matching recipe path in both `## Recipes` and `## Scope Paths`.

The starting set of canonical file types lives in the body of `.claude/commands/ai-files.md`. Backend types: `model`, `route`, `service`, `schema`, `testing`, `seed`, `migrations`. Frontend types: `page`, `component`, `hook`, `lib`, `form`, `types`. The list is extensible per repo when a repo uses shapes not on this list.

**Consequences:** Newly bootstrapped repos get a richer, immediately-useful set of recipe files. Each ticket touching a canonical file type now has a documented pattern to follow and a place to record evolution. The empty-stub concern is addressed structurally by the "at least one matching file" gate — no file type, no recipe. Existing repos scaffolded under ADR-013 (currently only the Hello Patient take-home) need a one-time retroactive scaffold; see HEL-6 for that work in the take-home repo.

## ADR-016: Recipe names are discovery-driven from the codebase's own vocabulary

**Status:** Accepted — supersedes the "starting set of canonical file types" paragraph in ADR-015

**Context:** ADR-015 enumerated a starting set of canonical file types (backend: model, route, service, schema, testing, seed, migrations; frontend: page, component, hook, lib, form, types) in the body of `/ai-files`. The list is shaped by web-app conventions and only loosely fits other domains: a game repo has sprites and scenes, a data pipeline has DAGs and transforms, infra has modules and stacks. Worse, the list invited two kinds of drift even inside web apps. First, the executor on HEL-6 picked `endpoint.md` over `router.md` because it was reasoning from what the file *contains* (endpoints) rather than what the file *is* (a router file in `routers/`). Second, the executor picked `api-client.md` over `lib.md` for a single-purpose file in `frontend/lib/` — which was the right call, but wasn't authorized by the rigid list. The rule needed to teach the executor when to follow the directory and when to follow the role, without reinventing the list per repo.

**Decision:** Recipe files are named after **what the file IS** in the codebase's own structural vocabulary, applied in this order:

1. **Directory name first.** A pluralized directory becomes the singular recipe name (`routers/` → `router.md`, `services/` → `service.md`, `sprites/` → `sprite.md`, `dags/` → `dag.md`).
2. **Single-purpose file inside a generic directory** takes the file's role, not the directory (`frontend/lib/chatApi.ts` alone in `lib/` → `api-client.md`, not `lib.md`).
3. **Framework terminology when no directory signals it** (SQLAlchemy "models" → `model.md`, Phaser "scenes" → `scene.md`).
4. **Reject renaming based on contents.** Routers contain endpoints; the file is still a router. Models contain fields; the file is still a model.

`/ai-files` no longer enumerates a fixed canonical set. It describes the rule, then provides worked example sets for four common repo shapes (web app, game, data pipeline, infra) as illustrations of how the rule produces a real recipe list. The actual recipe set for any given repo comes from applying the rule against what is on disk.

Two enforcement points support the rule:

- **`/run` Step 4** — before writing any file whose type matches an existing recipe, the executor reads `.ai/recipes/<area>/<task>.md` and conforms. If the ticket adds the first file of a previously-unseen recurring type, the executor creates the recipe inline, named per the rule.
- **`factory create-issue`** — emits a soft warning (stderr, never blocks) when a ticket's `## Scope Paths` touch an `<area>/` directory that already has a per-file-type recipe, but the recipe's path is not listed in scope. Implemented in `factory.ticket.find_scope_recipe_mismatches`. Detection-only: any false positives are ignored at human review.

**Consequences:** Recipe scaffolding works for non-web repos without per-domain code changes. The HEL-6 `endpoint.md` mistake is structurally ruled out — the rule now authorizes the executor's good naming instinct (`api-client.md`) and rejects its bad one (`endpoint.md`). The soft warning catches scope/recipe mismatches at ticket-creation time rather than waiting for an executor or reviewer to notice. The rigid type list in ADR-015 is no longer load-bearing — `/ticket` and ADR-015 are updated to refer to "recurring file-type patterns" + the naming rule rather than enumerating types.

## ADR-017: OpenCode + ollama as a third local executor tier

**Status:** Accepted

**Context:** Per ADR-009 the factory falls back from Claude to Codex when the metered provider exhausts. When both exhaust, the session ends and remaining queued tickets wait for the next quota window. For an always-on host processing a heavy backlog (the gaming-PC target), this leaves throughput on the table — hours of compute that the factory cannot use. A free, unmetered third tier closes the gap.

Three open-source coding-agent options were investigated: OpenCode (SST), OpenHands, and Aider, each driven by ollama serving a coding model locally. OpenCode won on three counts: (a) it has a non-interactive `opencode run` subcommand with `--format json` event streaming, mirroring the contract the codex provider already uses; (b) it speaks OpenAI-compatible API to ollama, the same surface other tools use; (c) its CLI flags drop cleanly alongside the existing claude/codex provider modules.

Three silent-failure cliffs were found during validation, none of them documented upfront. All three must hold or the model emits text describing tool calls instead of invoking them, and OpenCode exits 0 with no file changes — looking like the agent simply chose not to act. (1) The model must advertise `tools` capability — confirmed via `ollama show <model>`. Qwen2.5-Coder does not; Qwen3 and gpt-oss do. (2) The model must be registered in `~/.config/opencode/opencode.json` with `"tools": true`. (3) `num_ctx` must be ≥16384 — the ollama runtime default of 4096 truncates the tool schema before the model sees it. The fix is a Modelfile variant (`FROM <model>\nPARAMETER num_ctx 16384`).

Hardware ceilings are real and surfaced during validation. Models that emit OpenAI-compatible `tool_calls` AND fit a 16 GB Mac are scarce: `qwen3:8b-16k` (~5 GB resident) works comfortably; `gpt-oss:20b` (~13 GB) triggers severe swap thrashing on a 16 GB unified-memory machine (confirmed by hitting it on the dev Mac). Hosts with ≥24 GB can run `gpt-oss:20b` or `qwen3-coder:30b` for higher-quality diffs.

**Decision:** Add `opencode` as a third executor provider after `claude` and `codex` in `manifest.yaml`'s `executor_providers`. Implementation in `src/factory/providers/opencode.py` follows the codex.py shape: spawn `opencode run --format json --dir <repo> --dangerously-skip-permissions -m ollama/<model>`, stream-parse events for tokens / output / errors / tool_use, return an `AgentResult`. Default model is `ollama/qwen3:8b-16k`, overridable via the `OPENCODE_MODEL` environment variable.

The three silent-failure rules are encoded in the provider's module docstring so future Claude sessions reading the file get the warning. `scripts/bootstrap.sh` handles installation on macOS (Homebrew path) and Debian/Ubuntu Linux (apt + curl-installer path), including pulling the base model and creating the 16k Modelfile variant.

`QuotaTracker` treats opencode as a normal provider entry, but it never sets `usage_limit_hit` — the local tier is always available. If all three providers fail or are unreachable, the existing exhausted-session flow runs.

**Consequences:** Throughput unblocked when claude+codex exhaust on heavy backlogs. The local tier is *capable* of feature work but only when paired with per-file subtask decomposition (ADR-018) — without that, an 8B model produces garbage on real tickets and trips the scope/test/secret gates. Bootstrap goes from ~5 min to ~15 min on a fresh host (multi-GB model download dominates). On 16 GB Macs the viable model ceiling is 8B; ≥24 GB hosts unlock the 20B+ tier for better diffs.

## ADR-018: Per-file subtask decomposition

**Status:** Accepted

**Context:** Tickets historically ran as a single agent shot: build a prompt with the full AC and scope, hand it to claude (or codex on fallback), check the diff against scope, run tests, open PR. This works for tickets up to roughly 5–6 acceptance criteria touching a few files. Past that, single-shot execution degrades: the agent loses track partway through a long AC list, designs inconsistent shapes across files, and exits with a sprawling diff that trips the scope check or breaks tests in ways the auto-repair step cannot untangle. THM-21 (apply patterns to 9 modules) and THM-12 (vendors rolodex spanning schema → manager → service → routes → web pages) were the breakers.

Three observations pushed toward decomposition. First, smaller per-call agent context produces better outputs from every tier — claude included, not just the local tier introduced in ADR-017. Second, the local tier is fundamentally incapable of doing a 9-AC ticket in one shot, so it'd be permanently sidelined without a way to feed it bite-sized work. Third, a human engineer building one of these tickets does it one file at a time, accepting temporarily-broken state between commits and running tests only at the end. The factory was bundling design and implementation into one agent invocation; the natural decomposition is **design once, then execute mechanically**.

Two splittings were considered and rejected. **Multiple Linear tickets per feature** (one PR each) fragments architectural intent across PRs and adds bookkeeping overhead. **Execution-time chunking** (one ticket, agent runs in N segments) leaves each segment's agent blind to prior segments' design reasoning — it only sees their diffs — and failures partway through leave the PR in a half-built state that's hard to untangle.

What survived: **decomposition at ideation, sequential execution under one PR.** All the hard reasoning happens once during `/ideate` and `/ticket`. Subtask bodies carry the design (exact column names, function signatures, branching algorithms in pseudocode, layout grids, copy strings). Each subtask is one file. The runner executes each subtask as its own agent call but tests only once at the end of the whole ticket. One PR per ticket, one diff to review, but the agent never juggles more than one file's worth of design at a time.

**Decision:** Tickets carry a `## Subtasks` section, parsed by `src/factory/ticket.py` into a `Subtask` dataclass (`id`, `title`, `files`, `changes`, `recipe`, `depends_on`). Each subtask is markdown of the form:

```
### N. <imperative title>
- Files: <one or two paths>
- Recipe: <.ai/recipes/...>
- Depends on: <(none) | comma-separated ids>

<changes paragraph or code block, detailed enough that a small local
model can execute mechanically>
```

When `ticket.subtasks` is non-empty, `_run_subtask_loop` in `src/factory/runner.py` executes them sequentially. Each subtask gets its own agent invocation with a prompt containing: (a) project rules, (b) the overall ticket AC as context, (c) this subtask's spec, (d) the referenced recipe content loaded inline, (e) lists of completed and remaining subtasks, (f) the current `git diff HEAD` so the agent sees prior subtasks' work. The prompt explicitly instructs the agent to touch only listed files, not commit, not run tests, and stop. Failure (non-zero exit, timeout, usage-limit) stops the loop, preserves the branch, and propagates to the existing failure handlers. After all subtasks complete, the existing install/tests/secret-scan/commit/PR flow runs once.

The `/ticket` and `/ideate` command prompts encode the decomposition discipline. Subtasks reference recipes, not files — no exemplar field, because exemplar pointers drift and erode recipe quality. Missing recipe → prepend a recipe-creation subtask before the consuming one; the recipe catalog grows organically as features are decomposed. Per-file is the granularity floor; per-function loses too much semantic context. **If executing a subtask would require the agent to make a design decision, the decomposition is not done** — push the decision into the subtask body until execution is mechanical.

Tickets without `## Subtasks` continue to run on the existing single-shot path. Backward compatible.

**Consequences:** All three executor tiers (claude, codex, opencode) get smaller per-call contexts and produce more focused diffs. The local tier (ADR-017) becomes viable for real feature work because the reasoning is upstream. Ideation becomes more substantive work — subtask bodies are longer and require reading the relevant recipe files during the conversation, not just naming them. Failure recovery is fail-fast: a broken subtask aborts the ticket; no cross-subtask revision in v1. The factory's atomic-PR guarantee is preserved — one ticket, one branch, one PR — but the agent-side execution is no longer monolithic. The `tier_hint` field exists on the `Subtask` dataclass for future use but is not exercised by the current `/ticket` recipe prompt: every subtask defaults to local-first via the standard fallback order.

---

## ADR-019: Dependent tickets run as chains on one branch / one PR

**Status:** Accepted

**Context:** Today the factory runs one ticket per PR per branch. Tickets with a declared `## Depends On` block until the dependency PR merges to main, at which point the dependent ticket can be queued for the next run. For an always-on host processing a backlog of dependent tickets — a feature that spans N PRs by design — this means N round-trips: queue 1, wait for merge, queue 2, wait, etc. The user gatekeeps by promoting tickets in order in Linear; nothing in the factory enforces or accelerates the dependency relationship. `Depends On` has been parsed in the `/ticket` recipe format but explicitly noted as "for the factory to enforce it later" in the code comments.

ADR-018 (subtask decomposition) showed that smaller per-call agent context plus a shared branch produces better results than monolithic single-shot execution. The same shape applies one level up: a chain of dependent tickets is the *ticket-level* equivalent of a sequence of subtasks. Each ticket is still a real, independently-reviewable unit of work — but bundling them on one branch removes the merge-roundtrip friction and gives every executor tier (including the local tier from ADR-017) sustained context across the feature.

Three policy decisions had to be made up front. **Trigger:** always-chain when deps are in queue, no opt-in flag — simplest mental model, matches the stated user intent. **Mid-chain failure:** abort the whole chain, preserve the branch with the successful prior commits, no PR — partial PRs are non-atomic and create weird review states. **Cross-repo deps:** refuse to run the dependent ticket entirely — one branch lives in one repo, and silently dropping a declared dep would be wrong.

**Decision:** Tickets carry a `## Depends On` section parsed into `Ticket.depends_on: list[str]`. A new module `src/factory/chains.py` groups the queue into dependency chains via `group_into_chains(tickets, merged_ticket_ids, max_depth)`: topo-sorts each connected component, detects cycles (raises `ChainCycleError`), refuses cross-repo chains (returns them in `skipped_cross_repo`), enforces max chain depth of 5 (chains beyond that split into independent chains).

A new entry point `run_chain(chain, repo, ...)` in `src/factory/runner.py` executes a chain. Single-ticket chains delegate verbatim to `run_ticket` so the existing path is unchanged. Multi-ticket chains create one shared branch `factory/<first-id>-chain-<uuid>`, then loop per-ticket: build prompt (single-shot or subtask-loop per ADR-018) → run executor → scope check → install → tests (with one repair attempt) → write memory file → commit `<TICKET-ID>: <title>`. On per-ticket failure, the uncommitted changes are discarded so the chain branch is left with only the successful prior commits, the chain aborts, and the caller surfaces the failure. After all tickets succeed, secret scan runs once over the whole chain; on leaks every commit is undone and the branch is deleted; otherwise push and open one PR titled `<FIRST-ID> + N more` with a body listing each ticket's AC.

The orchestrator groups its `work_items` into chains before the run loop, then iterates chains. Per-ticket Linear write-back is unchanged — each ticket gets its own comment with the shared PR URL and its own state transition. Dependency satisfaction is verified strictly against Linear: for each declared dep that is NOT itself in the queue, the orchestrator calls `LinearClient.is_issue_merged(dep_id)`, which checks Linear's workflow `state.type` field. Only `completed`-typed states (the workspace-agnostic "Done" category) count as merged. Anything else — `backlog`, `unstarted`, `started` (which includes "In Review" with an open PR), `canceled`, `triage` — refuses the dependent ticket with an `ERROR` message naming the unsatisfied dep. The fallback for missing Linear access (dry-run or no API key) is trust-the-user with a warning, since strict verification is impossible without the API.

**Consequences:** No more manual re-queue between dependent ticket merges; a chain of N tickets runs as one continuous session. PRs grow with chain length (N commits, one per ticket) — the max-depth-5 cap is the lever if review fatigue sets in. Failure semantics are atomic at the chain level: success means all N tickets shipped together; failure means none of them are in a PR (the branch is preserved with the successful commits for inspection, but no PR exists to review). Cross-repo deps must be coordinated manually — refused with a stderr error. The local tier (ADR-017) benefits the most because sustained context across related tickets keeps the smaller model on the same wavelength. The atomic-PR guarantee from ADR-018 generalizes: one chain, one branch, one PR — agent-side execution within a chain is multi-ticket but human-side review is still one PR.

---

## ADR-020: The factory provisions new target projects (Linear team + GitHub repo)

**Status:** Accepted

**Context:** Until now the factory assumed every target project already existed. The Linear team was created by hand in the UI — the `linear.py` spike note literally instructs "Create the team in Linear → Settings → Teams first." The GitHub repo was created by hand. `manifest.yaml` was hand-edited to register it. `factory setup-team` only added the "Ready For AI" label to an already-existing team, erroring out if the team was missing. The Architecture "Boundaries" table assigned "team → repo mapping" to Linear's native GitHub integration and treated team/repo *existence* as outside the factory's concern. Onboarding a new project therefore meant three manual, easy-to-mis-key steps (create team, create repo, edit manifest) before the factory could do anything with it.

The factory is already authenticated for both sides: it shells out to `gh` for every GitHub operation (PR create/list/checkout/api) and talks to the Linear GraphQL API with a personal key. Creating a team and a repo are the same kinds of calls it already makes — there was no capability gap, only a missing entry point.

**Decision:** Add a single idempotent command, `factory new-project` (driven by the `/new-project` slash command), that provisions a target project end to end: (1) ensure the Linear team exists — `LinearClient.create_team(name, key)` wraps the `teamCreate` mutation, called only when `get_team_id` returns nothing; (2) ensure the "Ready For AI" label exists (the same logic `setup-team` uses); (3) create the GitHub repo via `gh repo create <owner>/<name> --public|--private --add-readme` — the `--add-readme` seeds a single commit so `main` exists and the repo is immediately PR-able, otherwise empty; (4) clone it to `repos/<key>/`; (5) register the repo in `manifest.yaml` by text-insertion under `repos:` (not a yaml round-trip, so the file's comments survive). Every step checks for the existing artifact first, so the command is safe to re-run and degrades to a no-op. A `--dry-run` flag prints the plan without side effects. Repo visibility is an explicit choice (`--visibility`), defaulting to private.

This reverses the prior assumption that team/repo creation is out of scope: the factory now owns project *provisioning*, not just per-ticket execution. Scaffolding the repo's actual contents is deliberately NOT part of this command — that stays a normal ticket (e.g. a project's T1), so provisioning and code generation remain cleanly separated and the empty repo flows through the existing `/ticket` → `/run` path like any other work.

**Consequences:** A new project goes from nothing to factory-ready in one command instead of three manual steps, with no chance of mis-keying the team↔repo↔manifest mapping. The factory's authentication surface now includes repo and team creation: the `gh` token needs `repo` scope (already present), and the Linear key must belong to a workspace whose plan permits programmatic team creation — the Free tier caps team count, in which case team creation fails and the user makes the team in the UI, after which the command picks it up idempotently. The `manifest.yaml` text-insertion is intentionally simple (insert under the first `repos:` line); it assumes the conventional manifest shape and is covered by a unit test. The Architecture "Boundaries" entry that delegated team/repo existence entirely to Linear/GitHub is superseded for the *creation* path; the *mapping* still lives in Linear's native integration once both sides exist. Visibility is a deliberate per-project decision rather than a default-public convenience, because target repos routinely carry integration details for the systems they automate.

---

## ADR-021: Command vs native skill vs recipe — explicit selection over auto-invocation

**Status:** Accepted

**Context:** The word "skill" had accumulated three incompatible meanings in and around the factory, which made the system hard to explain and reason about:

1. **Slash commands** in `.claude/commands/` that the user types (`/ticket`, `/run`, `/ideate`, `/ai-files`, `/new-project`). The Claude Code picker now labels every slash-invocable entry `Skill: /x (project)`, so commands *display* as "skills" even though they are user-triggered and never auto-invoked.
2. **Native skills** — `.claude/skills/<name>/SKILL.md`, auto-invocable by the harness via description match (progressive disclosure). This is now a cross-tool standard: both Claude Code and Codex read `SKILL.md` (Codex deprecated its custom prompts in favor of skills).
3. The factory's own **per-file-type pattern docs**, originally `.ai/skills/<area>/<task>.md`, which nothing auto-invokes — the control plane reads them by path and injects them. These were renamed `.ai/skills/` → `.ai/recipes/` (the prior ADRs were reworded in place as if "recipe" had always been the term).

**Decision:** Treat the three as distinct concepts with distinct names:
- **Command** — an explicit, user-typed slash workflow. Consequential factory operations stay commands *because* they must be triggered deliberately (auto-firing `/ticket` or `/run` would be dangerous).
- **Native skill** (`SKILL.md`) — an auto-invocable capability. The factory uses **none** of these in its execution path.
- **Recipe** (`.ai/recipes/<area>/<task>.md`) — a procedural, per-file-type how-to that the control plane explicitly selects per subtask and injects into the executor prompt. Provider-agnostic. The meta-recipe `.ai/recipes/recipe.md` defines how to author one.

The factory deliberately **selects context explicitly rather than relying on auto-invocation**, because it is an unattended, multi-provider pipeline:
1. Auto-invocation is non-deterministic and unauditable — measured right-skill activation tops out around ~79% even with explicit instructions, and agents exhibit no need-aware invocation (they reach for skills at similar rates whether or not one is required).
2. The local executor tier (Ollama via OpenCode, see ADR-017) has **no** skill mechanism at all, so path-referenced recipes are the only form portable across Claude / Codex / local.
3. The decision of *which* pattern applies belongs at planning time (`/ticket`, smart tier), not execution time (any tier, see ADR-018).

For automatic guardrails (branch checks, manifest validation), prefer **deterministic hooks** (`PreToolUse`/`Stop`, which the model cannot bypass) over auto-invoked skills.

**Consequences:** The factory is, in current terms, a **deterministic context-engineering control plane** — context is engineered and injected, not discovered at the model's discretion. Recipes remain the harness-neutral source of truth under `.ai/`; native skills stay available as an interactive convenience but are not part of the execution path. Because the ecosystem has converged on `SKILL.md` + `AGENTS.md` as shared formats, recipes could later double as `SKILL.md` files (one artifact, two access paths) if a single-harness interactive use ever warrants it — but that does not change the execution path, which stays explicit. The recipe concept goes by different names across the industry — Cursor "rules", Copilot "instructions", Anthropic/Codex "skills", Ramp / playbooks.com "playbooks"; the factory's one principled divergence is forbidding exemplar-file pointers in recipes (they drift) in favor of encoding the pattern itself (see ADR-013 / ADR-015 / ADR-018).


---

## ADR-022: Max chain depth raised from 5 to 10

**Status:** Accepted (amends ADR-019)

**Context:** ADR-019 capped dependency chains at 5 tickets as a review-fatigue lever, with the note that 6+ chained tickets "probably wants to be one ticket with subtasks." The first real greenfield product (billy-ai's parts-catalog parser) produced a legitimate 6-ticket linear chain — scaffold → output layer → web pipeline → PDF pipeline → GUI → packaging — where each ticket is a genuinely independent, reviewable unit and merging them into fewer mega-tickets would fight ADR-018's per-file subtask sizing. The cap also turned out to guard more than review fatigue: `group_into_chains` splits an over-cap component *arbitrarily* at the boundary, and the split tail chain runs in the same pass but branches from the default branch **before** the head chain's PR merges — so its in-queue dependencies are not on its branch and it fails (or worse, half-works). Splitting is therefore not a safe overflow behavior; the practical rule is "keep the whole queued component within the cap."

**Decision:** Raise `DEFAULT_MAX_CHAIN_DEPTH` from 5 to 10 in `src/factory/chains.py`. `group_into_chains` keeps its `max_depth` parameter, and the arbitrary-split overflow behavior is unchanged (now documented with the stale-base warning in the module docstring and the `/ticket` command doc). The review-fatigue concern is downgraded from a hard mechanical cap to operator judgment: the human still gatekeeps chain size by choosing what to promote to "Ready For AI" together.

**Consequences:** A 6–10 ticket feature can one-shot as a single branch/PR with one commit per ticket. The atomic-failure surface grows with chain length — one mid-chain failure aborts the PR for everything queued — which the operator accepts when promoting a long chain at once. The known split-tail hazard (tail chains cut from a stale base) still exists past 10; a proper fix (defer tail chains to the next run instead of running them against a stale base) is left for a future ADR if a >10 chain ever becomes real.

---

## ADR-023: Mid-chain failure ships a partial PR instead of no PR

**Status:** Accepted (amends ADR-019)

**Context:** ADR-019 chose atomic failure semantics for chains: a mid-chain failure preserved the branch with the successful commits but opened no PR, on the grounds that "partial PRs are non-atomic and create weird review states." With the depth cap raised to 10 (ADR-022) and real 6-ticket chains queued (billy-ai parser), that stance inverts: a failure at ticket 5 of 6 stranding four fully-tested, individually-committed tickets on an unpushed branch is strictly worse than reviewing them. Each chained ticket is already an independently-reviewable unit with its own commit and its own AC — a PR containing a clean prefix of the chain is not a "weird review state", it is exactly the PR that would have existed had the operator queued fewer tickets.

**Decision:** On mid-chain failure, `run_chain` now ships the successful prefix as a **partial PR** via `_finish_partial_chain`: secret-scan the branch, push, and open a PR titled with a `[partial chain — <ID> failed]` marker whose body names the failed ticket and the still-queued tail. The PR URL is backfilled onto only the *succeeded* per-ticket results, so Linear write-back attributes the PR to the tickets it actually contains; the failed ticket gets the normal failure write-back and stays in the queue along with the unattempted tail. Edge cases: a failure at the **first** ticket has nothing to ship — branch preserved, no PR (unchanged); a secret-scan hit on the partial prefix rolls back the commits and deletes the branch (mirroring the full-chain scan path), and additionally **downgrades the rolled-back per-ticket successes to failures** so the orchestrator cannot write back "PR opened" for commits that no longer exist. Dry runs preserve the partial branch without pushing.

**Consequences:** A long chain now degrades gracefully: merge the partial PR, and once its tickets reach Done in Linear the next `factory run` picks up the failed ticket (its deps now satisfy `is_issue_merged`) and continues the chain from the failure point on a fresh branch — no work is redone. The operator's recovery loop changes from "fix, re-run everything" to "merge what shipped, fix, re-run the remainder." The cost: the chain's one-PR-per-feature property is no longer guaranteed on failure — a feature can land across two (or more) PRs, each internally clean. Note the resume depends on the partial PR actually merging; an unmerged partial PR blocks the tail exactly like any unmerged dependency.

---

## ADR-024: Per-ticket memory is deterministic from a required `## Summary`; the index is a separate concern

**Status:** Accepted (supersedes the memory-writing behavior described in ADR-013's memory notes and the fallback in `write_run_memory`)

**Context:** Per-ticket memory files and the `MEMORY.md` index were produced two different ways depending on the executor. The intended path had the executor (Claude) author its own memory file and leave `MEMORY.md` alone, with a separate session PR rebuilding the index. But the Python `factory run` path also carried a fallback (`write_run_memory`) that fired whenever the executor did not write a memory file — and it (a) emitted boilerplate content (`**Why:** Ticket was marked Ready For AI`) with no real rationale, and (b) edited `MEMORY.md` inline on the *ticket* branch. Because Codex (the common fallback provider) does not reliably follow a trailing "write this file, don't touch that one" instruction, the fallback fired on essentially every ticket of the billy-ai BIL-4…17 batch. Result: contentless memory, and every ticket PR modified the one shared index file → the whole batch collided on `MEMORY.md` and needed manual conflict resolution. The root problem was an unstated dependency on the executor being Claude, plus a fallback that contradicted the redesign.

**Decision:** Make per-ticket memory **provider-independent and deterministic**, sourced from the ticket itself:
1. **Tickets carry a required `## Summary`** (2–4 sentences: what the ticket delivers + the key decision), parsed by both `ticket.parse_ticket` and `sync._issue_to_ticket` and validated at pull time. It is distinct from `## Context` (the "why"/background).
2. **The factory writes the per-ticket memory file itself** via `git_ops.write_ticket_memory`, deterministically from `ticket.summary` + the real changed-files list — one golden path, **no fallback**. The runner writes it at both execution sites (`run_ticket`, `_run_one_ticket_on_chain_branch`) and verifies it (`_write_and_verify_memory` raises if missing/malformed rather than degrading). The executor is told **not** to write memory at all. The `/run` command (run.md Step 8) produces the identical file from the same `## Summary`.
3. **Ticket PRs never touch `MEMORY.md`.** The index is rebuilt separately from each file's frontmatter `description` (first sentence of the summary). Placement is conditional (`create_memory_pr`): if the run produced exactly one ticket/chain PR for a repo and no index PR is open, the index is folded into that same PR; otherwise a single separate `factory/memory-*` PR is used/refreshed.

**Consequences:** Memory content no longer depends on which provider ran, or on the executor honoring a trailing instruction — the "why" comes from the ticket authored at `/ticket` time. The cross-PR `MEMORY.md` conflict class is eliminated because the invariant `create_memory_pr` always assumed (ticket PRs don't touch the index) is now actually enforced. A single-PR run ships its catalog update in the same PR instead of spawning a second one. Cost: `/ticket` must now write an accurate Summary (a required field; pull-tickets rejects tickets without one), and the memory reflects the ticket's stated intent rather than any post-hoc discovery the executor might have made — an acceptable trade for reliability, and richer than the boilerplate it replaces. The legacy `write_run_memory` is removed.
