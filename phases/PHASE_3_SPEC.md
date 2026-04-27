# Phase 3 — Closed Loop

## Brief for Claude Code

You are building Phase 3 of `ai_factory`. Phases 0–2 produced the docs, the executor (`run-ticket`), and the Linear read path (`pull-tickets`). Phase 3 closes the loop: a single `factory run` command pulls ready tickets, executes each via the Phase 1 pipeline, and writes results back to Linear (PR URL as a comment, workflow state advanced to "In Review", cost and duration recorded).

After Phase 3, the user can mark issues "Ready for Agent" in Linear, run one command, and watch PRs appear with Linear updated to match.

**Before starting, read these existing files in this order:**
- `CLAUDE.md`
- `docs/DECISIONS.md`
- `docs/ARCHITECTURE.md`
- `docs/LINEAR_SCHEMA.md`
- `phases/PHASE_1_SPEC.md`
- `phases/PHASE_2_SPEC.md`
- `src/factory/linear.py` and `src/factory/sync.py` (the Phase 2 implementation)
- `src/factory/runner.py` (the Phase 1 implementation)

## Goal

After Phase 3, this command works:

```bash
uv run factory run
```

It pulls ready tickets, executes each, and updates Linear. Manual trigger; tickets are processed sequentially. A summary report is printed at the end.

## Spike first — verify what `claude -p` emits at end of run

Cost and token usage tracking depends on what `claude -p` actually prints when it finishes. Before writing the cost-capture code:

1. Run `claude -p "<a small task>"` in a scratch directory and capture the full stdout/stderr.
2. Note whether (and where) it prints token counts, cost, or session metadata at the end.
3. If structured output is available via a flag (e.g. `--output-format json`), prefer that.
4. Document what you found in a comment at the top of `src/factory/runner.py`.

If `claude -p` does not reliably emit cost data, capture wall-clock duration only in Phase 3 and treat token-based budget enforcement as a Phase 4 problem.

## Behavior — the pipeline, step by step

The `run` command must do the following:

1. **Run `pull-tickets`** as the first step (call the Phase 2 function directly; do not shell out to the CLI). Capture the list of newly-pulled ticket file paths plus any tickets already in the queue that haven't been processed.
2. **Build the work list** — every file in the queue directory that has not been marked done in this batch. (Batch tracking lives in a small JSON file at `.factory/runs/<timestamp>.json` so a re-run after interruption can pick up where it left off.)
3. **For each ticket in the work list, in order:**
   a. Capture start time.
   b. Invoke the Phase 1 `run_ticket()` function in-process (refactor `runner.py` if it currently only exposes the CLI command). Pass the ticket's `target_repo`. Capture the result: PR URL on success, or an error reason on failure.
   c. Capture end time and (if available) cost from the `claude -p` invocation.
   d. **Write back to Linear:**
      - On success: post a comment to the issue containing the PR URL, the wall-clock duration, and (if captured) the cost. Transition the issue's workflow state to "In Review" (or to the team's equivalent — query the team's available states by name).
      - On failure: post a comment with the error reason, the branch name (if one exists), and the duration. Transition the issue's workflow state to "Failed for Agent" (a new workflow state — document this in `LINEAR_SCHEMA.md` as part of this phase) or, if that state doesn't exist, apply the `factory:failed` label.
   e. Record the ticket's outcome in the batch JSON file.
4. **Failures are isolated** — a failure on ticket A does not abort processing of ticket B. Continue through the work list.
5. **At the end**, print a summary:
   ```
   Run complete: 4 succeeded, 1 failed.
   - THMS-42 → https://github.com/.../pull/17 (1m 52s, $0.34)
   - THMS-43 → https://github.com/.../pull/18 (3m 04s, $0.71)
   - THMS-44 → FAILED: tests failed; branch factory/thms-44-a1b2c3d4 preserved
   - THMS-45 → https://github.com/.../pull/19 (45s, $0.18)
   - THMS-46 → https://github.com/.../pull/20 (2m 10s, $0.42)
   Total: 7m 51s, $1.65
   ```
6. **On clean completion**, the queue files for processed tickets are moved to `.factory/queue/processed/<ticket-id>.md`. They are not deleted (auditability). Failed tickets stay in the main queue so re-running `factory run` retries them.

## Implementation notes

- New module `src/factory/orchestrator.py` for the `run` pipeline. `runner.py` retains the single-ticket pipeline.
- Refactor `runner.py` so its core function is callable as a library (`run_ticket(ticket: Ticket, repo: RepoConfig) -> RunResult`). The CLI command becomes a thin wrapper.
- Extend `src/factory/linear.py` with write methods: `comment_on_issue(issue_id, body)`, `transition_issue(issue_id, state_name)`, `apply_label(issue_id, label_name)`. All three should be idempotent where possible (a comment is always new; a transition to the current state is a no-op).
- Batch state file at `.factory/runs/<UTC ISO timestamp>.json`:
  ```json
  {
    "started_at": "2026-04-27T22:14:00Z",
    "tickets": {
      "THMS-42": {"status": "succeeded", "pr_url": "...", "duration_s": 112, "cost_usd": 0.34},
      "THMS-44": {"status": "failed", "reason": "tests failed", "branch": "factory/thms-44-..."}
    }
  }
  ```
- The orchestrator must be safe to interrupt with Ctrl-C: any in-flight ticket is marked `interrupted` and the run exits cleanly. Re-running `factory run` resumes from the next unprocessed ticket in the latest batch.
- A new flag `--no-pull` skips the `pull-tickets` step (useful for retrying just the queue without hitting Linear).
- A new flag `--ticket <id>` runs a single ticket from the queue and ignores the rest.

## Linear schema additions (update `docs/LINEAR_SCHEMA.md`)

- New workflow state: **"Failed for Agent"** in the "Started" or "Backlog" category. Issues land here when execution fails so the user can fix the ticket and remark it as "Ready for Agent".
- Document the standard transitions: `Ready for Agent → In Progress → In Review` (success path) and `Ready for Agent → In Progress → Failed for Agent` (failure path). Note that Phase 3 does not move issues to `In Progress` mid-run because Linear's UI handles in-flight states poorly for this use case; the factory transitions directly to the terminal state on completion.

## Acceptance criteria

- [ ] `factory run` on a workspace with N ready tickets produces up to N PRs and N updated Linear issues.
- [ ] Each Linear issue gets a comment with the PR URL, the wall-clock duration, and (if captured) the cost.
- [ ] Successful runs move the issue to "In Review" (or the team's named equivalent); failures move it to "Failed for Agent" or apply the `factory:failed` label as fallback.
- [ ] Failures in one ticket do not block remaining tickets.
- [ ] A summary report matching the format above is printed at the end.
- [ ] The run is resumable: if interrupted mid-batch, the next invocation skips already-processed tickets in the same batch.
- [ ] Processed queue files are moved to `.factory/queue/processed/`; failed ones stay in the main queue.
- [ ] `--no-pull` skips the Linear pull step.
- [ ] `--ticket <id>` runs a single ticket from the queue.
- [ ] `LINEAR_SCHEMA.md` is updated with the "Failed for Agent" state and the documented transitions.

## Out of scope — do not build

- Scope/file allowlist enforcement (Phase 4).
- Budget caps (Phase 4) — Phase 3 captures duration and (if available) cost for reporting only, not for enforcement.
- Stale-branch cleanup (Phase 4).
- Structured JSON logs per ticket (Phase 4).
- Concurrent / parallel ticket execution. Phase 3 is sequential; concurrency is a future concern with its own design.
- Local scheduling (Phase 6, deferred).
- Webhook-driven runs.

## Verification (manual, after Claude Code finishes)

1. In Linear, create three test issues in the THMS team. Make one a hello-world ticket that should succeed; one that should fail tests (e.g. introduces a syntax error); one with deliberately invalid metadata. Mark all three "Ready for Agent".
2. Run `uv run factory run`. Watch the orchestrator process them.
3. Confirm: two PRs are created (the success and the test-failure tickets — wait, the test-failure should NOT create a PR). Re-read step 3d above. Adjust your test setup so one ticket succeeds, one has tests fail (no PR, branch preserved), and one has invalid metadata (skipped at validation).
4. Confirm Linear: success → "In Review" with PR-URL comment; test-fail → "Failed for Agent" with explanation; invalid → not touched (failed at pull stage in Phase 2).
5. Confirm `.factory/runs/<timestamp>.json` exists with the per-ticket outcomes.
6. Confirm the success ticket's queue file moved to `.factory/queue/processed/`.
7. Run `factory run` a second time with no new ready tickets. Confirm the report says "0 succeeded, 0 failed" and Linear is not touched.
8. Mark the failed ticket "Ready for Agent" again, fix the underlying problem, and re-run `factory run`. Confirm it succeeds this time and the issue moves to "In Review".
