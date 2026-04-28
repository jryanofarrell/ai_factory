from __future__ import annotations

import json
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentResult:
    exit_code: int
    cost_usd: float | None = None
    duration_ms: int | None = None
    output: str | None = None
    timed_out: bool = False
    tokens_used: int | None = None


def detect_install_command(local_path: Path) -> str | None:
    if (local_path / "package.json").exists():
        return "npm install"
    if (local_path / "pyproject.toml").exists():
        return "uv sync"
    if (local_path / "requirements.txt").exists():
        return "pip install -r requirements.txt"
    if (local_path / "Gemfile").exists():
        return "bundle install"
    return None


def detect_test_command(local_path: Path) -> str | None:
    if _makefile_has_target(local_path, "test"):
        return "make test"
    if (local_path / "package.json").exists():
        try:
            pkg = json.loads((local_path / "package.json").read_text())
            if "test" in (pkg.get("scripts") or {}):
                return "npm test"
        except (json.JSONDecodeError, OSError):
            pass
    if (local_path / "pyproject.toml").exists():
        return "uv run pytest"
    if (local_path / "requirements.txt").exists():
        return "pytest"
    if (local_path / "Gemfile").exists():
        return "bundle exec rspec"
    return None


def _makefile_has_target(local_path: Path, target: str) -> bool:
    makefile = local_path / "Makefile"
    if not makefile.exists():
        return False
    try:
        for line in makefile.read_text().splitlines():
            if line.startswith(f"{target}:"):
                return True
    except OSError:
        pass
    return False


def check_tools() -> None:
    missing = [t for t in ("git", "gh", "claude") if not shutil.which(t)]
    if missing:
        raise RuntimeError(
            f"Missing required tool(s): {', '.join(missing)}. "
            "Install them and ensure they are on your PATH."
        )


def check_docker() -> None:
    if not shutil.which("docker"):
        raise RuntimeError("docker not found on PATH. Install Docker Desktop and ensure it is running.")
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("Docker daemon is not running. Start Docker Desktop and try again.")


def ensure_stack_ready(local_path: Path) -> None:
    check_docker()
    result = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        cwd=local_path, capture_output=True, text=True,
    )
    running = set(result.stdout.strip().splitlines())
    if "api" not in running or "web" not in running or "postgres" not in running:
        print("$ docker compose up --build -d")
        result = subprocess.run(["docker", "compose", "up", "--build", "-d"], cwd=local_path)
        if result.returncode != 0:
            raise RuntimeError("docker compose up --build -d failed. Check Docker logs.")
    _wait_for_postgres(local_path)
    _wait_for_api()


def _wait_for_postgres(local_path: Path, timeout: int = 60) -> None:
    print("Waiting for postgres to be ready...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_isready"],
            cwd=local_path, capture_output=True,
        )
        if result.returncode == 0:
            print("Postgres is ready.")
            return
        time.sleep(2)
    raise RuntimeError(f"Postgres did not become ready within {timeout}s.")


def _wait_for_api(timeout: int = 120) -> None:
    import urllib.request
    print("Waiting for API to be ready...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://localhost:3001/health", timeout=2)
            print("API is ready.")
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"API did not become ready within {timeout}s.")


def _run(cmd: list[str], cwd: Path, stream: bool = False) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    if stream:
        return subprocess.run(cmd, cwd=cwd)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def is_dirty(local_path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=local_path, capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def has_changes(local_path: Path) -> bool:
    return is_dirty(local_path)


def get_changed_files(local_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=local_path, capture_output=True, text=True,
    )
    files = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().lstrip('"').rstrip('"'))
    return files


def check_scope(local_path: Path, scope_paths: list[str]) -> list[str]:
    """Returns list of changed files that violate scope_paths globs."""
    import pathspec
    changed = get_changed_files(local_path)
    if not changed:
        return []
    spec = pathspec.PathSpec.from_lines("gitignore", scope_paths)
    return [f for f in changed if not spec.match_file(f)]


def sync_repo(local_path: Path, github: str, default_branch: str) -> None:
    if not local_path.exists():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["git", "clone", f"https://github.com/{github}.git", str(local_path)],
            cwd=local_path.parent, stream=True,
        )
    else:
        result = _run(["git", "fetch", "origin"], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError("git fetch failed")
        result = _run(["git", "checkout", default_branch], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError(f"git checkout {default_branch} failed")
        result = _run(["git", "pull", "--ff-only"], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError("git pull --ff-only failed — branch may have diverged")


def create_branch(local_path: Path, branch: str) -> None:
    result = _run(["git", "checkout", "-b", branch], cwd=local_path, stream=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create branch '{branch}'")


def delete_branch(local_path: Path, branch: str, default_branch: str) -> None:
    subprocess.run(["git", "checkout", default_branch], cwd=local_path, capture_output=True)
    subprocess.run(["git", "branch", "-D", branch], cwd=local_path, capture_output=True)


def undo_commit(local_path: Path) -> None:
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=local_path, capture_output=True)


def run_agent(
    local_path: Path,
    prompt: str,
    capture_cost: bool = False,
    budget_minutes: float | None = None,
) -> AgentResult:
    # Token cap: claude CLI does not expose a --max-tokens flag for total context budget.
    # Token enforcement is therefore deferred to Phase 4+ tooling; only wall-clock time
    # is enforced here. Token usage is captured for reporting only (via --output-format json).
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    timeout_s = budget_minutes * 60 if budget_minutes else None

    if capture_cost:
        print("$ claude -p <prompt> --dangerously-skip-permissions --output-format json")
        cmd += ["--output-format", "json"]
        proc = subprocess.Popen(cmd, cwd=local_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        print("$ claude -p <prompt> --dangerously-skip-permissions")
        proc = subprocess.Popen(cmd, cwd=local_path)

    timed_out = False
    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return AgentResult(exit_code=-1, timed_out=True)

    if not capture_cost:
        return AgentResult(exit_code=proc.returncode)

    cost_usd = None
    duration_ms = None
    output = None
    tokens_used = None
    if stdout:
        try:
            data = json.loads(stdout.strip())
            cost_usd = data.get("total_cost_usd")
            duration_ms = data.get("duration_ms")
            output = data.get("result")
            usage = data.get("usage", {})
            tokens_used = (
                usage.get("input_tokens", 0) +
                usage.get("output_tokens", 0) +
                usage.get("cache_read_input_tokens", 0)
            )
            if output:
                print(output)
        except json.JSONDecodeError:
            output = stdout
            print(output)

    return AgentResult(
        exit_code=proc.returncode,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        output=output,
        tokens_used=tokens_used,
    )


def run_shell_command(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=cwd)  # noqa: S602


def commit(local_path: Path, message: str) -> None:
    result = _run(["git", "add", "-A"], cwd=local_path)
    if result.returncode != 0:
        raise RuntimeError("git add failed")
    result = _run(["git", "commit", "-m", message], cwd=local_path, stream=True)
    if result.returncode != 0:
        raise RuntimeError("git commit failed")


def push(local_path: Path, branch: str) -> None:
    result = _run(["git", "push", "-u", "origin", branch], cwd=local_path, stream=True)
    if result.returncode != 0:
        raise RuntimeError(f"git push failed for branch '{branch}'")


def create_pr(local_path: Path, title: str, body: str, base: str, head: str) -> str:
    print(f"$ gh pr create --title {title!r} --base {base} --head {head}")
    result = subprocess.run(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", base, "--head", head],
        cwd=local_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr create failed:\n{result.stderr}\n"
            f"Branch '{head}' preserved. Re-run gh pr create manually."
        )
    return result.stdout.strip()


def secret_scan(local_path: Path) -> list[str]:
    """Run gitleaks on the latest commit. Returns list of rule names that fired.
    Returns empty list if gitleaks is not installed (soft failure)."""
    if not shutil.which("gitleaks"):
        print("Warning: gitleaks not on PATH — secret scan skipped. Install gitleaks for full hardening.")
        return []

    result = subprocess.run(
        ["gitleaks", "detect", "--source", ".", "--log-opts", "HEAD~1..HEAD",
         "--report-format", "json", "--report-path", "/tmp/gitleaks-report.json",
         "--no-banner"],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return []

    try:
        import json as _json
        report = _json.loads(Path("/tmp/gitleaks-report.json").read_text())
        return list({f.get("RuleID", "unknown") for f in (report or [])})
    except Exception:
        return ["unknown"]


def cleanup_stale_branches(local_path: Path, github: str, stale_days: int = 7) -> list[str]:
    """Delete remote factory/* branches older than stale_days with no open PRs."""
    result = subprocess.run(
        ["git", "ls-remote", "origin", "refs/heads/factory/*"],
        cwd=local_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []

    deleted = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        sha, ref = line.split("\t", 1)
        branch = ref.removeprefix("refs/heads/")

        # Check commit age
        age_result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", sha],
            cwd=local_path, capture_output=True, text=True,
        )
        if age_result.returncode != 0 or not age_result.stdout.strip():
            continue
        age_s = time.time() - int(age_result.stdout.strip())
        if age_s < stale_days * 86400:
            continue

        # Check for open PRs
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd=local_path, capture_output=True, text=True,
        )
        if pr_result.returncode == 0:
            prs = json.loads(pr_result.stdout or "[]")
            if prs:
                continue

        # Delete
        del_result = subprocess.run(
            ["git", "push", "origin", "--delete", branch],
            cwd=local_path, capture_output=True, text=True,
        )
        if del_result.returncode == 0:
            print(f"  Deleted stale branch: {branch}")
            deleted.append(branch)

    # Prune local refs
    subprocess.run(["git", "fetch", "--prune"], cwd=local_path, capture_output=True)
    return deleted
