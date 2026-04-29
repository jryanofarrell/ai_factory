import json
import pytest
from unittest.mock import patch, MagicMock
from factory.ideate import _parse_result, _build_description, IdeateResult

VALID_JSON = {
    "title": "Add CHANGELOG entry",
    "description_markdown": "## Acceptance Criteria\n\n- Entry added under Unreleased.",
    "scope_paths": ["CHANGELOG.md"],
    "budget_tokens": 10000,
    "budget_minutes": 5,
    "rationale": "Single-file change.",
}


def test_parse_result_plain_json():
    result = _parse_result(json.dumps(VALID_JSON))
    assert result.title == "Add CHANGELOG entry"
    assert result.scope_paths == ["CHANGELOG.md"]
    assert result.budget_tokens == 10000
    assert result.budget_minutes == 5


def test_parse_result_strips_markdown_fences():
    fenced = f"```json\n{json.dumps(VALID_JSON)}\n```"
    result = _parse_result(fenced)
    assert result.title == "Add CHANGELOG entry"


def test_parse_result_missing_required_key_raises():
    bad = {**VALID_JSON}
    del bad["title"]
    with pytest.raises(ValueError, match="title"):
        _parse_result(json.dumps(bad))


def test_parse_result_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_result("not json at all")


def test_build_description_embeds_target_repo():
    result = IdeateResult(**VALID_JSON)
    desc = _build_description(result, "thms-platform")
    assert "## Target Repo" in desc
    assert "thms-platform" in desc


def test_build_description_embeds_scope_paths():
    result = IdeateResult(**VALID_JSON)
    desc = _build_description(result, "thms-platform")
    assert "## Scope Paths" in desc
    assert "CHANGELOG.md" in desc


def test_build_description_no_duplicate_sections():
    result = IdeateResult(
        title="t",
        description_markdown="## Acceptance Criteria\n\n- done\n\n## Scope Paths\n\nCHANGELOG.md\n\n## Target Repo\n\nthms-platform",
        scope_paths=["CHANGELOG.md"],
    )
    desc = _build_description(result, "thms-platform")
    assert desc.count("## Target Repo") == 1
    assert desc.count("## Scope Paths") == 1


def test_create_issue_refuses_ready_for_agent_state():
    """Regression: ideation must never create issues in the Ready for Agent state."""
    from factory.linear import LinearClient
    client = LinearClient("fake-key")
    # A state_id that happens to contain 'ready' should be blocked
    with pytest.raises(ValueError, match="Ready for Agent"):
        client.create_issue(
            team_id="team-123",
            title="test",
            description="## Acceptance Criteria\n\n- done",
            state_id="ready-for-agent-state-id",
        )


def test_ideate_errors_on_empty_brain_dump():
    from factory.ideate import ideate
    with pytest.raises(ValueError, match="empty"):
        ideate(brain_dump="   ", repo_key="thms-platform", yes=True, api_key="key")
