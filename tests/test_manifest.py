import pytest
from pathlib import Path
from factory.manifest import load_manifest, Manifest, RepoConfig

VALID_MANIFEST = """\
version: 1
queue_dir: .factory/queue
repos:
  thms-platform:
    github: toms-hms/thms-platform
    default_branch: main
    test_command: npm test
    build_command: npm run build
    linear_team: THMS
"""


def test_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    p.write_text(VALID_MANIFEST)
    m = load_manifest(p)
    assert m.version == 1
    assert m.queue_dir == ".factory/queue"
    assert "thms-platform" in m.repos
    repo = m.repos["thms-platform"]
    assert repo.github == "toms-hms/thms-platform"
    assert repo.default_branch == "main"
    assert repo.test_command == "npm test"
    assert repo.build_command == "npm run build"
    assert repo.linear_team == "THMS"


def test_default_local_path(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    p.write_text(VALID_MANIFEST)
    repo = load_manifest(p).repos["thms-platform"]
    assert repo.local_path == tmp_path / "repos" / "thms-platform"


def test_local_path_override(tmp_path: Path) -> None:
    content = """\
version: 1
repos:
  my-repo:
    github: owner/repo
    local_path: /custom/path
    default_branch: main
"""
    p = tmp_path / "manifest.yaml"
    p.write_text(content)
    assert load_manifest(p).repos["my-repo"].local_path == Path("/custom/path")


def test_relative_override_resolved_to_manifest_dir(tmp_path: Path) -> None:
    content = """\
version: 1
repos:
  my-repo:
    github: owner/repo
    local_path: elsewhere/my-repo
    default_branch: main
"""
    p = tmp_path / "manifest.yaml"
    p.write_text(content)
    assert load_manifest(p).repos["my-repo"].local_path == tmp_path / "elsewhere" / "my-repo"


def test_tilde_override_expanded(tmp_path: Path) -> None:
    content = """\
version: 1
repos:
  my-repo:
    github: owner/repo
    local_path: ~/some/path
    default_branch: main
"""
    p = tmp_path / "manifest.yaml"
    p.write_text(content)
    repo = load_manifest(p).repos["my-repo"]
    assert "~" not in str(repo.local_path)
    assert repo.local_path.is_absolute()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest.example.yaml"):
        load_manifest(tmp_path / "manifest.yaml")


def test_optional_fields_default(tmp_path: Path) -> None:
    minimal = """\
version: 1
repos:
  my-repo:
    github: owner/repo
    local_path: /tmp/repo
    default_branch: main
"""
    p = tmp_path / "manifest.yaml"
    p.write_text(minimal)
    repo = load_manifest(p).repos["my-repo"]
    assert repo.test_command == ""
    assert repo.build_command == ""
    assert repo.linear_team == ""


def test_default_queue_dir(tmp_path: Path) -> None:
    minimal = """\
version: 1
repos:
  my-repo:
    github: owner/repo
    local_path: /tmp/repo
    default_branch: main
"""
    p = tmp_path / "manifest.yaml"
    p.write_text(minimal)
    assert load_manifest(p).queue_dir == ".factory/queue"
