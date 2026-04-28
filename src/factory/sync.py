from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .linear import LinearClient, LinearError
from .manifest import Manifest, load_manifest
from .ticket import Ticket, _extract_section


@dataclass
class PullResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def print_summary(self) -> None:
        total = len(self.written) + len(self.skipped) + len(self.failed)
        print(
            f"\nPulled {total} ticket(s). "
            f"{len(self.written)} written, "
            f"{len(self.skipped)} skipped (unchanged), "
            f"{len(self.failed)} failed validation."
        )
        for identifier, reason in self.failed:
            print(f"  FAIL {identifier}: {reason}")


def pull_tickets(
    manifest_path: Path | None = None,
    team_filter: str | None = None,
    dry_run: bool = False,
    api_key: str | None = None,
) -> PullResult:
    manifest = load_manifest(manifest_path)
    queue_dir = Path(manifest.queue_dir)
    if not queue_dir.is_absolute():
        base = (manifest_path or Path("manifest.yaml")).resolve().parent
        queue_dir = base / queue_dir

    if api_key is None:
        raise ValueError(
            "LINEAR_API_KEY is not set. "
            "Add it to .env (get it from Linear → Settings → API → Personal API keys)."
        )

    client = LinearClient(api_key)
    result = PullResult()

    teams = _teams_from_manifest(manifest, team_filter)
    if not teams:
        print("No teams found in manifest" + (f" matching --team {team_filter}" if team_filter else "") + ".")
        return result

    for team_key, repo_key in teams.items():
        print(f"Querying Linear team {team_key}...")
        try:
            issues = client.get_ready_issues(team_key)
        except LinearError as e:
            print(f"  Warning: skipping team {team_key} — {e}")
            continue

        if not issues:
            print(f"  No ready issues for team {team_key}.")
            continue

        for issue in issues:
            identifier = issue["identifier"]
            try:
                ticket = _issue_to_ticket(issue, manifest, repo_key)
                _validate_ticket(ticket)
            except ValueError as e:
                result.failed.append((identifier, str(e)))
                print(f"  SKIP {identifier}: {e}")
                continue

            content = ticket.to_markdown()
            dest = queue_dir / f"{identifier.lower()}.md"

            if not dry_run:
                queue_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists() and _hash(dest.read_text()) == _hash(content):
                    result.skipped.append(identifier)
                    print(f"  SKIP {identifier} (unchanged)")
                    continue
                dest.write_text(content)
                result.written.append(identifier)
                print(f"  WROTE {dest}")
            else:
                print(f"  DRY-RUN {dest}")
                print(content)
                result.written.append(identifier)

    return result


def _teams_from_manifest(manifest: Manifest, team_filter: str | None) -> dict[str, str]:
    teams: dict[str, str] = {}
    for repo_key, repo in manifest.repos.items():
        key = repo.linear_team or repo_key.upper()
        if team_filter and key != team_filter.upper():
            continue
        teams[key] = repo_key
    return teams


def _issue_to_ticket(issue: dict[str, Any], manifest: Manifest, default_repo: str) -> Ticket:
    description = issue.get("description") or ""
    team_key = issue["team"]["key"]

    target_repo_section = _extract_section(description, "Target Repo")
    if target_repo_section:
        target_repo = target_repo_section.strip().splitlines()[0].strip()
        if target_repo not in manifest.repos:
            raise ValueError(f"target_repo '{target_repo}' not found in manifest")
    else:
        target_repo = default_repo

    acceptance_criteria = _extract_section(description, "Acceptance Criteria")
    if not acceptance_criteria:
        raise ValueError("description has no '## Acceptance Criteria' section")

    scope_paths: list[str] = []
    scope_text = _extract_section(description, "Scope Paths")
    if scope_text:
        for line in scope_text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                scope_paths.append(line)

    budget_tokens = 50_000
    budget_minutes = 30
    budget_text = _extract_section(description, "Budget")
    if budget_text:
        for line in budget_text.splitlines():
            if "tokens:" in line:
                try:
                    budget_tokens = int(line.split("tokens:")[1].strip())
                except (ValueError, IndexError):
                    pass
            if "minutes:" in line:
                try:
                    budget_minutes = int(line.split("minutes:")[1].strip())
                except (ValueError, IndexError):
                    pass

    notes = _extract_section(description, "Notes") or ""

    return Ticket(
        id=issue["identifier"],
        title=issue["title"],
        target_repo=target_repo,
        acceptance_criteria=acceptance_criteria,
        scope_paths=scope_paths,
        budget_tokens=budget_tokens,
        budget_minutes=budget_minutes,
        linear_url=issue["url"],
        linear_id=issue.get("id"),
        notes=notes,
    )


def _validate_ticket(ticket: Ticket) -> None:
    if not ticket.id:
        raise ValueError("missing id")
    if not ticket.title:
        raise ValueError("missing title")
    if not ticket.target_repo:
        raise ValueError("missing target_repo")
    if not ticket.acceptance_criteria:
        raise ValueError("missing acceptance_criteria")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
