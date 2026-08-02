from __future__ import annotations

import json
import platform
import re
import shutil
import signal
import subprocess
import time
from datetime import UTC
from pathlib import Path

from .providers import AgentResult


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


def check_tools(providers: list[str] | None = None) -> None:
    _PROVIDER_BINS = {"claude": "claude", "codex": "codex", "opencode": "opencode"}
    _PROVIDER_INSTALL_HINTS = {
        "codex": "npm install -g @openai/codex",
        "opencode": "curl -fsSL https://opencode.ai/install | bash",
    }
    _active = set(providers or ["claude"])
    base = [t for t in ("git", "gh") if not shutil.which(t)]
    provider_missing = [
        f"{name} (install: {_PROVIDER_INSTALL_HINTS[name]})"
        if name in _PROVIDER_INSTALL_HINTS
        else name
        for name, binary in _PROVIDER_BINS.items()
        if name in _active and not shutil.which(binary)
    ]
    missing = base + provider_missing
    if missing:
        raise RuntimeError(
            f"Missing required tool(s): {', '.join(missing)}. "
            "Install them and ensure they are on your PATH."
        )


def check_docker(timeout: int = 120) -> None:
    if not shutil.which("docker"):
        raise RuntimeError(
            "docker not found on PATH. Install Docker Desktop and ensure it is running."
        )
    if _docker_info_ok():
        return

    if platform.system() != "Darwin":
        raise RuntimeError("Docker daemon is not running. Start Docker and try again.")

    print("Docker daemon is not running. Starting Docker Desktop...", flush=True)
    result = subprocess.run(["open", "-a", "Docker"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("Failed to start Docker Desktop. Start Docker manually and try again.")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _docker_info_ok():
            print("Docker daemon is ready.", flush=True)
            return
        time.sleep(2)

    raise RuntimeError(f"Docker daemon did not become ready within {timeout}s.")


def _docker_info_ok() -> bool:
    result = subprocess.run(["docker", "info"], capture_output=True)
    return result.returncode == 0


def ensure_stack_ready(local_path: Path) -> None:
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


def is_dirty(local_path: Path, paths: list[str] | None = None) -> bool:
    cmd = ["git", "status", "--porcelain"]
    if paths:
        cmd += ["--", *paths]
    result = subprocess.run(
        cmd,
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def has_changes(local_path: Path, paths: list[str] | None = None) -> bool:
    return is_dirty(local_path, paths)


def get_changed_files(local_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=local_path,
        capture_output=True,
        text=True,
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
    # Restore working tree so the next ticket doesn't fail the dirty check
    subprocess.run(["git", "restore", "."], cwd=local_path, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=local_path, capture_output=True)


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
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--model", "claude-sonnet-4-6"]
    timeout_s = budget_minutes * 60 if budget_minutes else None

    if capture_cost:
        print("$ claude -p <prompt> --dangerously-skip-permissions --output-format json")
        cmd += ["--output-format", "json"]
        proc = subprocess.Popen(
            cmd, cwd=local_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    else:
        print("$ claude -p <prompt> --dangerously-skip-permissions")
        proc = subprocess.Popen(cmd, cwd=local_path)

    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
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
    usage_limit_hit = False
    if stdout:
        try:
            data = json.loads(stdout.strip())
            cost_usd = data.get("total_cost_usd")
            duration_ms = data.get("duration_ms")
            output = data.get("result")
            usage = data.get("usage", {})
            tokens_used = (
                usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
            # Detect Pro usage limit: is_error=true with a 429/529 status or
            # error message containing usage/rate-limit keywords.
            if data.get("is_error"):
                api_status = data.get("api_error_status")
                error_text = (output or "").lower()
                usage_limit_hit = (
                    api_status in (429, 529, 402)
                    or "usage limit" in error_text
                    or "rate limit" in error_text
                    or "overloaded" in error_text
                    or "exceeded" in error_text
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
        usage_limit_hit=usage_limit_hit,
    )


def run_shell_command(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=cwd)  # noqa: S602


def commit(local_path: Path, message: str, paths: list[str] | None = None) -> None:
    add_cmd = ["git", "add", "-A"]
    if paths:
        add_cmd += ["--", *paths]
    result = _run(add_cmd, cwd=local_path)
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
            f"Branch '{head}' preserved. Re-run gh pr create manually."
        )
    return result.stdout.strip()


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    # Line-by-line parse avoids yaml errors when description contains colons or backticks.
    result = {}
    for line in parts[1].splitlines():
        if ": " in line:
            key, _, val = line.partition(": ")
            result[key.strip()] = val.strip()
    return result


def _natural_key(name: str) -> list:
    """Sort key that orders embedded numbers numerically (bil-4 before bil-10)."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name)]


def rebuild_memory_index(local_path: Path) -> None:
    """Rebuild .claude/memory/MEMORY.md from individual memory file frontmatter."""
    memory_dir = local_path / ".claude" / "memory"
    if not memory_dir.exists():
        return
    entries = []
    for md_file in sorted(memory_dir.glob("*.md"), key=lambda p: _natural_key(p.name)):
        if md_file.name == "MEMORY.md":
            continue
        fm = _parse_frontmatter(md_file.read_text())
        name = fm.get("name")
        description = fm.get("description")
        if name and description:
            entries.append(f"- [{name}]({md_file.name}) — {description}")
    if not entries:
        return
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n\n" + "\n".join(entries) + "\n")


def _extract_session_memory_files(local_path: Path, session_branches: list[str]) -> None:
    """Pull memory files written during this session from their factory branches into the
    working tree so rebuild_memory_index sees them even though the PRs aren't merged yet."""
    memory_dir = local_path / ".claude" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    for branch in session_branches:
        # List .claude/memory/*.md files on this branch
        ls = subprocess.run(
            ["git", "ls-tree", "--name-only", branch, ".claude/memory/"],
            cwd=local_path,
            capture_output=True,
            text=True,
        )
        if ls.returncode != 0:
            continue
        for path in ls.stdout.splitlines():
            filename = Path(path).name
            if not filename.endswith(".md") or filename == "MEMORY.md":
                continue
            dest = memory_dir / filename
            if dest.exists():
                continue  # already on main — don't overwrite
            content = subprocess.run(
                ["git", "show", f"{branch}:{path}"],
                cwd=local_path,
                capture_output=True,
                text=True,
            )
            if content.returncode == 0 and content.stdout:
                dest.write_text(content.stdout)


def _pr_url_for_branch(local_path: Path, github: str, branch: str) -> str | None:
    """Return the open PR url whose head is `branch`, or None."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            github,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--limit",
            "1",
        ],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return prs[0]["url"] if prs else None


def _fold_index_into_pr(
    local_path: Path, github: str, default_branch: str, branch: str
) -> str | None:
    """Single-PR run: rebuild the index on that PR's own branch so the catalog
    ships in the SAME PR rather than a separate one. Returns the PR url or None."""
    result = _run(["git", "fetch", "origin", branch], cwd=local_path, stream=True)
    if result.returncode != 0:
        raise RuntimeError(f"git fetch origin {branch} failed")
    result = _run(["git", "checkout", branch], cwd=local_path, stream=True)
    if result.returncode != 0:
        raise RuntimeError(f"git checkout {branch} failed")
    _run(["git", "pull", "--ff-only"], cwd=local_path, stream=True)

    rebuild_memory_index(local_path)
    pr_url = _pr_url_for_branch(local_path, github, branch)
    # Scope to .claude/memory/ (per ADR-024 / the memory-index PR scoping) so
    # stray working-tree files can't ride along in the folded commit.
    memory_scope = [".claude/memory"]
    if not has_changes(local_path, memory_scope):
        return pr_url
    commit(local_path, "chore: update memory index", memory_scope)
    push(local_path, branch)
    return pr_url


def _find_open_memory_pr(local_path: Path, github: str) -> tuple[str, str] | None:
    """Return (branch_name, pr_url) for an open factory/memory-* PR on this repo, or None."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            github,
            "--state",
            "open",
            "--json",
            "headRefName,url",
            "--limit",
            "50",
        ],
        cwd=local_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for pr in prs:
        if pr.get("headRefName", "").startswith("factory/memory-"):
            return (pr["headRefName"], pr["url"])
    return None


def create_memory_pr(
    local_path: Path,
    default_branch: str,
    github: str,
    session_branches: list[str] | None = None,
) -> str | None:
    """Rebuild MEMORY.md (the memory INDEX) and ensure one open index PR per repo.

    This function only handles the MEMORY.md pointer PR. Per-ticket memory files
    (`.claude/memory/<ticket-id>_<date>.md`) ship inside their own ticket PRs, not
    here — see .claude/commands/run.md Step 8.

    Design intent (.claude/commands/run.md Step 12): one memory INDEX PR per repo,
    not one per batch. If an open `factory/memory-*` PR is found we update it in
    place — check out its branch, merge the latest default branch (ticket PRs
    don't touch MEMORY.md so this is conflict-free in normal operation), rebuild
    the index, and push. If instead the run produced exactly ONE ticket/chain PR
    for the repo (single `session_branches` entry) and no index PR is open, the
    index is folded into that same PR (_fold_index_into_pr). Otherwise create a
    fresh branch + PR.

    session_branches: factory branches created this run whose per-ticket memory
    files haven't been merged to default yet. Their .claude/memory/ files are
    extracted into the working tree before rebuilding so the index reflects the
    full session.

    Returns PR URL or None if no changes were needed.
    """
    import uuid
    from datetime import datetime

    sync_repo(local_path, github, default_branch)

    existing = _find_open_memory_pr(local_path, github)

    # Conditional catalog (per user design): if this run produced exactly ONE
    # ticket/chain PR for the repo and there is no open index PR to reconcile,
    # fold the index update into that same PR instead of opening a separate one.
    sole_branch = session_branches[0] if session_branches and len(session_branches) == 1 else None
    if existing is None and sole_branch is not None:
        return _fold_index_into_pr(local_path, github, default_branch, sole_branch)

    if existing is not None:
        branch, pr_url = existing
        result = _run(["git", "fetch", "origin", branch], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError(f"git fetch origin {branch} failed")
        result = _run(["git", "checkout", branch], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError(f"git checkout {branch} failed")
        result = _run(["git", "pull", "--ff-only"], cwd=local_path, stream=True)
        if result.returncode != 0:
            raise RuntimeError(f"git pull --ff-only on {branch} failed")
        # Bring in ticket commits merged to default since this branch last updated.
        # Should be conflict-free because ticket PRs never touch MEMORY.md.
        result = _run(
            ["git", "merge", "--no-edit", f"origin/{default_branch}"],
            cwd=local_path,
            stream=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git merge origin/{default_branch} into {branch} failed — "
                f"resolve the conflict manually on the open memory PR."
            )
    else:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        branch = f"factory/memory-{date_str}-{uuid.uuid4().hex[:8]}"
        create_branch(local_path, branch)

    if session_branches:
        _extract_session_memory_files(local_path, session_branches)

    rebuild_memory_index(local_path)
    # Scope to .claude/memory/ so leftover working-tree junk (test __pycache__,
    # build output in repos without a .gitignore) can't ride along in the index PR.
    memory_scope = [".claude/memory"]
    if not has_changes(local_path, memory_scope):
        if existing is not None:
            return pr_url
        delete_branch(local_path, branch, default_branch)
        return None

    commit(local_path, "chore: rebuild memory index", memory_scope)
    push(local_path, branch)

    if existing is not None:
        return pr_url

    return create_pr(
        local_path,
        title="chore: update memory index",
        body=(
            "Rebuilds `.claude/memory/MEMORY.md` from individual memory files "
            "added during this run session.\n\n_Generated by ai\\_factory_"
        ),
        base=default_branch,
        head=branch,
    )


def _first_sentence(text: str, limit: int = 160) -> str:
    """Condense a summary to a one-line catalog description.

    Takes text up to the first sentence break (period or newline), collapsing
    internal whitespace, and hard-truncates over-long results.
    """
    flat = " ".join(text.split())
    match = re.search(r"^(.*?[.!?])(?:\s|$)", flat)
    sentence = match.group(1) if match else flat
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def write_ticket_memory(
    local_path: Path,
    ticket_id: str,
    title: str,
    summary: str,
    pr_url: str,
    files_changed: list[str],
    cost_usd: float | None,
    duration_s: float,
    date_str: str | None = None,
) -> Path:
    """Write the per-ticket memory file deterministically from the ticket's own summary.

    This is the single golden path for per-ticket memory — no executor prose is
    required, so it behaves identically regardless of which provider ran the
    ticket. It writes ONLY the per-ticket file; the MEMORY.md index is rebuilt
    separately (rebuild_memory_index / create_memory_pr) so ticket PRs never
    touch the shared index. Returns the path written.
    """
    from datetime import datetime

    memory_dir = local_path / ".claude" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    date_str = date_str or datetime.now(UTC).strftime("%Y-%m-%d")
    file_name = f"{ticket_id.lower()}_{date_str}.md"
    memory_path = memory_dir / file_name

    cost_str = f"${cost_usd:.2f}" if cost_usd is not None else "n/a"
    m, s = divmod(int(duration_s), 60)
    dur_str = f"{m}m {s}s" if m else f"{s}s"
    files_str = "\n".join(f"- {f}" for f in files_changed) if files_changed else "- (none recorded)"

    content = f"""\
---
name: {ticket_id}: {title}
description: {_first_sentence(summary)}
type: project
---

Factory ran ticket **{ticket_id}** on {date_str}.

**PR:** {pr_url}
**Duration:** {dur_str} · **Cost:** {cost_str}

**Files changed:**
{files_str}

## Summary

{summary.strip()}
"""
    memory_path.write_text(content)
    return memory_path


def secret_scan(local_path: Path) -> list[str]:
    """Run gitleaks on the latest commit. Returns list of rule names that fired.
    Returns empty list if gitleaks is not installed (soft failure)."""
    if not shutil.which("gitleaks"):
        print(
            "Warning: gitleaks not on PATH — secret scan skipped. "
            "Install gitleaks for full hardening."
        )
        return []

    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--source",
            ".",
            "--log-opts",
            "HEAD~1..HEAD",
            "--report-format",
            "json",
            "--report-path",
            "/tmp/gitleaks-report.json",
            "--no-banner",
        ],
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
        cwd=local_path,
        capture_output=True,
        text=True,
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
            cwd=local_path,
            capture_output=True,
            text=True,
        )
        if age_result.returncode != 0 or not age_result.stdout.strip():
            continue
        age_s = time.time() - int(age_result.stdout.strip())
        if age_s < stale_days * 86400:
            continue

        # Check for open PRs
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd=local_path,
            capture_output=True,
            text=True,
        )
        if pr_result.returncode == 0:
            prs = json.loads(pr_result.stdout or "[]")
            if prs:
                continue

        # Delete
        del_result = subprocess.run(
            ["git", "push", "origin", "--delete", branch],
            cwd=local_path,
            capture_output=True,
            text=True,
        )
        if del_result.returncode == 0:
            print(f"  Deleted stale branch: {branch}")
            deleted.append(branch)

    # Prune local refs
    subprocess.run(["git", "fetch", "--prune"], cwd=local_path, capture_output=True)
    return deleted
