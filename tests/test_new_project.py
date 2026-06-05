import pytest
import yaml

from factory import new_project
from factory.linear import LinearClient, LinearError
from factory.new_project import provision_project, register_repo_in_manifest

EXISTING_MANIFEST = """\
version: 1
queue_dir: .factory/queue
repos:
  thms-platform:
    github: toms-hms/thms-platform  # inline comment survives
    default_branch: main
    linear_team: THM
"""


def test_register_adds_block(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(EXISTING_MANIFEST)

    added = register_repo_in_manifest(m, "billy-ai", "jryanofarrell/billy-ai", "BIL")

    assert added is True
    data = yaml.safe_load(m.read_text())
    assert data["repos"]["billy-ai"] == {
        "github": "jryanofarrell/billy-ai",
        "default_branch": "main",
        "linear_team": "BIL",
    }
    # existing entry untouched
    assert data["repos"]["thms-platform"]["github"] == "toms-hms/thms-platform"


def test_register_preserves_comments(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(EXISTING_MANIFEST)

    register_repo_in_manifest(m, "billy-ai", "jryanofarrell/billy-ai", "BIL")

    assert "# inline comment survives" in m.read_text()


def test_register_is_idempotent(tmp_path):
    m = tmp_path / "manifest.yaml"
    original = (
        "version: 1\nrepos:\n  billy-ai:\n    github: jryanofarrell/billy-ai\n"
        "    default_branch: main\n    linear_team: BIL\n"
    )
    m.write_text(original)

    added = register_repo_in_manifest(m, "billy-ai", "jryanofarrell/billy-ai", "BIL")

    assert added is False
    assert m.read_text() == original  # file left byte-for-byte untouched


def test_register_creates_repos_section_when_absent(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text("version: 1\nqueue_dir: .factory/queue\n")

    added = register_repo_in_manifest(m, "billy-ai", "jryanofarrell/billy-ai", "BIL")

    assert added is True
    data = yaml.safe_load(m.read_text())
    assert data["repos"]["billy-ai"]["github"] == "jryanofarrell/billy-ai"


def test_create_team_returns_team(monkeypatch):
    client = LinearClient("key")
    monkeypatch.setattr(
        client,
        "_query",
        lambda q, v: {
            "teamCreate": {"success": True, "team": {"id": "t1", "key": "BIL", "name": "Billy AI"}}
        },
    )

    team = client.create_team("Billy AI", "BIL")

    assert team == {"id": "t1", "key": "BIL", "name": "Billy AI"}


def test_create_team_raises_on_failure(monkeypatch):
    client = LinearClient("key")
    monkeypatch.setattr(
        client, "_query", lambda q, v: {"teamCreate": {"success": False, "team": None}}
    )

    with pytest.raises(LinearError):
        client.create_team("Billy AI", "BIL")


def test_provision_dry_run_writes_nothing(tmp_path, monkeypatch):
    m = tmp_path / "manifest.yaml"
    m.write_text(EXISTING_MANIFEST)
    monkeypatch.setattr(new_project.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(new_project, "_gh_repo_exists", lambda r: False)

    result = provision_project(
        team_name="Billy AI",
        team_key="BIL",
        github="jryanofarrell/billy-ai",
        visibility="public",
        manifest_path=m,
        api_key=None,  # skip Linear
        dry_run=True,
    )

    assert result.repo_key == "billy-ai"
    assert result.repo_created is True  # would-create
    assert result.manifest_updated is True  # would-register
    assert m.read_text() == EXISTING_MANIFEST  # dry run touched nothing on disk


def test_provision_rejects_bad_visibility(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(EXISTING_MANIFEST)
    with pytest.raises(ValueError):
        provision_project(
            team_name="x",
            team_key="X",
            github="a/b",
            visibility="secret",
            manifest_path=m,
            dry_run=True,
        )


def test_provision_rejects_github_without_slash(tmp_path):
    m = tmp_path / "manifest.yaml"
    m.write_text(EXISTING_MANIFEST)
    with pytest.raises(ValueError):
        provision_project(
            team_name="x", team_key="X", github="noslash", manifest_path=m, dry_run=True
        )
