# Phase 3 spike findings (2026-04-27):
# claude -p --output-format json emits a single JSON object on stdout with:
#   total_cost_usd, duration_ms, result (text), usage (token counts)
#
# Phase 4 token cap note:
# The claude CLI does not expose a flag to enforce a total token budget mid-run.
# Token usage is captured post-run for reporting only. Wall-clock time cap (SIGTERM/SIGKILL)
# is the enforced budget mechanism. Token cap enforcement deferred.

from __future__ import annotations

import json
import subprocess
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer

from .git_ops import (
    check_scope,
    check_tools,
    commit,
    create_branch,
    create_pr,
    delete_branch,
    detect_install_command,
    detect_test_command,
    ensure_stack_ready,
    get_changed_files,
    has_changes,
    is_dirty,
    push,
    run_shell_command,
    secret_scan,
    sync_repo,
    undo_commit,
    write_run_memory,
)
from .manifest import RepoConfig, load_manifest
from .providers import AgentResult
from .providers import claude as claude_provider
from .providers import codex as codex_provider
from .providers import opencode as opencode_provider
from .quota_tracker import QuotaTracker
from .ticket import Subtask, Ticket, parse_ticket


@dataclass
class RunResult:
    ticket_id: str
    success: bool
    pr_url: str | None = None
    branch: str | None = None
    duration_s: float = 0.0
    cost_usd: float | None = None
    error: str | None = None
    reason: str | None = None
    files_changed: list[str] = field(default_factory=list)
    scope_violations: list[str] = field(default_factory=list)
    tokens_used: int | None = None
    exit_code: int | None = None
    dry_run: bool = False
    usage_limit_hit: bool = False
    provider_used: str | None = None


@dataclass
class ChainResult:
    """Result of running a multi-ticket chain on a shared branch (ADR-019).

    per_ticket: one RunResult per ticket the chain attempted, in execution
        order. On mid-chain failure, the failed ticket is the last entry and
        any not-attempted tickets are omitted.
    branch: the shared chain branch (preserved on failure for inspection).
    pr_url: set only when all chained tickets succeed and a PR is opened.
    success: True iff every ticket in the chain ran to a clean commit AND
        the post-chain secret scan + push + PR all succeeded.
    """

    chain_id: str  # the first ticket's id
    branch: str | None
    pr_url: str | None
    per_ticket: list[RunResult] = field(default_factory=list)
    success: bool = False
    duration_s: float = 0.0
    error: str | None = None


def run_ticket(
    ticket: Ticket,
    repo: RepoConfig,
    capture_cost: bool = False,
    dry_run: bool = False,
    log_dir: Path | None = None,
    quota_tracker: QuotaTracker | None = None,
    executor_providers: list[str] | None = None,
    max_utilization: float = 0.90,
) -> RunResult:
    """Core single-ticket pipeline."""
    import time as _time

    _providers = executor_providers or ["claude"]
    check_tools(providers=_providers)

    if repo.local_path.exists() and is_dirty(repo.local_path):
        raise RuntimeError(
            f"Working tree at {repo.local_path} is dirty. "
            "Commit or stash your changes before running."
        )
    sync_repo(repo.local_path, repo.github, repo.default_branch)

    short_uuid = uuid.uuid4().hex[:8]
    branch = f"factory/{ticket.id.lower()}-{short_uuid}"
    create_branch(repo.local_path, branch)

    started_at = datetime.now(UTC)
    start = _time.monotonic()
    files_changed: list[str] = []

    result = RunResult(
        ticket_id=ticket.id,
        success=False,
        branch=branch,
        dry_run=dry_run,
    )

    try:
        if ticket.subtasks:
            agent = _run_subtask_loop(
                ticket=ticket,
                repo_path=repo.local_path,
                capture_cost=capture_cost,
                providers=_providers,
                quota_tracker=quota_tracker,
                max_utilization=max_utilization,
            )
        else:
            agent = _run_with_fallback(
                repo.local_path,
                _build_prompt(ticket, repo.local_path),
                capture_cost=capture_cost,
                budget_minutes=None,
                providers=_providers,
                quota_tracker=quota_tracker,
                max_utilization=max_utilization,
            )
        result.exit_code = agent.exit_code
        result.tokens_used = agent.tokens_used
        result.cost_usd = agent.cost_usd
        result.usage_limit_hit = agent.usage_limit_hit
        result.provider_used = agent.provider

        # All providers were quota-exhausted before any run started.
        # Clean up the empty branch so the ticket stays queued for next session.
        if agent.usage_limit_hit and agent.provider == "exhausted":
            delete_branch(repo.local_path, branch, repo.default_branch)
            result.branch = None
            result.reason = "all_providers_quota_exhausted"
            return _finalise(result, start, started_at, log_dir)

        if agent.timed_out:
            typer.echo(
                f"\nExecutor timed out. Branch '{branch}' preserved for inspection.", err=True
            )
            _preserve_and_return_to_default(repo.local_path, branch, repo.default_branch, ticket.id)
            result.error = "Executor timed out"
            result.reason = "timeout"
            return _finalise(result, start, started_at, log_dir)

        if agent.exit_code != 0:
            typer.echo(
                f"\nAgent exited with code {agent.exit_code}. "
                f"Branch '{branch}' preserved for inspection.",
                err=True,
            )
            _preserve_and_return_to_default(repo.local_path, branch, repo.default_branch, ticket.id)
            result.error = f"Agent exited with code {agent.exit_code}"
            result.reason = "unknown"
            return _finalise(result, start, started_at, log_dir)

        if not has_changes(repo.local_path):
            delete_branch(repo.local_path, branch, repo.default_branch)
            result.branch = None
            result.error = "Agent produced no changes"
            result.reason = "agent_no_changes"
            return _finalise(result, start, started_at, log_dir)

        files_changed = get_changed_files(repo.local_path)
        result.files_changed = files_changed

        violations = _scope_violations(repo.local_path, ticket)
        result.scope_violations = violations
        if violations:
            _report_scope_advisory(violations)

        # Install / build
        install_cmd = repo.build_command or detect_install_command(repo.local_path)
        if install_cmd:
            r = run_shell_command(install_cmd, repo.local_path)
            if r.returncode != 0:
                typer.echo(
                    f"\nInstall/build failed. Branch '{branch}' preserved for inspection.", err=True
                )
                _preserve_and_return_to_default(
                    repo.local_path, branch, repo.default_branch, ticket.id
                )
                result.error = f"Install/build failed (exit {r.returncode})"
                result.reason = "tests_failed"
                return _finalise(result, start, started_at, log_dir)

        # Tests
        test_cmd = repo.test_command or detect_test_command(repo.local_path)
        if test_cmd:
            if test_cmd.startswith("make "):
                ensure_stack_ready(repo.local_path)
            r = run_shell_command(test_cmd, repo.local_path)
            if r.returncode != 0:
                repair = _attempt_test_repair(
                    ticket=ticket,
                    repo_path=repo.local_path,
                    test_cmd=test_cmd,
                    exit_code=r.returncode,
                    capture_cost=capture_cost,
                    providers=_providers,
                    quota_tracker=quota_tracker,
                    max_utilization=max_utilization,
                )
                if repair.exit_code != 0 or repair.timed_out or repair.usage_limit_hit:
                    typer.echo(
                        f"\nTest repair failed. Branch '{branch}' preserved for inspection.",
                        err=True,
                    )
                    _preserve_and_return_to_default(
                        repo.local_path, branch, repo.default_branch, ticket.id
                    )
                    result.error = _repair_error(repair)
                    result.reason = "tests_failed"
                    return _finalise(result, start, started_at, log_dir)

                files_changed = get_changed_files(repo.local_path)
                result.files_changed = files_changed

                violations = _scope_violations(repo.local_path, ticket)
                result.scope_violations = violations
                if violations:
                    _report_scope_advisory(violations)

                r = run_shell_command(test_cmd, repo.local_path)
                if r.returncode != 0:
                    typer.echo(
                        f"\nTests still failed after one repair attempt. "
                        f"Branch '{branch}' preserved for inspection.",
                        err=True,
                    )
                    _preserve_and_return_to_default(
                        repo.local_path, branch, repo.default_branch, ticket.id
                    )
                    result.error = (
                        f"Tests failed after one repair attempt (exit {r.returncode}). "
                        f"Inspect branch '{branch}' and the test output above."
                    )
                    result.reason = "tests_failed"
                    return _finalise(result, start, started_at, log_dir)

        files_changed = get_changed_files(repo.local_path)
        result.files_changed = files_changed

        violations = _scope_violations(repo.local_path, ticket)
        result.scope_violations = violations
        if violations:
            _report_scope_advisory(violations)

        # Memory file: Claude writes it as part of its task (see _build_prompt).
        # Fall back to the generic writer only if Claude forgot.
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        memory_file = repo.local_path / ".claude" / "memory" / f"{ticket.id.lower()}_{today}.md"
        if not memory_file.exists():
            typer.echo(
                "  Note: Claude did not write a memory file — using generic fallback.", err=True
            )
            write_run_memory(
                local_path=repo.local_path,
                ticket_id=ticket.id,
                pr_url="(pending)",
                files_changed=files_changed,
                cost_usd=agent.cost_usd,
                duration_s=_time.monotonic() - start,
            )

        # Commit
        commit_msg = f"{ticket.id}: {ticket.title}"
        commit(repo.local_path, commit_msg)

        # Secret scan (after commit, before push)
        leaks = secret_scan(repo.local_path)
        if leaks:
            undo_commit(repo.local_path)
            delete_branch(repo.local_path, branch, repo.default_branch)
            result.branch = None
            result.error = f"Secret scan failed — rules fired: {', '.join(leaks)}"
            result.reason = "secret_scan_failed"
            return _finalise(result, start, started_at, log_dir)

        if dry_run:
            typer.echo(f"\nDry-run: branch '{branch}' and commit preserved. No push or PR.")
            result.success = True
            result.reason = "dry_run"
            return _finalise(result, start, started_at, log_dir)

        # Push
        push(repo.local_path, branch)

        # PR
        pr_url = create_pr(
            repo.local_path,
            title=commit_msg,
            body=_build_pr_body(ticket),
            base=repo.default_branch,
            head=branch,
        )
        result.pr_url = pr_url
        result.success = True
        typer.echo(f"\nPR opened: {pr_url}")
        return _finalise(result, start, started_at, log_dir)

    except RuntimeError as e:
        result.error = str(e)
        if not result.reason:
            result.reason = "unknown"
        return _finalise(result, start, started_at, log_dir)

    except Exception:
        msg = traceback.format_exc()
        typer.echo(f"\nUnexpected error on branch '{branch}':\n{msg}", err=True)
        result.error = msg.splitlines()[-1]
        result.reason = "unknown"
        return _finalise(result, start, started_at, log_dir)


def _run_with_fallback(
    local_path: Path,
    prompt: str,
    capture_cost: bool,
    budget_minutes: float | None,
    providers: list[str],
    quota_tracker: QuotaTracker | None,
    max_utilization: float = 0.90,
) -> AgentResult:
    """Try each provider in order, skipping quota-exhausted ones and falling back on quota hit.

    For claude, fetches live 5-hour utilization from the API before starting — skips the
    provider if utilization >= max_utilization (default 0.90 = 90%).
    """
    for name in providers:
        if quota_tracker and not quota_tracker.is_available(name):
            reset_msg = quota_tracker.format_reset_time(name)
            typer.echo(f"  [{name}] quota exhausted — {reset_msg}, skipping.", err=True)
            continue

        # Pre-flight quota check (claude only — live header probe)
        if name == "claude":
            quota = claude_provider.fetch_quota()
            if quota is not None:
                pct = round(quota.utilization_5h * 100, 1)
                reset_str = quota.reset_5h.strftime("%H:%M UTC") if quota.reset_5h else "unknown"
                typer.echo(f"  [claude] 5h quota: {pct}% used (resets {reset_str})")
                if quota.utilization_5h >= max_utilization:
                    typer.echo(
                        f"  [claude] {pct}% >= {max_utilization * 100:.0f}% threshold — skipping.",
                        err=True,
                    )
                    if quota_tracker:
                        quota_tracker.mark_exhausted("claude")
                    continue
            else:
                typer.echo("  [claude] quota probe failed — proceeding without check.")

        typer.echo(f"  Using executor: {name}")
        if name == "claude":
            agent = claude_provider.run(
                local_path, prompt, capture_cost=capture_cost, budget_minutes=budget_minutes
            )
        elif name == "codex":
            agent = codex_provider.run(local_path, prompt, budget_minutes=budget_minutes)
        elif name == "opencode":
            agent = opencode_provider.run(local_path, prompt, budget_minutes=budget_minutes)
        else:
            typer.echo(f"  [{name}] unknown provider, skipping.", err=True)
            continue

        if agent.usage_limit_hit:
            if quota_tracker:
                quota_tracker.mark_exhausted(name)
                reset_msg = quota_tracker.format_reset_time(name)
                typer.echo(
                    f"  [{name}] quota hit — marked exhausted ({reset_msg}). Trying next provider.",
                    err=True,
                )
            else:
                typer.echo(f"  [{name}] quota hit. Trying next provider.", err=True)
            continue

        return agent

    # All providers exhausted — signal the caller to end the session gracefully.
    # The ticket is NOT run; it stays in the queue for the next session.
    status_parts = []
    for name in providers:
        if quota_tracker:
            status_parts.append(f"{name}: {quota_tracker.format_reset_time(name)}")
        else:
            status_parts.append(name)
    msg = "All executor providers are quota-exhausted. " + ", ".join(status_parts)
    typer.echo(f"\n{msg}", err=True)
    return AgentResult(exit_code=-1, usage_limit_hit=True, provider="exhausted")


def _run_subtask_loop(
    ticket: Ticket,
    repo_path: Path,
    capture_cost: bool,
    providers: list[str],
    quota_tracker: QuotaTracker | None,
    max_utilization: float,
) -> AgentResult:
    """Execute each subtask sequentially as its own agent invocation.

    Returns an aggregated AgentResult:
      - exit_code 0 if all subtasks ran cleanly; first non-zero otherwise (and loop stops)
      - tokens_used / cost_usd summed across subtasks
      - timed_out / usage_limit_hit propagated from any failing subtask
      - provider = the provider that ran the last (or failing) subtask

    Failure policy: if a subtask hits a quota-exhausted state or returns non-zero,
    stop the loop and propagate. The caller (run_ticket) handles branch preservation.

    Per-subtask tier_hint reorders the providers list so the hinted provider is tried
    first; fallbacks remain available if it fails or is exhausted.
    """
    total_tokens = 0
    total_cost = 0.0
    last_provider = "unknown"
    last_output: str | None = None
    completed: list[Subtask] = []

    for subtask in ticket.subtasks:
        typer.echo(f"\n  ── subtask [{subtask.id}] {subtask.title}")
        ordered = _providers_with_tier_hint(providers, subtask.tier_hint)
        prompt = _build_subtask_prompt(ticket, subtask, completed, repo_path)
        agent = _run_with_fallback(
            repo_path,
            prompt,
            capture_cost=capture_cost,
            budget_minutes=None,
            providers=ordered,
            quota_tracker=quota_tracker,
            max_utilization=max_utilization,
        )

        if agent.tokens_used:
            total_tokens += agent.tokens_used
        if agent.cost_usd:
            total_cost += agent.cost_usd
        last_provider = agent.provider
        last_output = agent.output or last_output

        if agent.usage_limit_hit or agent.timed_out or agent.exit_code != 0:
            return AgentResult(
                exit_code=agent.exit_code if agent.exit_code != 0 else -1,
                cost_usd=total_cost or None,
                tokens_used=total_tokens or None,
                output=last_output,
                timed_out=agent.timed_out,
                usage_limit_hit=agent.usage_limit_hit,
                provider=last_provider,
            )

        completed.append(subtask)

    return AgentResult(
        exit_code=0,
        cost_usd=total_cost or None,
        tokens_used=total_tokens or None,
        output=last_output,
        provider=last_provider,
    )


def _providers_with_tier_hint(providers: list[str], hint: str | None) -> list[str]:
    """Reorder providers so the hinted tier is first; keep others as fallbacks.

    hint='local' maps to 'opencode'. hint='hosted' is a no-op (default order is
    already claude→codex). Unknown hints are no-ops.
    """
    if not hint:
        return providers
    if hint == "local":
        target = "opencode"
    elif hint == "hosted":
        return providers
    else:
        return providers

    if target not in providers:
        return providers
    return [target] + [p for p in providers if p != target]


def _build_subtask_prompt(
    ticket: Ticket,
    subtask: Subtask,
    completed: list[Subtask],
    repo_path: Path,
) -> str:
    """Build a focused prompt for one subtask within a ticket.

    Includes: project rules, overall ticket title + AC (for context), this subtask's
    spec, the referenced skill content (loaded from disk), the list of already-completed
    subtasks, and the current git diff so the agent sees prior work.
    """
    rules = _load_repo_rules(repo_path)
    skill_text = _load_skill(repo_path, subtask.skill) if subtask.skill else ""
    diff_so_far = _current_diff(repo_path)
    remaining = [s for s in ticket.subtasks if s not in completed and s is not subtask]

    parts: list[str] = []
    if rules:
        parts += ["## Project Rules", "", rules, "", "---", ""]

    parts += [
        f"You are executing ONE subtask within ticket {ticket.id}.",
        "Other subtasks will run before and after yours — together they make the full PR.",
        "Do not satisfy the overall acceptance criteria yourself; only complete YOUR subtask.",
        "",
        f"## Ticket: {ticket.title}",
        "",
        "Overall acceptance criteria (context only):",
        "",
        ticket.acceptance_criteria,
        "",
        f"## Your subtask: [{subtask.id}] {subtask.title}",
        "",
        "**Files you may touch:**",
    ]
    for f in subtask.files or ["(no files listed — be conservative)"]:
        parts.append(f"  - {f}")
    parts += [
        "",
        "**Changes:**",
        "",
        subtask.changes or "(see overall ticket; subtask body left empty)",
    ]

    if skill_text:
        parts += [
            "",
            f"## Skill to follow: {subtask.skill}",
            "",
            skill_text,
        ]
    elif subtask.skill:
        parts += [
            "",
            f"## Skill reference: {subtask.skill}",
            "",
            f"(Skill file not found at {subtask.skill}. "
            "Proceed using only the subtask spec above.)",
        ]

    if completed:
        parts += ["", "## Already completed in this run"]
        for s in completed:
            parts.append(f"  - [{s.id}] {s.title}")

    if remaining:
        parts += ["", "## Remaining subtasks (do NOT do these — they belong to later runs)"]
        for s in remaining:
            parts.append(f"  - [{s.id}] {s.title}")

    if diff_so_far.strip():
        parts += [
            "",
            "## Current state of the working tree (changes from earlier subtasks)",
            "",
            "```diff",
            diff_so_far,
            "```",
        ]

    parts += [
        "",
        "## Rules for this subtask",
        "",
        "1. Read the listed files (if they exist) and the skill above.",
        "2. Make ONLY the changes for this subtask. Do not touch other files.",
        "3. Do NOT run tests. Do NOT run `git commit` or `git push`.",
        "4. The codebase may be in a partially-broken state — that's expected. "
        "Tests run once after all subtasks complete.",
        "5. When done, stop.",
    ]

    return "\n".join(parts)


def _load_skill(repo_path: Path, skill_rel: str) -> str:
    """Read a skill markdown file. Returns empty string if missing."""
    p = repo_path / skill_rel
    try:
        return p.read_text().strip()
    except (OSError, FileNotFoundError):
        return ""


def _current_diff(repo_path: Path) -> str:
    """Return `git diff HEAD` (all tracked + staged) for the working tree."""
    if not repo_path.exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _scope_violations(local_path: Path, ticket: Ticket) -> list[str]:
    """Return changed files outside the ticket scope, with factory memory always allowed."""
    if not ticket.scope_paths:
        return []
    always_allowed = (".claude/memory/",)
    return [
        f
        for f in check_scope(local_path, ticket.scope_paths)
        if not any(f.startswith(prefix) for prefix in always_allowed)
    ]


def _report_scope_advisory(violations: list[str]) -> None:
    typer.echo(
        "\nScope advisory: changed files outside ticket scope: " + ", ".join(violations),
        err=True,
    )


def _attempt_test_repair(
    *,
    ticket: Ticket,
    repo_path: Path,
    test_cmd: str,
    exit_code: int,
    capture_cost: bool,
    providers: list[str],
    quota_tracker: QuotaTracker | None,
    max_utilization: float,
) -> AgentResult:
    """Give the executor one focused chance to repair failing tests."""
    typer.echo("\nTests failed. Asking executor for one repair attempt.", err=True)
    return _run_with_fallback(
        repo_path,
        _build_test_repair_prompt(ticket, test_cmd, exit_code),
        capture_cost=capture_cost,
        budget_minutes=None,
        providers=providers,
        quota_tracker=quota_tracker,
        max_utilization=max_utilization,
    )


def _repair_error(agent: AgentResult) -> str:
    if agent.timed_out:
        return "Test repair executor timed out"
    if agent.usage_limit_hit:
        return "Test repair skipped because all executor providers are quota-exhausted"
    return f"Test repair agent exited with code {agent.exit_code}"


def _build_test_repair_prompt(ticket: Ticket, test_cmd: str, exit_code: int) -> str:
    return "\n".join(
        [
            "The implementation for this ticket was completed, but the verification command "
            "failed.",
            "",
            f"Ticket ID: {ticket.id}",
            f"Title: {ticket.title}",
            "",
            "## Acceptance Criteria",
            "",
            ticket.acceptance_criteria,
            "",
            "## Failed Verification",
            "",
            f"Command: `{test_cmd}`",
            f"Exit code: {exit_code}",
            "",
            "Re-run the failing command, inspect the failure, and make the smallest changes needed",
            "to get the suite passing while preserving the ticket acceptance criteria.",
            "",
            "If the failure is caused by an unrelated broken environment or requires work outside",
            "the ticket's scope, do not make speculative changes. Print a concise explanation of",
            "what failed and what a human should do next.",
            "",
            "Do NOT run git commit. Do NOT run git push. Only edit files, then stop.",
        ]
    )


def _preserve_and_return_to_default(
    local_path: Path, branch: str, default_branch: str, ticket_id: str
) -> None:
    """Leave failed work on its branch and return the repo to the default branch."""
    subprocess.run(["git", "add", "-A"], cwd=local_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"{ticket_id}: preserve failed factory run"],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "checkout", default_branch],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo(
            f"  Warning: could not return from branch '{branch}' to '{default_branch}': "
            f"{result.stderr.strip()}",
            err=True,
        )


def _finalise(
    result: RunResult, start: float, started_at: datetime, log_dir: Path | None
) -> RunResult:
    import time as _time

    result.duration_s = _time.monotonic() - start
    if log_dir:
        _write_log(result, started_at, log_dir)
    return result


def _write_log(result: RunResult, started_at: datetime, log_dir: Path) -> None:
    ended_at = datetime.now(UTC)
    date_str = started_at.strftime("%Y-%m-%d")
    log_path = log_dir / date_str / f"{result.ticket_id.lower()}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    outcome = "dry_run" if result.dry_run else ("succeeded" if result.success else "failed")
    entry = {
        "ticket_id": result.ticket_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_s": round(result.duration_s, 2),
        "result": outcome,
        "pr_url": result.pr_url,
        "branch": result.branch,
        "files_changed": result.files_changed,
        "scope_check": {
            "passed": not result.scope_violations,
            "violations": result.scope_violations,
        },
        "budget": {
            "tokens_used": result.tokens_used,
            "minutes_used": round(result.duration_s / 60, 2),
        },
        "cost_usd": result.cost_usd,
        "exit_code": result.exit_code,
        "error": result.error,
        "reason": result.reason,
        "provider": result.provider_used,
    }
    log_path.write_text(json.dumps(entry, indent=2))


def run_ticket_from_file(
    ticket_path: Path,
    repo_key: str,
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> RunResult:
    manifest = load_manifest(manifest_path)
    if repo_key not in manifest.repos:
        available = ", ".join(manifest.repos.keys())
        raise ValueError(f"Repo '{repo_key}' not found in manifest. Available: {available}")
    repo = manifest.repos[repo_key]

    ticket = parse_ticket(ticket_path)
    if ticket.target_repo != repo_key:
        typer.echo(
            f"Warning: ticket target_repo='{ticket.target_repo}' does not match "
            f"--repo='{repo_key}'. Using --repo.",
            err=True,
        )
        ticket.target_repo = repo_key

    base_dir = (manifest_path or Path("manifest.yaml")).resolve().parent
    log_dir = base_dir / "logs"

    return run_ticket(ticket, repo, capture_cost=False, dry_run=dry_run, log_dir=log_dir)


def _load_repo_rules(repo_path: Path) -> str:
    """Read all .ai/rules/*.md files from the repo and return combined text.

    Injected directly into the prompt so rules are enforced even in non-interactive
    -p mode, where Claude may not proactively open files referenced in CLAUDE.md.
    """
    rules_dir = repo_path / ".ai" / "rules"
    if not rules_dir.exists():
        return ""
    sections = []
    for rules_file in sorted(rules_dir.glob("*.md")):
        try:
            content = rules_file.read_text().strip()
            if content:
                sections.append(content)
        except OSError:
            pass
    return "\n\n".join(sections)


def _skill_loading_instructions(ticket: Ticket) -> list[str]:
    """Return step 4 of the pre-code checklist.

    Always instructs the executor to discover skills. If the ticket named skills,
    lists them as a starting hint so the executor doesn't have to guess from scratch.
    """
    lines = [
        "4. List the contents of `.ai/skills/` to see what guidance is available,",
        "   then read every skill file relevant to this task. They encode repo-specific",
        "   conventions that override general patterns you may already know.",
    ]
    if ticket.skills:
        lines += ["   Pre-selected as likely relevant — read these first:"]
        for s in ticket.skills:
            lines.append(f"   - `{s}`")
    return lines


def _build_prompt(ticket: Ticket, repo_path: Path) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    memory_path = f".claude/memory/{ticket.id.lower()}_{today}.md"

    rules = _load_repo_rules(repo_path)
    preamble = []
    if rules:
        preamble = [
            "## Project Rules",
            "",
            "These rules always apply. Follow them exactly.",
            "",
            rules,
            "",
            "---",
            "",
        ]

    parts = preamble + [
        "You are working on a task described by the following ticket.",
        "",
        f"Ticket ID: {ticket.id}",
        f"Title: {ticket.title}",
        "",
        "## Acceptance Criteria",
        "",
        ticket.acceptance_criteria,
    ]
    if ticket.notes:
        parts += ["", "## Notes", "", ticket.notes]
    if ticket.scope_paths:
        parts += ["", "## Scope", "", "Only modify files matching these patterns:"]
        parts += [f"  - {p}" for p in ticket.scope_paths]
    parts += [
        "",
        "---",
        "",
        "## Before writing any code",
        "",
        "1. Read `AGENTS.md` and `CLAUDE.md` if present.",
        "2. Read `.claude/memory/MEMORY.md` if present, then read only memory files",
        "   that are relevant to this ticket.",
        "3. Read scoped context only when it matches the task:",
        "   - Backend/API work → read `.ai/context/api.md` if present.",
        "   - Frontend/web work → read `.ai/context/web.md` if present.",
        "   - Full-stack work → read both.",
        *_skill_loading_instructions(ticket),
        "5. Then implement the changes.",
        "",
        "## Implementation",
        "",
        "Make the necessary changes to satisfy the acceptance criteria.",
        "Do NOT run git commit. Do NOT run git push. Only edit files.",
        "",
        f"When you are done, write a memory file at `{memory_path}` using this exact format:",
        "",
        "```",
        "---",
        "name: <short descriptive name of what was built — NOT the ticket ID>",
        "description: <one sentence: what was added/changed and the key decision or pattern>",
        "type: project",
        "---",
        "",
        f"Factory ran ticket **{ticket.id}** on {today}.",
        "",
        "**PR:** (pending)",
        "**Files changed:**",
        "- <list key files modified>",
        "",
        "**Key decisions:**",
        "- <most important non-obvious architectural choice>",
        "```",
        "",
        "The name and description must describe WHAT WAS BUILT, not the ticket ID.",
        "Good: `name: Contractor detail page` / `description: GET /contractors/:id "
        "route + detail page with job history table`",
        f"Bad:  `name: {ticket.id} run {today}` / `description: Factory ran {ticket.id}`",
        "",
        "Do NOT touch `.claude/memory/MEMORY.md` — that file is managed separately "
        "after all tickets complete.",
        "",
        "Then stop.",
    ]
    return "\n".join(parts)


def _build_pr_body(ticket: Ticket) -> str:
    return (
        f"## Acceptance Criteria\n\n"
        f"{ticket.acceptance_criteria}\n\n"
        f"---\n\n"
        f"_Generated by ai\\_factory_"
    )


# =====================================================================
# Chain execution (ADR-019)
# =====================================================================


def run_chain(
    chain: list[Ticket],
    repo: RepoConfig,
    *,
    capture_cost: bool = False,
    dry_run: bool = False,
    log_dir: Path | None = None,
    quota_tracker: QuotaTracker | None = None,
    executor_providers: list[str] | None = None,
    max_utilization: float = 0.90,
) -> ChainResult:
    """Run a dependency chain of tickets on a shared branch, one commit each.

    Single-ticket chains delegate to ``run_ticket`` to preserve the existing
    behavior verbatim. Multi-ticket chains use a shared branch and per-ticket
    commits per ADR-019.

    On mid-chain failure (agent error, tests fail, scope abort, quota
    exhaustion): the chain branch is preserved with the *successful* commits;
    no PR is opened; all tickets remain in the queue. The caller can re-run
    once the underlying failure is resolved.
    """
    import time as _time

    if not chain:
        raise ValueError("run_chain called with empty chain")

    if len(chain) == 1:
        rr = run_ticket(
            chain[0], repo,
            capture_cost=capture_cost, dry_run=dry_run, log_dir=log_dir,
            quota_tracker=quota_tracker,
            executor_providers=executor_providers,
            max_utilization=max_utilization,
        )
        return ChainResult(
            chain_id=chain[0].id,
            branch=rr.branch,
            pr_url=rr.pr_url,
            per_ticket=[rr],
            success=rr.success,
            duration_s=rr.duration_s,
            error=rr.error,
        )

    _providers = executor_providers or ["claude"]
    check_tools(providers=_providers)
    if repo.local_path.exists() and is_dirty(repo.local_path):
        raise RuntimeError(
            f"Working tree at {repo.local_path} is dirty. "
            "Commit or stash your changes before running."
        )
    sync_repo(repo.local_path, repo.github, repo.default_branch)

    short_uuid = uuid.uuid4().hex[:8]
    branch = f"factory/{chain[0].id.lower()}-chain-{short_uuid}"
    create_branch(repo.local_path, branch)

    start = _time.monotonic()
    result = ChainResult(chain_id=chain[0].id, branch=branch, pr_url=None)

    for ticket in chain:
        typer.echo(f"\n══ chain ticket {ticket.id}: {ticket.title} ══")
        rr = _run_one_ticket_on_chain_branch(
            ticket=ticket, repo=repo, branch=branch,
            capture_cost=capture_cost,
            providers=_providers, quota_tracker=quota_tracker,
            max_utilization=max_utilization, log_dir=log_dir,
        )
        result.per_ticket.append(rr)
        if not rr.success:
            typer.echo(
                f"\nChain aborted at ticket {ticket.id}. Branch '{branch}' preserved "
                f"with {len(result.per_ticket) - 1} successful commit(s).",
                err=True,
            )
            result.error = f"Chain failed at {ticket.id}: {rr.error or rr.reason}"
            result.duration_s = _time.monotonic() - start
            _return_to_default(repo.local_path, repo.default_branch)
            return result

    # All tickets committed cleanly. Secret scan once over the whole chain,
    # then push + open PR.
    leaks = secret_scan(repo.local_path)
    if leaks:
        # Undo all chain commits and delete the branch — no PR.
        for _ in range(len(chain)):
            undo_commit(repo.local_path)
        delete_branch(repo.local_path, branch, repo.default_branch)
        result.branch = None
        result.error = f"Secret scan failed — rules fired: {', '.join(leaks)}"
        result.duration_s = _time.monotonic() - start
        return result

    if dry_run:
        typer.echo(f"\nDry-run: chain branch '{branch}' preserved. No push or PR.")
        result.success = True
        result.duration_s = _time.monotonic() - start
        return result

    push(repo.local_path, branch)
    pr_url = create_pr(
        repo.local_path,
        title=_chain_pr_title(chain),
        body=_chain_pr_body(chain),
        base=repo.default_branch,
        head=branch,
    )
    result.pr_url = pr_url
    result.success = True
    # Backfill the PR URL onto each per-ticket result so Linear write-back
    # downstream can attribute the same PR to every chained ticket.
    for rr in result.per_ticket:
        rr.pr_url = pr_url
    result.duration_s = _time.monotonic() - start
    typer.echo(f"\nChain PR opened ({len(chain)} commits): {pr_url}")
    return result


def _run_one_ticket_on_chain_branch(
    *,
    ticket: Ticket,
    repo: RepoConfig,
    branch: str,
    capture_cost: bool,
    providers: list[str],
    quota_tracker: QuotaTracker | None,
    max_utilization: float,
    log_dir: Path | None,
) -> RunResult:
    """Run one ticket in the middle of a chain, committing on the shared branch.

    Mirrors the inner per-ticket logic of ``run_ticket`` minus the branch
    creation, secret scan, push, and PR steps (those are chain-level).
    On any failure, discards this ticket's uncommitted changes so the
    chain branch is left with only the successful prior commits.
    """
    import time as _time

    started_at = datetime.now(UTC)
    start = _time.monotonic()
    rr = RunResult(ticket_id=ticket.id, success=False, branch=branch)

    try:
        if ticket.subtasks:
            agent = _run_subtask_loop(
                ticket=ticket, repo_path=repo.local_path,
                capture_cost=capture_cost, providers=providers,
                quota_tracker=quota_tracker, max_utilization=max_utilization,
            )
        else:
            agent = _run_with_fallback(
                repo.local_path,
                _build_prompt(ticket, repo.local_path),
                capture_cost=capture_cost, budget_minutes=None,
                providers=providers, quota_tracker=quota_tracker,
                max_utilization=max_utilization,
            )
        rr.exit_code = agent.exit_code
        rr.tokens_used = agent.tokens_used
        rr.cost_usd = agent.cost_usd
        rr.usage_limit_hit = agent.usage_limit_hit
        rr.provider_used = agent.provider

        if agent.usage_limit_hit and agent.provider == "exhausted":
            rr.error = "All providers quota-exhausted mid-chain"
            rr.reason = "all_providers_quota_exhausted"
            _discard_uncommitted(repo.local_path)
            return _finalise(rr, start, started_at, log_dir)

        if agent.timed_out:
            rr.error = "Executor timed out"
            rr.reason = "timeout"
            _discard_uncommitted(repo.local_path)
            return _finalise(rr, start, started_at, log_dir)

        if agent.exit_code != 0:
            rr.error = f"Agent exited with code {agent.exit_code}"
            rr.reason = "unknown"
            _discard_uncommitted(repo.local_path)
            return _finalise(rr, start, started_at, log_dir)

        if not has_changes(repo.local_path):
            rr.error = "Agent produced no changes"
            rr.reason = "agent_no_changes"
            return _finalise(rr, start, started_at, log_dir)

        files_changed = get_changed_files(repo.local_path)
        rr.files_changed = files_changed
        violations = _scope_violations(repo.local_path, ticket)
        rr.scope_violations = violations
        if violations:
            _report_scope_advisory(violations)

        install_cmd = repo.build_command or detect_install_command(repo.local_path)
        if install_cmd:
            r = run_shell_command(install_cmd, repo.local_path)
            if r.returncode != 0:
                rr.error = f"Install/build failed (exit {r.returncode})"
                rr.reason = "tests_failed"
                _discard_uncommitted(repo.local_path)
                return _finalise(rr, start, started_at, log_dir)

        test_cmd = repo.test_command or detect_test_command(repo.local_path)
        if test_cmd:
            if test_cmd.startswith("make "):
                ensure_stack_ready(repo.local_path)
            r = run_shell_command(test_cmd, repo.local_path)
            if r.returncode != 0:
                repair = _attempt_test_repair(
                    ticket=ticket, repo_path=repo.local_path, test_cmd=test_cmd,
                    exit_code=r.returncode, capture_cost=capture_cost,
                    providers=providers, quota_tracker=quota_tracker,
                    max_utilization=max_utilization,
                )
                if repair.exit_code != 0 or repair.timed_out or repair.usage_limit_hit:
                    rr.error = _repair_error(repair)
                    rr.reason = "tests_failed"
                    _discard_uncommitted(repo.local_path)
                    return _finalise(rr, start, started_at, log_dir)
                r = run_shell_command(test_cmd, repo.local_path)
                if r.returncode != 0:
                    rr.error = (
                        f"Tests failed after one repair attempt (exit {r.returncode})"
                    )
                    rr.reason = "tests_failed"
                    _discard_uncommitted(repo.local_path)
                    return _finalise(rr, start, started_at, log_dir)
                files_changed = get_changed_files(repo.local_path)
                rr.files_changed = files_changed

        # Memory file: Claude writes it; fall back if it forgot.
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        memory_file = (
            repo.local_path / ".claude" / "memory" / f"{ticket.id.lower()}_{today}.md"
        )
        if not memory_file.exists():
            write_run_memory(
                local_path=repo.local_path, ticket_id=ticket.id,
                pr_url="(pending)", files_changed=files_changed,
                cost_usd=agent.cost_usd, duration_s=_time.monotonic() - start,
            )

        commit(repo.local_path, f"{ticket.id}: {ticket.title}")
        rr.success = True
        return _finalise(rr, start, started_at, log_dir)

    except RuntimeError as e:
        rr.error = str(e)
        if not rr.reason:
            rr.reason = "unknown"
        _discard_uncommitted(repo.local_path)
        return _finalise(rr, start, started_at, log_dir)


def _discard_uncommitted(local_path: Path) -> None:
    """Drop uncommitted changes so the chain branch is left at the last good commit."""
    subprocess.run(["git", "restore", "."], cwd=local_path, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=local_path, capture_output=True)


def _return_to_default(local_path: Path, default_branch: str) -> None:
    """Switch back to the default branch without touching the chain branch."""
    subprocess.run(
        ["git", "checkout", default_branch],
        cwd=local_path,
        capture_output=True,
        text=True,
    )


def _chain_pr_title(chain: list[Ticket]) -> str:
    first = chain[0].id
    rest = len(chain) - 1
    return f"{first} + {rest} more: {chain[0].title}" if rest else f"{first}: {chain[0].title}"


def _chain_pr_body(chain: list[Ticket]) -> str:
    lines = ["This chain landed multiple dependent tickets on one branch.\n"]
    for i, t in enumerate(chain, 1):
        lines.append(f"## Commit {i}: {t.id} — {t.title}\n")
        lines.append("**Acceptance Criteria:**\n")
        lines.append(t.acceptance_criteria + "\n")
        if t.linear_url:
            lines.append(f"_Linear:_ {t.linear_url}\n")
        lines.append("")
    lines.append("---\n\n_Generated by ai\\_factory (ADR-019 chain)_")
    return "\n".join(lines)
