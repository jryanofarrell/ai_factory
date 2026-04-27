# Phase 4 — Hardening

## Brief for Claude Code

You are building Phase 4 of `ai_factory`. Phases 0–3 produced a closed loop: `factory run` pulls tickets from Linear, executes them, and writes results back. Phase 4 adds the safety rails that make this loop trustworthy enough to use in earnest, including over the course of a longer run where the user is not watching every step.

This phase is wider than the previous ones — it adds five distinct safety features. Treat each as its own sub-feature with its own acceptance criterion. Implement them in the order listed; later features depend on earlier ones.

**Before starting, read these existing files in this order:**
- `CLAUDE.md`
- `docs/DECISIONS.md`
- `phases/PHASE_1_SPEC.md`, `PHASE_2_SPEC.md`, `PHASE_3_SPEC.md`
- `src/factory/runner.py`, `orchestrator.py`, `git_ops.py`, `linear.py`

## Goal

After Phase 4, `factory run` is hardened against the most likely failure modes:

- Agents straying outside the ticket's scope (scope check).
- Agents looping or burning excessive cost (budget caps).
- Stale branches accumulating from failed runs (idempotency).
- Loss of audit trail when something does go wrong (structured logs).
- Inadvertent commits of secrets the agent reads from the target repo (secret scan).
- Need to inspect a run without actually pushing (dry-run mode).

## Sub-features (in implementation order)

### 1. Scope check

After the agent finishes editing files but **before** the commit step in `runner.py`:

- If the ticket's `scope_paths` is non-empty, run `git diff --name-only HEAD` and compare each changed path against the ticket's glob list.
- Any path not matched by at least one glob is a violation. Collect all violations.
- If there are any violations, abort the ticket: log the violating paths, delete the branch, and write back to Linear with a "scope violation" failure.
- If `scope_paths` is empty (unspecified), skip the check (back-compat with hand-written tickets).

Use `pathspec` (the same library Git uses for `.gitignore` matching) for glob semantics. Don't roll your own.

### 2. Budget caps

Two enforcement axes: wall-clock time and tokens (if Phase 3's spike confirmed `claude -p` emits usage).

- **Time cap**: spawn the `claude -p` subprocess with a watchdog thread. If `budget_minutes` elapse, send SIGTERM, wait 5 seconds, then SIGKILL. Mark the ticket failed with reason "exceeded time budget". Clean up the branch.
- **Token cap**: if `claude -p` supports `--max-tokens` or equivalent, pass `budget_tokens`. Otherwise, parse incremental usage from its output if the spike showed it's emitted, and kill the subprocess when exceeded. If neither is feasible, document this in `runner.py` and skip the token cap for now (time cap is the load-bearing one anyway).
- The defaults from `LINEAR_SCHEMA.md` (`50000` tokens, `30` minutes) apply when the ticket doesn't specify.
- Always log the cap that triggered the kill in the failure comment to Linear.

### 3. Branch hygiene / idempotency

At the start of `factory run`, before pulling tickets:

- List remote branches matching `factory/*` on each registered repo (`git ls-remote origin 'refs/heads/factory/*'`).
- For each, check the commit date. If older than the configured threshold (`stale_branch_days`, default 7) AND no open PR points at it (`gh pr list --head <branch> --state open --json number`), delete it remotely (`git push origin --delete <branch>`).
- Local branches that no longer exist remotely should be pruned (`git fetch --prune`).

Add a `--no-cleanup` flag to `factory run` that skips this step (useful for debugging).

### 4. Structured logs

Every ticket run produces a JSON log at `logs/<YYYY-MM-DD>/<ticket-id>.json` with at minimum:

```json
{
  "ticket_id": "THMS-42",
  "started_at": "2026-04-27T22:14:00Z",
  "ended_at": "2026-04-27T22:15:52Z",
  "duration_s": 112,
  "result": "succeeded",
  "pr_url": "https://github.com/.../pull/17",
  "branch": "factory/thms-42-a1b2c3d4",
  "files_changed": ["CHANGELOG.md"],
  "scope_check": {"passed": true, "violations": []},
  "budget": {"tokens_used": 8421, "tokens_cap": 10000, "minutes_used": 1.87, "minutes_cap": 5},
  "cost_usd": 0.34,
  "exit_code": 0,
  "error": null
}
```

Failed runs include the relevant `error` field and a `reason` (one of `tests_failed`, `scope_violation`, `budget_exceeded`, `agent_no_changes`, `push_failed`, `pr_create_failed`, `unknown`).

Add `logs/` to `.gitignore`.

### 5. Secret scanning

After the commit step but **before** `git push`:

- Run `gitleaks detect --source . --staged` (or its equivalent for already-committed-but-not-pushed changes) in the target repo.
- If gitleaks reports any findings, abort the push: revert the commit (`git reset --hard HEAD~1`), delete the branch, and write back to Linear with a "secret-scan failure" comment listing the rule names that fired (not the secret values themselves).
- Document `gitleaks` as a new prerequisite in the README.

If `gitleaks` is not on PATH, log a warning but do not fail. The user can install it later; the rest of the pipeline shouldn't break.

### 6. Dry-run mode

Add a `--dry-run` flag to both `run-ticket` and `run`:

- The full pipeline executes through the agent, scope check, budget enforcement, test run, commit, and secret scan.
- The push step is skipped.
- The PR creation step is skipped.
- The Linear write-back is skipped.
- The branch and commit are preserved for inspection.
- The structured log records `result: "dry_run"`.

This gives the user a way to test ticket scoping, run fidelity, and agent output without producing PRs or touching Linear.

## Manifest additions

Add optional fields to the manifest schema (update `docs/MANIFEST.md`):

```yaml
version: 1
queue_dir: .factory/queue
stale_branch_days: 7        # NEW: cleanup threshold
secret_scan: true            # NEW: enable/disable per-workspace
repos:
  thms-platform:
    ...
```

Both default sensibly when omitted.

## Acceptance criteria

- [ ] **Scope**: a ticket with `scope_paths: [CHANGELOG.md]` whose agent edits `README.md` fails with a clear violation log; branch is deleted; Linear gets a "scope violation" failure comment.
- [ ] **Budget time**: a ticket with `budget_minutes: 1` whose agent runs longer is killed; branch is deleted; Linear gets a "budget exceeded" comment with the elapsed time.
- [ ] **Budget tokens**: token cap is enforced if technically feasible; otherwise documented as deferred with a clear comment in code.
- [ ] **Branch cleanup**: stale `factory/*` branches without open PRs are pruned at the start of `factory run`.
- [ ] **Logs**: every ticket run produces a JSON log at `logs/<date>/<ticket-id>.json` with all the listed fields.
- [ ] **Secret scan**: a deliberately introduced fake secret (e.g. AWS-style key prefix) in the agent's diff causes the push to be aborted and Linear to receive a comment.
- [ ] **Dry-run**: `--dry-run` produces an inspectable branch and a JSON log, but no PR and no Linear update.
- [ ] All existing Phase 3 acceptance criteria still pass.
- [ ] `MANIFEST.md` is updated with the new optional fields.
- [ ] `README.md` documents `gitleaks` as an optional prerequisite.

## Out of scope — do not build

- Re-trying within a single ticket run (no automatic retry — failures stay failed; the user retries manually by re-marking ready).
- Multi-ticket parallelism.
- Rate-limited runs (e.g. "max N tickets per day"). Add later if needed.
- Pre-execution test runs (running tests before the agent starts to confirm the baseline is green) — interesting but not in scope.
- Linear-side changes other than the existing comment/state writes.
- Anything in Phase 5 (ideation), Phase 6 (scheduling), or Phase 7 (multi-repo).

## Verification (manual, after Claude Code finishes)

For each of the six sub-features, deliberately construct a Linear ticket that exercises the failure path:

1. **Scope**: ticket with `scope_paths: [CHANGELOG.md]` whose acceptance criteria require touching `README.md`. Confirm scope violation, branch cleanup, Linear comment.
2. **Budget time**: ticket with `budget_minutes: 1` and instructions that imply long work. Confirm SIGTERM kill, branch cleanup, Linear comment.
3. **Branch cleanup**: manually create a `factory/junk-old` branch on the target repo with a backdated commit (`git commit --date="14 days ago"`) and no PR. Run `factory run`. Confirm the branch is deleted from the remote.
4. **Logs**: run any ticket. Open `logs/<today>/<ticket-id>.json`. Confirm all fields are present and accurate.
5. **Secret scan**: ticket whose acceptance criteria say "add a config file with `AWS_SECRET_ACCESS_KEY=AKIA...`". Confirm gitleaks fires, push is aborted, branch is deleted, Linear gets the comment.
6. **Dry-run**: run a normal ticket with `--dry-run`. Confirm the branch and commit exist locally, no PR was created, Linear is untouched, and `logs/<today>/<ticket-id>.json` records `result: "dry_run"`.
7. Re-run all the Phase 3 verification steps. They should all still pass.
