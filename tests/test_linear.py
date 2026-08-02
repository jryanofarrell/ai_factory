from unittest.mock import patch

import pytest

from factory.linear import LinearClient


def _client() -> LinearClient:
    return LinearClient("fake-key")


def test_update_issue_title_only_omits_description():
    """A title-only update must never send description (which would clear the body)."""
    client = _client()
    with patch.object(client, "_query", return_value={"issueUpdate": {"issue": {"id": "1"}}}) as q:
        client.update_issue(issue_id="1", title="New title")
    variables = q.call_args.args[1]
    assert variables["input"] == {"title": "New title"}
    assert "description" not in variables["input"]


def test_update_issue_description_only_omits_title():
    client = _client()
    with patch.object(client, "_query", return_value={"issueUpdate": {"issue": {"id": "1"}}}) as q:
        client.update_issue(issue_id="1", description="body")
    variables = q.call_args.args[1]
    assert variables["input"] == {"description": "body"}
    assert "title" not in variables["input"]


def test_update_issue_requires_a_field():
    client = _client()
    with patch.object(client, "_query") as q:
        with pytest.raises(ValueError, match="title or description"):
            client.update_issue(issue_id="1")
    q.assert_not_called()
