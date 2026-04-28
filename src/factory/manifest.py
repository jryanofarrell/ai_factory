from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    github: str
    local_path: Path
    default_branch: str
    test_command: str = ""
    build_command: str = ""
    linear_team: str = ""


@dataclass
class Manifest:
    version: int
    repos: dict[str, RepoConfig]
    queue_dir: str = ".factory/queue"
    stale_branch_days: int = 7
    secret_scan: bool = True


def load_manifest(path: Path | None = None) -> Manifest:
    if path is None:
        path = Path("manifest.yaml")
    if not path.exists():
        raise FileNotFoundError(
            f"manifest.yaml not found at {path}. "
            "Copy manifest.example.yaml to manifest.yaml and edit local_path."
        )
    with path.open() as f:
        data = yaml.safe_load(f)

    manifest_dir = path.resolve().parent

    repos: dict[str, RepoConfig] = {}
    for key, repo_data in (data.get("repos") or {}).items():
        raw_path = repo_data.get("local_path") or f"repos/{key}"
        raw = Path(os.path.expanduser(raw_path))
        resolved = raw if raw.is_absolute() else (manifest_dir / raw)
        repos[key] = RepoConfig(
            github=repo_data["github"],
            local_path=resolved,
            default_branch=repo_data.get("default_branch", "main"),
            test_command=repo_data.get("test_command", ""),
            build_command=repo_data.get("build_command", ""),
            linear_team=repo_data.get("linear_team", ""),
        )

    return Manifest(
        version=int(data.get("version", 1)),
        repos=repos,
        queue_dir=data.get("queue_dir", ".factory/queue"),
        stale_branch_days=int(data.get("stale_branch_days", 7)),
        secret_scan=bool(data.get("secret_scan", True)),
    )
