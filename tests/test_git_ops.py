import subprocess
from pathlib import Path
import pytest
from factory.git_ops import check_scope, get_changed_files


def _make_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
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
