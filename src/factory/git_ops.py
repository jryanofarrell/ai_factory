from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


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
    # Makefile with a test target takes priority — likely Docker-based
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
    """Ensure Docker stack is up, healthy, and migrated. Starts it if not running."""
    check_docker()

    result = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        cwd=local_path,
        capture_output=True,
        text=True,
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
    import time
    print("Waiting for postgres to be ready...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_isready"],
            cwd=local_path,
            capture_output=True,
        )
        if result.returncode == 0:
            print("Postgres is ready.")
            return
        time.sleep(2)
    raise RuntimeError(f"Postgres did not become ready within {timeout}s. Check: docker compose logs postgres")


def _wait_for_api(timeout: int = 120) -> None:
    import time
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
    raise RuntimeError(f"API did not become ready within {timeout}s. Check: docker compose logs api")


def _run(cmd: list[str], cwd: Path, stream: bool = False) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    if stream:
        return subprocess.run(cmd, cwd=cwd)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def is_dirty(local_path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def has_changes(local_path: Path) -> bool:
    return is_dirty(local_path)


def sync_repo(local_path: Path, github: str, default_branch: str) -> None:
    if not local_path.exists():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["git", "clone", f"https://github.com/{github}.git", str(local_path)],
            cwd=local_path.parent,
            stream=True,
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


def run_agent(local_path: Path, prompt: str) -> int:
    print("$ claude -p <prompt> --dangerously-skip-permissions")
    result = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions"],
        cwd=local_path,
    )
    return result.returncode


def run_shell_command(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    # shell=True needed for user-defined commands that may use shell syntax
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
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr create failed:\n{result.stderr}\n"
            f"Branch '{head}' and commit are preserved. Fix the issue and re-run gh pr create manually."
        )
    return result.stdout.strip()
