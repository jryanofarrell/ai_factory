"""Provision a brand-new target project (Linear team + GitHub repo + manifest entry).

This is the onboarding step for a new repo, per ADR-020. It composes calls the
factory already makes elsewhere — the Linear GraphQL API and the ``gh`` CLI — into
one idempotent command. It deliberately does NOT scaffold the repo's contents:
the new repo is left empty (a stub ``main`` only), and its first ticket establishes
conventions and the ``.ai/`` layout like any other work.

Every step checks for the existing artifact first, so the whole flow is safe to
re-run if one step fails partway through.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .linear import READY_LABEL, LinearClient


@dataclass
class ProvisionResult:
    repo_key: str
    team_key: str
    team_created: bool
    label_created: bool
    repo_full: str
    repo_created: bool
    cloned: bool
    manifest_updated: bool
    local_path: Path
    notes: list[str] = field(default_factory=list)


def register_repo_in_manifest(
    manifest_path: Path,
    repo_key: str,
    github: str,
    linear_team: str,
    default_branch: str = "main",
) -> bool:
    """Insert a repo block under ``repos:`` in the manifest.

    Returns ``False`` (and leaves the file untouched) if ``repo_key`` is already
    registered. Uses text insertion rather than a yaml round-trip so the
    manifest's comments and formatting survive (pyyaml's dumper drops both).
    Assumes the conventional manifest shape: a top-level ``repos:`` key on its
    own line with 2-space-indented children.
    """
    text = manifest_path.read_text()
    data = yaml.safe_load(text) or {}
    if repo_key in (data.get("repos") or {}):
        return False

    block = "\n".join(
        [
            f"  {repo_key}:",
            f"    github: {github}",
            f"    default_branch: {default_branch}",
            f"    linear_team: {linear_team}",
        ]
    )

    out: list[str] = []
    inserted = False
    for line in text.splitlines():
        out.append(line)
        if not inserted and line.rstrip() == "repos:":
            out.append(block)
            inserted = True
    if not inserted:
        # No `repos:` key yet — create the section at the end of the file.
        if out and out[-1].strip():
            out.append("")
        out.append("repos:")
        out.append(block)

    manifest_path.write_text("\n".join(out) + "\n")
    return True


def _gh_repo_exists(repo_full: str) -> bool:
    result = subprocess.run(["gh", "repo", "view", repo_full], capture_output=True, text=True)
    return result.returncode == 0


def _run_gh(cmd: list[str], failure_msg: str) -> None:
    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{failure_msg}\n{result.stderr.strip()}")


def _ensure_team(
    client: LinearClient, team_name: str, team_key: str, dry_run: bool, notes: list[str]
) -> tuple[bool, str | None]:
    team_id = client.get_team_id(team_key)
    if team_id:
        notes.append(f"Linear team {team_key} already exists.")
        return False, team_id
    if dry_run:
        notes.append(f"[dry-run] would create Linear team '{team_name}' (key {team_key}).")
        return True, None
    team = client.create_team(team_name, team_key)
    notes.append(f"Created Linear team '{team['name']}' (key {team['key']}).")
    return True, team["id"]


def _ensure_label(
    client: LinearClient, team_key: str, team_id: str | None, dry_run: bool, notes: list[str]
) -> bool:
    existing = client.get_label_id(team_key, READY_LABEL)
    if existing:
        notes.append(f"Label '{READY_LABEL}' already on team {team_key}.")
        return False
    if dry_run or team_id is None:
        notes.append(f"[dry-run] would create label '{READY_LABEL}' on team {team_key}.")
        return True
    client.create_label(team_id=team_id, name=READY_LABEL)
    notes.append(f"Created label '{READY_LABEL}' on team {team_key}.")
    return True


def _ensure_repo(
    repo_full: str, visibility: str, description: str | None, dry_run: bool, notes: list[str]
) -> bool:
    if _gh_repo_exists(repo_full):
        notes.append(f"GitHub repo {repo_full} already exists.")
        return False
    cmd = ["gh", "repo", "create", repo_full, f"--{visibility}", "--add-readme"]
    if description:
        cmd += ["--description", description]
    if dry_run:
        notes.append(f"[dry-run] would create GitHub repo {repo_full} ({visibility}).")
        return True
    _run_gh(cmd, f"gh repo create failed for {repo_full}:")
    notes.append(f"Created GitHub repo {repo_full} ({visibility}, seeded with a stub main).")
    return True


def _ensure_clone(repo_full: str, local_path: Path, dry_run: bool, notes: list[str]) -> bool:
    if (local_path / ".git").exists():
        notes.append(f"Local clone already present at {local_path}.")
        return False
    if dry_run:
        notes.append(f"[dry-run] would clone {repo_full} -> {local_path}.")
        return True
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _run_gh(
        ["gh", "repo", "clone", repo_full, str(local_path)],
        f"gh repo clone failed for {repo_full}:",
    )
    notes.append(f"Cloned {repo_full} -> {local_path}.")
    return True


def provision_project(
    *,
    team_name: str,
    team_key: str,
    github: str,
    visibility: str = "private",
    description: str | None = None,
    repo_key: str | None = None,
    manifest_path: Path | None = None,
    api_key: str | None = None,
    dry_run: bool = False,
) -> ProvisionResult:
    """Provision a new target project end to end. Idempotent and re-runnable.

    Steps: ensure Linear team + "Ready For AI" label, create the GitHub repo
    (seeded with a stub ``main``), clone it to ``repos/<key>/``, and register it
    in ``manifest.yaml``. ``api_key`` is the Linear key; when absent, the Linear
    steps are skipped (GitHub + manifest still run).
    """
    if visibility not in ("public", "private"):
        raise ValueError("visibility must be 'public' or 'private'")
    if "/" not in github:
        raise ValueError("github must be 'owner/name', e.g. jryanofarrell/billy-ai")
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found. Install it and run `gh auth login`.")

    _, name = github.split("/", 1)
    repo_key = repo_key or name
    manifest_path = manifest_path or Path("manifest.yaml")
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.yaml not found at {manifest_path}.")
    base_dir = manifest_path.resolve().parent
    local_path = base_dir / "repos" / repo_key

    notes: list[str] = []

    team_created = False
    label_created = False
    if api_key:
        client = LinearClient(api_key)
        team_created, team_id = _ensure_team(client, team_name, team_key, dry_run, notes)
        label_created = _ensure_label(client, team_key, team_id, dry_run, notes)
    else:
        notes.append("LINEAR_API_KEY not set — skipped Linear team + label creation.")

    repo_created = _ensure_repo(github, visibility, description, dry_run, notes)
    cloned = _ensure_clone(github, local_path, dry_run, notes)

    if dry_run:
        data = yaml.safe_load(manifest_path.read_text()) or {}
        manifest_updated = repo_key not in (data.get("repos") or {})
        notes.append(
            f"[dry-run] would register '{repo_key}' in {manifest_path.name}."
            if manifest_updated
            else f"'{repo_key}' already in {manifest_path.name}."
        )
    else:
        manifest_updated = register_repo_in_manifest(manifest_path, repo_key, github, team_key)
        notes.append(
            f"Registered '{repo_key}' in {manifest_path.name}."
            if manifest_updated
            else f"'{repo_key}' already in {manifest_path.name}."
        )

    return ProvisionResult(
        repo_key=repo_key,
        team_key=team_key,
        team_created=team_created,
        label_created=label_created,
        repo_full=github,
        repo_created=repo_created,
        cloned=cloned,
        manifest_updated=manifest_updated,
        local_path=local_path,
        notes=notes,
    )
