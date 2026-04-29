# Linear GraphQL client
#
# Spike findings (2026-04-27):
# - Linear's GraphQL API does NOT expose custom properties on Issue. The fields
#   `customFields`, `customProperties`, and `IssuePropertyValue` do not exist.
#   All ticket metadata must be parsed from structured Markdown sections in the
#   issue description. See ADR-010 and docs/LINEAR_SCHEMA.md for the section format.
# - Team keys are short uppercase identifiers (e.g. "THM"), not derived from repo names.
#   Verify in Linear → Settings → Teams before adding a repo to the manifest.
# - Triggering is via the "Ready For AI" label (not a workflow state) — Linear's
#   workflow state settings UI did not expose an "Add state" option in testing.
#   The label was created via the API (issueLabelCreate mutation, team THM).
# - Issue fields available: identifier, title, description, url, state.name,
#   team.key, labels.nodes.name. No custom property access.

from __future__ import annotations

import time
from typing import Any

import requests

GRAPHQL_URL = "https://api.linear.app/graphql"

ISSUES_QUERY = """
query($teamKey: String!, $labelName: String!, $stateName: String!) {
  issues(
    filter: {
      team: { key: { eq: $teamKey } }
      labels: { name: { eq: $labelName } }
      state: { name: { eq: $stateName } }
    }
    first: 100
  ) {
    nodes {
      id
      identifier
      title
      description
      url
      state { name }
      team { key }
      labels { nodes { id name } }
    }
  }
}
"""

COMMENT_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
"""

STATE_QUERY = """
query($teamKey: String!) {
  workflowStates(filter: { team: { key: { eq: $teamKey } } }) {
    nodes { id name }
  }
}
"""

ISSUE_UPDATE_MUTATION = """
mutation($id: String!, $stateId: String) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
  }
}
"""

ISSUE_BY_IDENTIFIER_QUERY = """
query($identifier: String!) {
  issue(id: $identifier) {
    id
    identifier
    title
    url
  }
}
"""

ISSUE_UPDATE_DESCRIPTION_MUTATION = """
mutation($id: String!, $title: String, $description: String) {
  issueUpdate(id: $id, input: { title: $title, description: $description }) {
    success
    issue { id identifier url }
  }
}
"""

LABEL_QUERY = """
query($teamKey: String!, $labelName: String!) {
  issueLabels(filter: { team: { key: { eq: $teamKey } }, name: { eq: $labelName } }) {
    nodes { id name }
  }
}
"""

ISSUE_ADD_LABEL_MUTATION = """
mutation($id: String!, $labelIds: [String!]!) {
  issueAddLabel(id: $id, labelId: $labelIds) {
    success
  }
}
"""

READY_LABEL = "Ready For AI"
READY_STATE = "Todo"


class LinearError(Exception):
    pass


class LinearClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": api_key,
        }

    def get_ready_issues(self, team_key: str) -> list[dict[str, Any]]:
        data = self._query(ISSUES_QUERY, {"teamKey": team_key, "labelName": READY_LABEL, "stateName": READY_STATE})
        return data["issues"]["nodes"]

    def comment_on_issue(self, issue_id: str, body: str) -> None:
        self._query(COMMENT_MUTATION, {"issueId": issue_id, "body": body})

    def get_state_id(self, team_key: str, state_name: str) -> str | None:
        data = self._query(STATE_QUERY, {"teamKey": team_key})
        for node in data["workflowStates"]["nodes"]:
            if node["name"] == state_name:
                return node["id"]
        return None

    def transition_issue(self, issue_id: str, state_id: str) -> None:
        self._query(ISSUE_UPDATE_MUTATION, {"id": issue_id, "stateId": state_id})

    def get_label_id(self, team_key: str, label_name: str) -> str | None:
        data = self._query(LABEL_QUERY, {"teamKey": team_key, "labelName": label_name})
        nodes = data.get("issueLabels", {}).get("nodes", [])
        return nodes[0]["id"] if nodes else None

    def get_team_id(self, team_key: str) -> str | None:
        data = self._query("""
        query($teamKey: String!) {
          teams(filter: { key: { eq: $teamKey } }) {
            nodes { id key }
          }
        }
        """, {"teamKey": team_key})
        nodes = data.get("teams", {}).get("nodes", [])
        return nodes[0]["id"] if nodes else None

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        state_id: str,
    ) -> dict[str, Any]:
        # Safety: refuse to create directly in "Ready for Agent" state (ADR-006 + Phase 5 rule)
        if state_id and "ready" in state_id.lower():
            raise ValueError("create_issue: state_id must not be the Ready for Agent state")
        data = self._query("""
        mutation($teamId: String!, $title: String!, $description: String!, $stateId: String!) {
          issueCreate(input: {
            teamId: $teamId
            title: $title
            description: $description
            stateId: $stateId
          }) {
            success
            issue { id identifier url }
          }
        }
        """, {"teamId": team_id, "title": title, "description": description, "stateId": state_id})
        return data["issueCreate"]["issue"]

    def get_issue_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        data = self._query(ISSUE_BY_IDENTIFIER_QUERY, {"identifier": identifier})
        return data.get("issue")

    def update_issue(
        self,
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        data = self._query(
            ISSUE_UPDATE_DESCRIPTION_MUTATION,
            {"id": issue_id, "title": title, "description": description},
        )
        return data["issueUpdate"]["issue"]

    def apply_label(self, issue_id: str, label_id: str) -> None:
        self._query(
            """
            mutation($id: String!, $labelId: String!) {
              issueAddLabel(id: $id, labelId: $labelId) { success }
            }
            """,
            {"id": issue_id, "labelId": label_id},
        )

    def _query(self, query: str, variables: dict | None = None, attempt: int = 0) -> dict[str, Any]:
        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers=self._headers,
                timeout=30,
            )
        except requests.RequestException as e:
            raise LinearError(f"Network error querying Linear: {e}") from e

        if resp.status_code == 401 or resp.status_code == 403:
            raise LinearError(
                f"Linear API returned {resp.status_code}. "
                "Check that LINEAR_API_KEY is valid (Linear → Settings → API → Personal API keys)."
            )

        if resp.status_code >= 500:
            if attempt < 3:
                wait = 2 ** attempt
                print(f"Linear API {resp.status_code} — retrying in {wait}s...")
                time.sleep(wait)
                return self._query(query, variables, attempt + 1)
            raise LinearError(f"Linear API returned {resp.status_code} after 3 retries.")

        resp.raise_for_status()

        body = resp.json()
        if "errors" in body:
            raise LinearError(f"Linear GraphQL error: {body['errors']}")

        return body["data"]
