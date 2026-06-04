from __future__ import annotations

import json
import signal
from datetime import UTC, datetime
from pathlib import Path

import typer

from .chains import ChainCycleError, group_into_chains
from .git_ops import cleanup_stale_branches, create_memory_pr
from .linear import LinearClient, LinearError
from .manifest import load_manifest
from .quota_tracker import QuotaTracker
from .runner import ChainResult, RunResult, run_chain
from .sync import pull_tickets
from .ticket import Ticket, parse_ticket


def run(
    manifest_path: Path | None = None,
    no_pull: bool = False,
    no_cleanup: bool = False,
    ticket_filter: str | None = None,
    dry_run: bool = False,
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
    log_dir = base_dir / "logs"

    quota_tracker = QuotaTracker(
        state_file=base_dir / ".factory" / "quota_state.json",
        reset_hours=manifest.quota_reset_hours or None,
    )

    providers = manifest.executor_providers
    if len(providers) > 1:
        typer.echo(f"Executor providers (in order): {', '.join(providers)}")
    else:
        typer.echo(f"Executor provider: {providers[0]}")

    # Step 1: branch hygiene
    if not no_cleanup:
        typer.echo("Checking for stale factory/* branches...")
        for repo_key, repo in manifest.repos.items():
            if repo.local_path.exists():
                deleted = cleanup_stale_branches(
                    repo.local_path,
                    repo.github,
                    stale_days=manifest.stale_branch_days,
                )
                if not deleted:
                    typer.echo(f"  {repo_key}: no stale branches.")

    pulled_tickets: list[Ticket] | None = None

    # Step 2: pull tickets
    if not no_pull:
        if api_key is None:
            raise ValueError(
                "LINEAR_API_KEY is not set. Add it to .env to enable pull. Use --no-pull to skip."
            )
        typer.echo("Pulling tickets from Linear...")
        pull_result = pull_tickets(manifest_path=manifest_path, api_key=api_key, write_files=False)
        pulled_tickets = pull_result.tickets

    # Step 3: build work list
    if no_pull:
        work_items: list[tuple[Ticket | None, Path | None]] = [
            (None, path) for path in sorted(queue_dir.glob("*.md"))
        ]
        if ticket_filter:
            work_items = [
                item for item in work_items if ticket_filter.lower() in item[1].stem.lower()
            ]
        empty_message = "No tickets in local queue."
    else:
        assert pulled_tickets is not None
        work_items = [(ticket, None) for ticket in pulled_tickets]
        if ticket_filter:
            work_items = [
                item for item in work_items if ticket_filter.lower() in item[0].id.lower()
            ]
        empty_message = "No ready tickets in Linear."

    if not work_items:
        typer.echo(empty_message)
        return

    # Step 4: batch state
    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    batch_file = runs_dir / f"{batch_ts}.json"
    batch: dict = {"started_at": batch_ts, "tickets": {}}

    client = LinearClient(api_key) if api_key and not dry_run else None

    interrupted = False

    def _handle_sigint(sig, frame):
        nonlocal interrupted
        interrupted = True
        typer.echo("\nInterrupted — finishing current ticket then stopping.", err=True)

    signal.signal(signal.SIGINT, _handle_sigint)

    processing_dir = queue_dir / "processing"
    results: list[RunResult] = []
    successful_repos: set[str] = set()
    # Track factory branches per repo so create_memory_pr can pull their memory files
    session_branches: dict[str, list[str]] = {}

    # Step 5a: parse all tickets up front, filter unparseable / bad-target ones
    parsed: list[tuple[Ticket, Path | None]] = []
    for ticket_or_none, ticket_file in work_items:
        if ticket_or_none is None:
            assert ticket_file is not None
            try:
                t = parse_ticket(ticket_file)
            except ValueError as e:
                typer.echo(f"SKIP {ticket_file.name}: {e}", err=True)
                continue
        else:
            t = ticket_or_none
        if t.target_repo not in manifest.repos:
            typer.echo(
                f"SKIP {t.id}: target_repo '{t.target_repo}' not in manifest", err=True
            )
            continue
        parsed.append((t, ticket_file))

    if not parsed:
        _print_summary(results, batch_file, dry_run)
        return

    # Step 5b: group into dependency chains (ADR-019)
    # For each ticket dep referenced but NOT in the queue, query Linear:
    # the dep counts as satisfied only if its state.type is `completed`.
    # Anything else (Backlog, In Progress, In Review, Failed) → dependent
    # ticket refuses to run with an error. If no Linear client is available
    # (dry run, no API key), fall back to trust-the-user with a warning.
    parsed_ids = {t.id for t, _ in parsed}
    referenced = {dep for t, _ in parsed for dep in t.depends_on}
    needs_verify = referenced - parsed_ids
    merged_verified: set[str] = set()

    if needs_verify:
        if client is None:
            typer.echo(
                f"WARN: cannot verify {len(needs_verify)} external dep(s) — no Linear "
                "client (dry-run or no API key). Treating as merged.",
                err=True,
            )
            merged_verified = set(needs_verify)
        else:
            for dep_id in sorted(needs_verify):
                if client.is_issue_merged(dep_id):
                    merged_verified.add(dep_id)

    try:
        grouped = group_into_chains(
            [t for t, _ in parsed],
            merged_ticket_ids=merged_verified,
        )
    except ChainCycleError as e:
        typer.echo(f"\nERROR: dependency cycle in queue — {e}", err=True)
        typer.echo("Aborting. Fix the cycle in Linear and re-run.", err=True)
        return

    file_by_id = {t.id: f for t, f in parsed}

    for refused_ticket, bad_deps in grouped.skipped_cross_repo:
        typer.echo(
            f"ERROR {refused_ticket.id}: cross-repo dependency on "
            f"{', '.join(bad_deps)} (different target_repo). Refusing to run.",
            err=True,
        )
    for refused_ticket, missing in grouped.skipped_unsatisfied:
        typer.echo(
            f"ERROR {refused_ticket.id}: depends on {', '.join(missing)} which "
            "is neither in the queue nor merged on main. Refusing to run.",
            err=True,
        )

    # Step 5c: process each chain
    for chain in grouped.chains:
        if interrupted:
            break

        repo = manifest.repos[chain[0].target_repo]
        team_key = repo.linear_team or chain[0].target_repo.upper()

        # Stage local files for every ticket in this chain.
        staging_files: dict[str, Path] = {}
        for t in chain:
            f = file_by_id.get(t.id)
            if f is not None:
                processing_dir.mkdir(parents=True, exist_ok=True)
                staged = processing_dir / f.name
                f.rename(staged)
                staging_files[t.id] = staged

        typer.echo(f"\n{'─' * 60}")
        if len(chain) == 1:
            typer.echo(
                f"{'[DRY-RUN] ' if dry_run else ''}Running {chain[0].id}: {chain[0].title}"
            )
        else:
            chain_label = " → ".join(t.id for t in chain)
            typer.echo(f"{'[DRY-RUN] ' if dry_run else ''}Running chain {chain_label}")
        typer.echo(f"{'─' * 60}")

        try:
            chain_result: ChainResult = run_chain(
                chain,
                repo,
                capture_cost=True,
                dry_run=dry_run,
                log_dir=log_dir,
                quota_tracker=quota_tracker,
                executor_providers=providers,
                max_utilization=manifest.max_utilization,
            )
        except KeyboardInterrupt:
            for t in chain:
                batch["tickets"][t.id] = {"status": "interrupted"}
            _save_batch(batch_file, batch)
            raise

        # For each ticket in the chain that was actually attempted, record
        # results, write back to Linear, and stage the file appropriately.
        attempted_ids = {rr.ticket_id for rr in chain_result.per_ticket}
        for rr in chain_result.per_ticket:
            results.append(rr)
            ticket = next(t for t in chain if t.id == rr.ticket_id)
            _record_batch_entry(batch, ticket, rr, dry_run)
            _save_batch(batch_file, batch)
            if client and ticket.linear_id:
                _write_back(client, ticket, team_key, rr)
            if rr.success and not dry_run:
                successful_repos.add(ticket.target_repo)
                if rr.branch:
                    session_branches.setdefault(ticket.target_repo, []).append(rr.branch)
                staged = staging_files.get(ticket.id)
                if staged is not None:
                    processed_dir.mkdir(parents=True, exist_ok=True)
                    staged.rename(processed_dir / staged.name)
            else:
                staged = staging_files.get(ticket.id)
                if staged is not None:
                    staged.rename(queue_dir / staged.name)

        # Tickets that the chain never attempted (downstream of a mid-chain
        # failure): un-stage them back to the queue, mark them queued.
        for t in chain:
            if t.id in attempted_ids:
                continue
            batch["tickets"][t.id] = {"status": "skipped_after_chain_failure"}
            _save_batch(batch_file, batch)
            staged = staging_files.get(t.id)
            if staged is not None:
                staged.rename(queue_dir / staged.name)

        # If any chained ticket hit the usage limit and the manifest says
        # stop, end the session.
        if (
            any(rr.usage_limit_hit for rr in chain_result.per_ticket)
            and manifest.stop_on_usage_limit
        ):
            typer.echo(
                "\nUsage limit detected. Stopping after this chain to avoid overage charges.\n"
                "Set `stop_on_usage_limit: false` in manifest.yaml to disable this behaviour.",
                err=True,
            )
            _save_batch(batch_file, batch)
            _push_memory_prs(manifest, successful_repos, session_branches, dry_run)
            _print_summary(results, batch_file, dry_run)
            return

    _push_memory_prs(manifest, successful_repos, session_branches, dry_run)
    _print_summary(results, batch_file, dry_run)


def _record_batch_entry(
    batch: dict, ticket: Ticket, result: RunResult, dry_run: bool
) -> None:
    record: dict = {
        "status": "dry_run" if dry_run else ("succeeded" if result.success else "failed")
    }
    record["duration_s"] = round(result.duration_s, 1)
    if result.pr_url:
        record["pr_url"] = result.pr_url
    if result.cost_usd is not None:
        record["cost_usd"] = round(result.cost_usd, 4)
    if result.branch:
        record["branch"] = result.branch
    if result.error:
        record["reason"] = result.error
    if result.scope_violations:
        record["scope_advisory"] = result.scope_violations
    batch["tickets"][ticket.id] = record


def _push_memory_prs(
    manifest,
    successful_repos: set[str],
    session_branches: dict[str, list[str]],
    dry_run: bool,
) -> None:
    if dry_run or not successful_repos:
        return
    typer.echo(f"\n{'─' * 60}")
    typer.echo("Opening memory index PR(s)...")
    for repo_key in successful_repos:
        repo = manifest.repos[repo_key]
        try:
            pr_url = create_memory_pr(
                repo.local_path,
                repo.default_branch,
                repo.github,
                session_branches=session_branches.get(repo_key),
            )
            if pr_url:
                typer.echo(f"  {repo_key}: memory PR → {pr_url}")
            else:
                typer.echo(f"  {repo_key}: memory index unchanged, no PR needed")
        except Exception as e:
            typer.echo(f"  Warning: memory PR failed for {repo_key}: {e}", err=True)


def _write_back(client: LinearClient, ticket, team_key: str, result: RunResult) -> None:
    try:
        if result.success:
            dur = _fmt_duration(result.duration_s)
            cost = f"${result.cost_usd:.2f}" if result.cost_usd is not None else "n/a"
            body = f"PR opened: {result.pr_url}\nDuration: {dur} · Cost: {cost}"
            body += _scope_advisory_comment(result)
            client.comment_on_issue(ticket.linear_id, body)
            state_id = client.get_state_id(team_key, "In Review")
            if state_id:
                client.transition_issue(ticket.linear_id, state_id)
        else:
            dur = _fmt_duration(result.duration_s)
            branch_note = f"\nBranch preserved: `{result.branch}`" if result.branch else ""
            body = (
                f"Execution failed: {result.error}\n"
                f"Reason: {result.reason}\n"
                f"Duration: {dur}{branch_note}"
            )
            body += _scope_advisory_comment(result)
            client.comment_on_issue(ticket.linear_id, body)
            state_id = client.get_state_id(team_key, "Failed for Agent")
            if state_id:
                client.transition_issue(ticket.linear_id, state_id)
            else:
                label_id = client.get_label_id(team_key, "factory:failed")
                if label_id:
                    client.apply_label(ticket.linear_id, label_id)
    except LinearError as e:
        typer.echo(f"  Warning: Linear write-back failed for {ticket.id}: {e}", err=True)


def _print_summary(results: list[RunResult], batch_file: Path, dry_run: bool) -> None:
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    label = "dry-run" if dry_run else "complete"

    typer.echo(f"\n{'═' * 60}")
    typer.echo(f"Run {label}: {len(succeeded)} succeeded, {len(failed)} failed.")
    for r in results:
        dur = _fmt_duration(r.duration_s)
        cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else ""
        suffix = f"({dur}{', ' + cost if cost else ''})"
        if r.success:
            target = r.pr_url or "branch preserved (dry-run)"
            typer.echo(f"  ✓ {r.ticket_id} → {target} {suffix}")
        else:
            typer.echo(f"  ✗ {r.ticket_id} → FAILED: {r.error} {suffix}")
        if r.scope_violations:
            typer.echo(f"    Scope advisory: {', '.join(r.scope_violations)}")

    total_s = sum(r.duration_s for r in results)
    total_cost = sum(r.cost_usd for r in results if r.cost_usd is not None)
    typer.echo(f"Total: {_fmt_duration(total_s)}" + (f", ${total_cost:.2f}" if total_cost else ""))
    typer.echo(f"Batch log: {batch_file}")


def _scope_advisory_comment(result: RunResult) -> str:
    if not result.scope_violations:
        return ""
    files = "\n".join(f"- `{path}`" for path in result.scope_violations)
    return f"\n\nScope advisory: changed files outside ticket scope:\n{files}"


def _save_batch(path: Path, batch: dict) -> None:
    path.write_text(json.dumps(batch, indent=2))


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"
