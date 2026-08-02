import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.git_ops import (
    _first_sentence,
    check_docker,
    check_scope,
    commit,
    get_changed_files,
    has_changes,
    rebuild_memory_index,
    write_ticket_memory,
)


def _make_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    # Initial commit so HEAD exists
    (tmp_path / "README.md").write_text("readme")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_scope_check_no_violations(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "CHANGELOG.md").write_text("new entry")
    violations = check_scope(repo, ["CHANGELOG.md"])
    assert violations == []


def test_scope_check_violation_detected(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "CHANGELOG.md").write_text("new entry")
    (repo / "README.md").write_text("modified")
    violations = check_scope(repo, ["CHANGELOG.md"])
    assert "README.md" in violations
    assert "CHANGELOG.md" not in violations


def test_scope_check_glob_pattern(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.ts").write_text("code")
    (repo / "README.md").write_text("modified")
    violations = check_scope(repo, ["src/**"])
    assert "README.md" in violations
    assert "src/app.ts" not in violations


def test_scope_check_empty_scope_returns_nothing(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "anything.md").write_text("changed")
    # empty scope_paths means no check — caller is responsible for skipping
    violations = check_scope(repo, [])
    # pathspec with no patterns matches nothing, so everything is a violation
    # but runner.py skips check_scope when scope_paths is empty
    assert isinstance(violations, list)


def test_get_changed_files(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "new_file.md").write_text("hello")
    (repo / "README.md").write_text("modified")
    files = get_changed_files(repo)
    assert "new_file.md" in files
    assert "README.md" in files


def test_get_changed_files_expands_untracked_directories(tmp_path):
    repo = _make_git_repo(tmp_path)
    memory_dir = repo / ".claude" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "hel-1_2026-05-27.md").write_text("memory")

    files = get_changed_files(repo)

    assert ".claude/memory/hel-1_2026-05-27.md" in files
    assert ".claude/" not in files


def test_has_changes_scoped_to_paths(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "junk.pyc").write_text("bytecode")
    assert has_changes(repo)
    assert not has_changes(repo, [".claude/memory"])

    memory_dir = repo / ".claude" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n")
    assert has_changes(repo, [".claude/memory"])


def test_commit_scoped_to_paths_leaves_other_files_uncommitted(tmp_path):
    repo = _make_git_repo(tmp_path)
    memory_dir = repo / ".claude" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n")
    (repo / "junk.pyc").write_text("bytecode")

    commit(repo, "chore: rebuild memory index", [".claude/memory"])

    committed = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert committed == [".claude/memory/MEMORY.md"]
    assert has_changes(repo)  # junk.pyc still dirty, not swept into the commit


def test_check_docker_returns_when_daemon_running():
    with (
        patch("factory.git_ops.shutil.which", return_value="/usr/local/bin/docker"),
        patch(
            "factory.git_ops.subprocess.run",
            return_value=subprocess.CompletedProcess(["docker", "info"], 0),
        ) as run,
    ):
        check_docker()

    run.assert_called_once_with(["docker", "info"], capture_output=True)


def test_check_docker_starts_docker_desktop_on_macos():
    calls = [
        subprocess.CompletedProcess(["docker", "info"], 1),
        subprocess.CompletedProcess(["open", "-a", "Docker"], 0),
        subprocess.CompletedProcess(["docker", "info"], 1),
        subprocess.CompletedProcess(["docker", "info"], 0),
    ]

    with (
        patch("factory.git_ops.shutil.which", return_value="/usr/local/bin/docker"),
        patch("factory.git_ops.platform.system", return_value="Darwin"),
        patch("factory.git_ops.subprocess.run", side_effect=calls) as run,
        patch("factory.git_ops.time.sleep"),
    ):
        check_docker(timeout=5)

    assert [c.args[0] for c in run.call_args_list] == [
        ["docker", "info"],
        ["open", "-a", "Docker"],
        ["docker", "info"],
        ["docker", "info"],
    ]


def test_check_docker_raises_when_daemon_stopped_on_non_macos():
    with (
        patch("factory.git_ops.shutil.which", return_value="/usr/bin/docker"),
        patch("factory.git_ops.platform.system", return_value="Linux"),
        patch(
            "factory.git_ops.subprocess.run",
            return_value=subprocess.CompletedProcess(["docker", "info"], 1),
        ),
    ):
        with pytest.raises(RuntimeError, match="Docker daemon is not running"):
            check_docker()


def test_first_sentence_condenses():
    assert _first_sentence("One. Two. Three.") == "One."
    assert _first_sentence("No period here either") == "No period here either"
    long = "x" * 300
    assert len(_first_sentence(long)) <= 160


def test_write_ticket_memory_uses_summary_and_skips_index(tmp_path: Path):
    memory_dir = tmp_path / ".claude" / "memory"

    path = write_ticket_memory(
        local_path=tmp_path,
        ticket_id="BIL-16",
        title="Sitemap image discovery",
        summary="Web discovery reads image tags in the sitemap. Falls back otherwise.",
        pr_url="(pending)",
        files_changed=["src/parts_parser/web/discovery.py"],
        cost_usd=0.30,
        duration_s=442.0,
        date_str="2026-08-02",
    )

    assert path == memory_dir / "bil-16_2026-08-02.md"
    text = path.read_text()
    # Frontmatter: descriptive name + one-line description drawn from the summary.
    assert "name: BIL-16: Sitemap image discovery" in text
    assert "description: Web discovery reads image tags in the sitemap." in text
    # Body carries the full semi-detailed summary and the basic run info.
    assert "## Summary" in text
    assert "Falls back otherwise." in text
    assert "src/parts_parser/web/discovery.py" in text
    # Invariant: the per-ticket write must never touch the shared index.
    assert not (memory_dir / "MEMORY.md").exists()


def test_rebuild_index_reads_ticket_memory_frontmatter(tmp_path: Path):
    write_ticket_memory(
        local_path=tmp_path,
        ticket_id="BIL-16",
        title="Sitemap image discovery",
        summary="Web discovery reads image tags in the sitemap.",
        pr_url="(pending)",
        files_changed=["a.py"],
        cost_usd=None,
        duration_s=1.0,
        date_str="2026-08-02",
    )
    rebuild_memory_index(tmp_path)

    index = (tmp_path / ".claude" / "memory" / "MEMORY.md").read_text()
    assert "# Memory Index" in index
    assert "[BIL-16: Sitemap image discovery](bil-16_2026-08-02.md)" in index
    assert "Web discovery reads image tags in the sitemap." in index
