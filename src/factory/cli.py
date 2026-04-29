from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Personal AI factory CLI.", no_args_is_help=True)


@app.callback()
def _callback() -> None:
    pass


@app.command()
def record_result(
    ticket_file: Path = typer.Argument(..., help="Path to the ticket file."),
    pr_url: str | None = typer.Option(None, "--pr-url"),
    files: str | None = typer.Option(None, "--files", help="Comma-separated list of changed files."),
    duration: float = typer.Option(0.0, "--duration", help="Wall-clock seconds."),
    cost: float | None = typer.Option(None, "--cost", help="Cost in USD."),
    failed: bool = typer.Option(False, "--failed"),
    error: str | None = typer.Option(None, "--error"),
    branch: str | None = typer.Option(None, "--branch"),
    manifest: Path | None = typer.Option(None, "--manifest"),
) -> None:
    """Write Linear write-back and memory after a skill-driven ticket run."""
    import os
    from dotenv import load_dotenv
    from .linear import LinearClient, LinearError
    from .manifest import load_manifest
    from .ticket import parse_ticket
    from .git_ops import write_run_memory

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")

    ticket = parse_ticket(ticket_file)
    m = load_manifest(manifest)
    repo = m.repos.get(ticket.target_repo)
    team_key = (repo.linear_team if repo else None) or ticket.target_repo.upper()

    # Linear write-back
    if api_key and ticket.linear_id:
        client = LinearClient(api_key)
        try:
            m_s, m_s_rem = divmod(int(duration), 60)
            dur_str = f"{m_s}m {m_s_rem}s" if m_s else f"{m_s_rem}s"
            if not failed and pr_url:
                cost_str = f"${cost:.2f}" if cost is not None else "n/a"
                body = f"PR opened: {pr_url}\nDuration: {dur_str} · Cost: {cost_str}"
                client.comment_on_issue(ticket.linear_id, body)
                state_id = client.get_state_id(team_key, "In Review")
                if state_id:
                    client.transition_issue(ticket.linear_id, state_id)
            else:
                branch_note = f"\nBranch preserved: `{branch}`" if branch else ""
                body = f"Execution failed: {error or 'unknown'}\nDuration: {dur_str}{branch_note}"
                client.comment_on_issue(ticket.linear_id, body)
                state_id = client.get_state_id(team_key, "Failed for Agent")
                if state_id:
                    client.transition_issue(ticket.linear_id, state_id)
        except LinearError as e:
            typer.echo(f"Warning: Linear write-back failed: {e}", err=True)

    # Move ticket to processed/
    if not failed:
        processed = ticket_file.parent / "processed"
        processed.mkdir(exist_ok=True)
        ticket_file.rename(processed / ticket_file.name)
        typer.echo(f"Moved {ticket_file.name} → processed/")


@app.command()
def create_issue(
    title: str = typer.Option(..., "--title", help="Issue title."),
    description: str = typer.Option(..., "--description", help="Issue description (markdown)."),
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Create a single Linear issue in Backlog state (called by the /ticket skill)."""
    import os
    from dotenv import load_dotenv
    from .linear import LinearClient
    from .manifest import load_manifest

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        typer.echo("Error: LINEAR_API_KEY not set in .env", err=True)
        raise typer.Exit(1)

    m = load_manifest(manifest)
    if repo not in m.repos:
        typer.echo(f"Error: repo '{repo}' not in manifest", err=True)
        raise typer.Exit(1)

    repo_config = m.repos[repo]
    team_key = repo_config.linear_team or repo.upper()

    client = LinearClient(api_key)
    team_id = client.get_team_id(team_key)
    if not team_id:
        typer.echo(f"Error: team '{team_key}' not found in Linear", err=True)
        raise typer.Exit(1)

    state_id = client.get_state_id(team_key, "Backlog") or client.get_state_id(team_key, "Todo")
    if not state_id:
        typer.echo(f"Error: no Backlog/Todo state found for team {team_key}", err=True)
        raise typer.Exit(1)

    issue = client.create_issue(team_id=team_id, title=title, description=description, state_id=state_id)
    typer.echo(f"{issue['identifier']} — {issue['url']}")


@app.command()
def update_issue(
    identifier: str = typer.Option(..., "--identifier", help="Linear issue identifier (e.g. THM-5)."),
    description: str | None = typer.Option(None, "--description", help="New description (markdown)."),
    title: str | None = typer.Option(None, "--title", help="New title."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Update the title and/or description of an existing Linear issue."""
    import os
    from dotenv import load_dotenv
    from .linear import LinearClient

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        typer.echo("Error: LINEAR_API_KEY not set in .env", err=True)
        raise typer.Exit(1)

    if not title and not description:
        typer.echo("Error: provide at least --title or --description", err=True)
        raise typer.Exit(1)

    client = LinearClient(api_key)
    issue = client.get_issue_by_identifier(identifier)
    if not issue:
        typer.echo(f"Error: issue '{identifier}' not found in Linear", err=True)
        raise typer.Exit(1)

    updated = client.update_issue(
        issue_id=issue["id"],
        title=title or issue["title"],
        description=description,
    )
    typer.echo(f"{updated['identifier']} — {updated['url']}")


@app.command()
def ideate(
    brain_dump_file: Path | None = typer.Argument(None, help="Path to brain dump file (or omit to read from stdin)."),
    repo: str | None = typer.Option(None, "--repo", help="Repo key from manifest.yaml."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Turn a brain dump into a structured Linear ticket."""
    import os
    import sys
    from dotenv import load_dotenv
    from .ideate import ideate as _ideate

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")

    if brain_dump_file is not None:
        brain_dump = brain_dump_file.read_text()
    else:
        if sys.stdin.isatty():
            typer.echo("Reading from stdin (pipe text or pass a file path)...", err=True)
        brain_dump = sys.stdin.read()

    try:
        _ideate(brain_dump=brain_dump, repo_key=repo, manifest_path=manifest, yes=yes, api_key=api_key)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the factory version."""
    typer.echo("ai_factory 0.1.0 (phase 1 — single-shot executor)")


@app.command()
def run(
    no_pull: bool = typer.Option(False, "--no-pull", help="Skip pulling from Linear; run queue as-is."),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Skip stale branch cleanup."),
    ticket: str | None = typer.Option(None, "--ticket", help="Run a single ticket by ID (e.g. THM-5)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run pipeline but skip push, PR, and Linear write-back."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Pull ready Linear tickets and execute each, writing results back to Linear."""
    import os
    from dotenv import load_dotenv
    from .orchestrator import run as _run

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")

    try:
        _run(
            manifest_path=manifest,
            no_pull=no_pull,
            no_cleanup=no_cleanup,
            ticket_filter=ticket,
            dry_run=dry_run,
            api_key=api_key,
        )
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def pull_tickets(
    team: str | None = typer.Option(None, "--team", help="Restrict pull to a single Linear team key."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print would-be writes without touching disk."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml (default: ./manifest.yaml)."),
) -> None:
    """Pull ready Linear tickets to the local queue directory."""
    import os
    from dotenv import load_dotenv
    from .sync import pull_tickets as _pull

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")

    try:
        result = _pull(manifest_path=manifest, team_filter=team, dry_run=dry_run, api_key=api_key)
        result.print_summary()
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def run_ticket(
    ticket_file: Path = typer.Argument(..., help="Path to the ticket markdown file."),
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run pipeline but skip push, PR, and Linear write-back."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml (default: ./manifest.yaml)."),
) -> None:
    """Run a ticket through the executor pipeline and open a PR."""
    from .runner import run_ticket_from_file

    try:
        result = run_ticket_from_file(ticket_file, repo, manifest, dry_run=dry_run)
        if not result.success and not result.dry_run:
            typer.echo(f"Error: {result.error}", err=True)
            raise typer.Exit(1)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
