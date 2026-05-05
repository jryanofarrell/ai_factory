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
    stop_on_usage_limit: bool = True
    # Ordered list of executor providers to try; later entries are fallbacks.
    executor_providers: list[str] = field(default_factory=lambda: ["claude"])
    # Per-provider quota reset window in hours (overrides built-in defaults).
    quota_reset_hours: dict[str, float] = field(default_factory=dict)
    # Don't start a ticket on a provider if its utilization is at or above this fraction.
    max_utilization: float = 0.90


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

    raw_providers = data.get("executor_providers")
    executor_providers = list(raw_providers) if raw_providers else ["claude"]

    raw_quota = data.get("quota_reset_hours") or {}
    quota_reset_hours = {str(k): float(v) for k, v in raw_quota.items()}

    return Manifest(
        version=int(data.get("version", 1)),
        repos=repos,
        queue_dir=data.get("queue_dir", ".factory/queue"),
        stale_branch_days=int(data.get("stale_branch_days", 7)),
        secret_scan=bool(data.get("secret_scan", True)),
        stop_on_usage_limit=bool(data.get("stop_on_usage_limit", True)),
        executor_providers=executor_providers,
        quota_reset_hours=quota_reset_hours,
        max_utilization=float(data.get("max_utilization", 0.90)),
    )
