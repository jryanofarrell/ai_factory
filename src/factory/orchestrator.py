from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import typer

from .linear import LinearClient, LinearError
from .manifest import load_manifest
from .runner import RunResult, run_ticket
from .sync import pull_tickets
from .ticket import parse_ticket


def run(
    manifest_path: Path | None = None,
    no_pull: bool = False,
    ticket_filter: str | None = None,
    api_key: str | None = None,
) -> None:
    manifest = load_manifest(manifest_path)
    base_dir = (manifest_path or Path("manifest.yaml")).resolve().parent

    queue_dir = Path(manifest.queue_dir)
    if not queue_dir.is_absolute():
        queue_dir = base_dir / queue_dir
    processed_dir = queue_dir / "processed"
    runs_dir = base_dir / ".factory" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: pull tickets
    if not no_pull:
        if api_key is None:
            raise ValueError(
                "LINEAR_API_KEY is not set. Add it to .env to enable pull. "
                "Use --no-pull to skip."
            )
        typer.echo("Pulling tickets from Linear...")
        pull_tickets(manifest_path=manifest_path, api_key=api_key)

    # Step 2: build work list
    ticket_files = sorted(queue_dir.glob("*.md"))
    if ticket_filter:
        ticket_files = [f for f in ticket_files if ticket_filter.lower() in f.stem.lower()]

    if not ticket_files:
        typer.echo("No tickets in queue.")
        return

    # Step 3: batch state file
    batch_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_file = runs_dir / f"{batch_ts}.json"
    batch: dict = {"started_at": batch_ts, "tickets": {}}

    client = LinearClient(api_key) if api_key else None

    # Ctrl-C handler
    interrupted = False

    def _handle_sigint(sig, frame):
        nonlocal interrupted
        interrupted = True
        typer.echo("\nInterrupted — finishing current ticket then stopping.", err=True)

    signal.signal(signal.SIGINT, _handle_sigint)

    results: list[RunResult] = []

    # Step 4: process each ticket
    for ticket_file in ticket_files:
        if interrupted:
            break

        try:
            ticket = parse_ticket(ticket_file)
        except ValueError as e:
            typer.echo(f"SKIP {ticket_file.name}: {e}", err=True)
            continue

        if ticket.target_repo not in manifest.repos:
            typer.echo(f"SKIP {ticket.id}: target_repo '{ticket.target_repo}' not in manifest", err=True)
            continue

        repo = manifest.repos[ticket.target_repo]
        team_key = repo.linear_team or ticket.target_repo.upper()

        typer.echo(f"\n{'─'*60}")
        typer.echo(f"Running {ticket.id}: {ticket.title}")
        typer.echo(f"{'─'*60}")

        try:
            result = run_ticket(ticket, repo, capture_cost=True)
        except KeyboardInterrupt:
            batch["tickets"][ticket.id] = {"status": "interrupted"}
            _save_batch(batch_file, batch)
            raise

        results.append(result)

        # Record in batch
        record: dict = {
            "status": "succeeded" if result.success else "failed",
            "duration_s": round(result.duration_s, 1),
        }
        if result.pr_url:
            record["pr_url"] = result.pr_url
        if result.cost_usd is not None:
            record["cost_usd"] = round(result.cost_usd, 4)
        if result.branch:
            record["branch"] = result.branch
        if result.error:
            record["reason"] = result.error
        batch["tickets"][ticket.id] = record
        _save_batch(batch_file, batch)

        # Write back to Linear
        if client and ticket.linear_id:
            _write_back(client, ticket, team_key, result)

        # Move processed ticket
        if result.success:
            processed_dir.mkdir(parents=True, exist_ok=True)
            ticket_file.rename(processed_dir / ticket_file.name)

    # Step 5: summary
    _print_summary(results, batch_file)


def _write_back(
    client: LinearClient,
    ticket,
    team_key: str,
    result: RunResult,
) -> None:
    try:
        if result.success:
            dur = _fmt_duration(result.duration_s)
            cost = f"${result.cost_usd:.2f}" if result.cost_usd is not None else "n/a"
            body = f"PR opened: {result.pr_url}\nDuration: {dur} · Cost: {cost}"
            client.comment_on_issue(ticket.linear_id, body)

            state_id = client.get_state_id(team_key, "In Review")
            if state_id:
                client.transition_issue(ticket.linear_id, state_id)
            else:
                typer.echo(f"  Warning: 'In Review' state not found for team {team_key}", err=True)
        else:
            dur = _fmt_duration(result.duration_s)
            branch_note = f"\nBranch preserved: `{result.branch}`" if result.branch else ""
            body = f"Execution failed: {result.error}\nDuration: {dur}{branch_note}"
            client.comment_on_issue(ticket.linear_id, body)

            state_id = client.get_state_id(team_key, "Failed for Agent")
            if state_id:
                client.transition_issue(ticket.linear_id, state_id)
            else:
                label_id = client.get_label_id(team_key, "factory:failed")
                if label_id:
                    client.apply_label(ticket.linear_id, label_id)
                else:
                    typer.echo(
                        f"  Warning: 'Failed for Agent' state and 'factory:failed' label "
                        f"not found for team {team_key}. Create one in Linear.",
                        err=True,
                    )
    except LinearError as e:
        typer.echo(f"  Warning: Linear write-back failed for {ticket.id}: {e}", err=True)


def _print_summary(results: list[RunResult], batch_file: Path) -> None:
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    typer.echo(f"\n{'═'*60}")
    typer.echo(f"Run complete: {len(succeeded)} succeeded, {len(failed)} failed.")
    for r in results:
        dur = _fmt_duration(r.duration_s)
        cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else ""
        suffix = f"({dur}{', ' + cost if cost else ''})"
        if r.success:
            typer.echo(f"  ✓ {r.ticket_id} → {r.pr_url} {suffix}")
        else:
            typer.echo(f"  ✗ {r.ticket_id} → FAILED: {r.error} {suffix}")

    total_s = sum(r.duration_s for r in results)
    total_cost = sum(r.cost_usd for r in results if r.cost_usd is not None)
    typer.echo(f"Total: {_fmt_duration(total_s)}" + (f", ${total_cost:.2f}" if total_cost else ""))
    typer.echo(f"Batch log: {batch_file}")


def _save_batch(path: Path, batch: dict) -> None:
    path.write_text(json.dumps(batch, indent=2))


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"
