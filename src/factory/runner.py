# Phase 3 spike findings (2026-04-27):
# claude -p --output-format json emits a single JSON object on stdout with:
#   total_cost_usd, duration_ms, result (text), usage (token counts)
# This is the only way to capture cost; plain invocation streams text but no metadata.
# capture_cost=True in run_agent enables JSON mode; False keeps live streaming for CLI use.

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import typer

from .git_ops import (
    AgentResult,
    check_tools,
    commit,
    create_branch,
    create_pr,
    delete_branch,
    detect_install_command,
    detect_test_command,
    ensure_stack_ready,
    has_changes,
    is_dirty,
    push,
    run_agent,
    run_shell_command,
    sync_repo,
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


def run_ticket(ticket: Ticket, repo: RepoConfig, capture_cost: bool = False) -> RunResult:
    """Core single-ticket pipeline. Called by both the CLI and the orchestrator."""
    import time

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

    start = time.monotonic()

    try:
        agent: AgentResult = run_agent(repo.local_path, _build_prompt(ticket), capture_cost=capture_cost)
        if agent.exit_code != 0:
            raise RuntimeError(f"Claude Code exited with code {agent.exit_code}.")

        if not has_changes(repo.local_path):
            delete_branch(repo.local_path, branch, repo.default_branch)
            raise RuntimeError("Agent produced no changes. Branch deleted.")

        install_cmd = repo.build_command or detect_install_command(repo.local_path)
        if install_cmd:
            result = run_shell_command(install_cmd, repo.local_path)
            if result.returncode != 0:
                raise RuntimeError(f"Install/build failed (exit {result.returncode}).")

        test_cmd = repo.test_command or detect_test_command(repo.local_path)
        if test_cmd:
            if test_cmd.startswith("make "):
                ensure_stack_ready(repo.local_path)
            result = run_shell_command(test_cmd, repo.local_path)
            if result.returncode != 0:
                typer.echo(f"\nTests failed. Branch '{branch}' preserved for inspection.", err=True)
                raise RuntimeError(f"Tests failed (exit {result.returncode}).")

        commit_msg = f"{ticket.id}: {ticket.title}"
        commit(repo.local_path, commit_msg)
        push(repo.local_path, branch)

        pr_url = create_pr(
            repo.local_path,
            title=commit_msg,
            body=_build_pr_body(ticket),
            base=repo.default_branch,
            head=branch,
        )

        duration = time.monotonic() - start
        cost = agent.cost_usd or (agent.duration_ms / 1000 * 0 if agent.duration_ms else None)

        typer.echo(f"\nPR opened: {pr_url}")
        return RunResult(
            ticket_id=ticket.id,
            success=True,
            pr_url=pr_url,
            branch=branch,
            duration_s=duration,
            cost_usd=agent.cost_usd,
        )

    except RuntimeError as e:
        duration = time.monotonic() - start
        return RunResult(
            ticket_id=ticket.id,
            success=False,
            branch=branch,
            duration_s=duration,
            error=str(e),
        )
    except Exception:
        duration = time.monotonic() - start
        msg = traceback.format_exc()
        typer.echo(f"\nUnexpected error on branch '{branch}':\n{msg}", err=True)
        return RunResult(
            ticket_id=ticket.id,
            success=False,
            branch=branch,
            duration_s=duration,
            error=msg.splitlines()[-1],
        )


def run_ticket_from_file(
    ticket_path: Path,
    repo_key: str,
    manifest_path: Path | None = None,
) -> RunResult:
    """CLI entry point: loads manifest + ticket then delegates to run_ticket()."""
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

    return run_ticket(ticket, repo, capture_cost=False)


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
