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
    files: str | None = typer.Option(
        None, "--files", help="Comma-separated list of changed files."
    ),
    duration: float = typer.Option(0.0, "--duration", help="Wall-clock seconds."),
    cost: float | None = typer.Option(None, "--cost", help="Cost in USD."),
    failed: bool = typer.Option(False, "--failed"),
    error: str | None = typer.Option(None, "--error"),
    branch: str | None = typer.Option(None, "--branch"),
    manifest: Path | None = typer.Option(None, "--manifest"),
) -> None:
    """Write Linear write-back and memory after a recipe-driven ticket run."""
    import os

    from dotenv import load_dotenv

    from .linear import LinearClient, LinearError
    from .manifest import load_manifest
    from .ticket import parse_ticket

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

    from .ticket import find_scope_recipe_mismatches

    warnings = find_scope_recipe_mismatches(description, repo_config.local_path)
    for w in warnings:
        typer.echo(f"warning: {w}", err=True)

    issue = client.create_issue(
        team_id=team_id, title=title, description=description, state_id=state_id
    )
    typer.echo(f"{issue['identifier']} — {issue['url']}")


@app.command()
def setup_team(
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Ensure a repo's Linear team has the 'Ready For AI' label. Idempotent — safe to re-run."""
    import os

    from dotenv import load_dotenv

    from .linear import READY_LABEL, LinearClient
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

    team_key = m.repos[repo].linear_team or repo.upper()

    client = LinearClient(api_key)
    team_id = client.get_team_id(team_key)
    if not team_id:
        typer.echo(
            f"Error: team '{team_key}' not found in Linear. "
            f"Create the team in Linear → Settings → Teams first.",
            err=True,
        )
        raise typer.Exit(1)

    existing = client.get_label_id(team_key, READY_LABEL)
    if existing:
        typer.echo(f"Team {team_key}: '{READY_LABEL}' label already exists (id={existing}).")
        return

    label_id = client.create_label(team_id=team_id, name=READY_LABEL)
    typer.echo(f"Team {team_key}: created '{READY_LABEL}' label (id={label_id}).")


@app.command()
def new_project(
    name: str = typer.Option(..., "--name", help="Linear team name, e.g. 'Billy AI'."),
    key: str = typer.Option(..., "--key", help="Linear team key/abbreviation, e.g. BIL."),
    repo: str = typer.Option(
        ..., "--repo", help="GitHub repo as owner/name, e.g. jryanofarrell/billy-ai."
    ),
    visibility: str = typer.Option("private", "--visibility", help="'public' or 'private'."),
    description: str | None = typer.Option(None, "--description", help="GitHub repo description."),
    repo_key: str | None = typer.Option(
        None, "--repo-key", help="Manifest key + repos/<key>/ dir (default: repo name)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan without creating anything."
    ),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Provision a new target project: Linear team + label, GitHub repo, clone, manifest entry."""
    import os

    from dotenv import load_dotenv

    from .linear import LinearError
    from .new_project import provision_project

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY")

    try:
        result = provision_project(
            team_name=name,
            team_key=key,
            github=repo,
            visibility=visibility,
            description=description,
            repo_key=repo_key,
            manifest_path=manifest,
            api_key=api_key,
            dry_run=dry_run,
        )
    except (ValueError, FileNotFoundError, RuntimeError, LinearError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    for note in result.notes:
        typer.echo(note)

    if not dry_run:
        typer.echo("")
        typer.echo(
            f"Project '{result.repo_key}' provisioned (team {result.team_key}, "
            f"{result.repo_full}, clone at {result.local_path})."
        )
        typer.echo(
            "Next: draft its first ticket(s) with /ticket — the first ticket scaffolds the "
            "repo. Mark them 'Ready For AI' in Linear, then run `factory run`."
        )


@app.command()
def update_issue(
    identifier: str = typer.Option(
        ..., "--identifier", help="Linear issue identifier (e.g. THM-5)."
    ),
    description: str | None = typer.Option(
        None, "--description", help="New description (markdown)."
    ),
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
    brain_dump_file: Path | None = typer.Argument(
        None, help="Path to brain dump file (or omit to read from stdin)."
    ),
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
        _ideate(
            brain_dump=brain_dump, repo_key=repo, manifest_path=manifest, yes=yes, api_key=api_key
        )
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the factory version."""
    typer.echo("ai_factory 0.1.0 (phase 1 — single-shot executor)")


@app.command()
def run(
    no_pull: bool = typer.Option(
        False, "--no-pull", help="Skip Linear and run local queue files as-is."
    ),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Skip stale branch cleanup."),
    ticket: str | None = typer.Option(
        None, "--ticket", help="Run a single ticket by ID (e.g. THM-5)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run pipeline but skip push, PR, and Linear write-back."
    ),
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
    team: str | None = typer.Option(
        None, "--team", help="Restrict pull to a single Linear team key."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print would-be writes without touching disk."
    ),
    manifest: Path | None = typer.Option(
        None, "--manifest", help="Path to manifest.yaml (default: ./manifest.yaml)."
    ),
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
def address_pr_comments(
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    pr: int = typer.Option(..., "--pr", help="Pull request number to inspect."),
    branch: str | None = typer.Option(
        None,
        "--branch",
        help="Optional local branch to create from the PR head before addressing comments.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run the executor but skip committing and pushing code changes."
    ),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml."),
) -> None:
    """Address GitHub PR comments with the configured executor."""
    from .manifest import load_manifest
    from .pr_comments import address_pr_comments as _address
    from .quota_tracker import QuotaTracker

    try:
        m = load_manifest(manifest)
        if repo not in m.repos:
            typer.echo(f"Error: repo '{repo}' not in manifest", err=True)
            raise typer.Exit(1)

        base_dir = (manifest or Path("manifest.yaml")).resolve().parent
        quota_tracker = QuotaTracker(
            state_file=base_dir / ".factory" / "quota_state.json",
            reset_hours=m.quota_reset_hours or None,
        )
        result = _address(
            repo=m.repos[repo],
            pr_number=pr,
            providers=m.executor_providers,
            quota_tracker=quota_tracker,
            max_utilization=m.max_utilization,
            branch=branch,
            dry_run=dry_run,
        )
        if result.committed:
            typer.echo(
                f"Committed and pushed `{result.commit_message}` to {result.branch} "
                f"for {result.pr_url}."
            )
        elif result.files_changed:
            typer.echo(f"Dry run complete; changed files: {', '.join(result.files_changed)}")
        else:
            typer.echo(f"No code changes to push for {result.pr_url}.")
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def run_ticket(
    ticket_file: Path = typer.Argument(..., help="Path to the ticket markdown file."),
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run pipeline but skip push, PR, and Linear write-back."
    ),
    manifest: Path | None = typer.Option(
        None, "--manifest", help="Path to manifest.yaml (default: ./manifest.yaml)."
    ),
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
