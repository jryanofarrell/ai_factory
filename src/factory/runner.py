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
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import typer

from .git_ops import (
    AgentResult,
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
    run_agent,
    run_shell_command,
    secret_scan,
    sync_repo,
    undo_commit,
)
from .manifest import RepoConfig, load_manifest
from .ticket import Ticket, parse_ticket


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


def run_ticket(
    ticket: Ticket,
    repo: RepoConfig,
    capture_cost: bool = False,
    dry_run: bool = False,
    log_dir: Path | None = None,
) -> RunResult:
    """Core single-ticket pipeline."""
    import time as _time

    check_tools()

    if repo.local_path.exists() and is_dirty(repo.local_path):
        raise RuntimeError(
            f"Working tree at {repo.local_path} is dirty. "
            "Commit or stash your changes before running."
        )
    sync_repo(repo.local_path, repo.github, repo.default_branch)

    short_uuid = uuid.uuid4().hex[:8]
    branch = f"factory/{ticket.id.lower()}-{short_uuid}"
    create_branch(repo.local_path, branch)

    started_at = datetime.now(timezone.utc)
    start = _time.monotonic()
    files_changed: list[str] = []

    result = RunResult(
        ticket_id=ticket.id,
        success=False,
        branch=branch,
        dry_run=dry_run,
    )

    try:
        agent: AgentResult = run_agent(
            repo.local_path,
            _build_prompt(ticket),
            capture_cost=capture_cost,
            budget_minutes=ticket.budget_minutes,
        )
        result.exit_code = agent.exit_code
        result.tokens_used = agent.tokens_used
        result.cost_usd = agent.cost_usd

        if agent.timed_out:
            delete_branch(repo.local_path, branch, repo.default_branch)
            result.branch = None
            result.error = f"Exceeded time budget of {ticket.budget_minutes} minutes"
            result.reason = "budget_exceeded"
            return _finalise(result, start, started_at, log_dir)

        if agent.exit_code != 0:
            delete_branch(repo.local_path, branch, repo.default_branch)
            result.branch = None
            result.error = f"Claude Code exited with code {agent.exit_code}"
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

        # Scope check
        if ticket.scope_paths:
            violations = check_scope(repo.local_path, ticket.scope_paths)
            result.scope_violations = violations
            if violations:
                delete_branch(repo.local_path, branch, repo.default_branch)
                result.branch = None
                result.error = f"Scope violation: {', '.join(violations)}"
                result.reason = "scope_violation"
                return _finalise(result, start, started_at, log_dir)

        # Install / build
        install_cmd = repo.build_command or detect_install_command(repo.local_path)
        if install_cmd:
            r = run_shell_command(install_cmd, repo.local_path)
            if r.returncode != 0:
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
                typer.echo(f"\nTests failed. Branch '{branch}' preserved for inspection.", err=True)
                result.error = f"Tests failed (exit {r.returncode})"
                result.reason = "tests_failed"
                return _finalise(result, start, started_at, log_dir)

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


def _finalise(result: RunResult, start: float, started_at: datetime, log_dir: Path | None) -> RunResult:
    import time as _time
    result.duration_s = _time.monotonic() - start
    if log_dir:
        _write_log(result, started_at, log_dir)
    return result


def _write_log(result: RunResult, started_at: datetime, log_dir: Path) -> None:
    ended_at = datetime.now(timezone.utc)
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


def _build_prompt(ticket: Ticket) -> str:
    parts = [
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
        "Make the necessary changes to satisfy the acceptance criteria.",
        "Do NOT run git commit. Do NOT run git push. Only edit files.",
        "When you are done, stop.",
    ]
    return "\n".join(parts)


def _build_pr_body(ticket: Ticket) -> str:
    return (
        f"## Acceptance Criteria\n\n"
        f"{ticket.acceptance_criteria}\n\n"
        f"---\n\n"
        f"_Generated by ai\\_factory_"
    )
