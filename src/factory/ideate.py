# Model invocation strategy (spike 2026-04-28):
# Two options considered:
#   1. Anthropic SDK (anthropic Python package) — reliable structured output via tool use,
#      but requires ANTHROPIC_API_KEY in .env, which the user has not set up.
#   2. claude -p --output-format json — zero additional credentials (reuses existing
#      Claude Code auth), returns {"result": "<text>"} where text is the model's response.
#
# Choice: claude -p --output-format json.
# Reasoning: the factory is a personal local tool with a single user already authenticated
# via Claude Code. Requiring a separate ANTHROPIC_API_KEY is pure friction with no benefit.
# The result field reliably contains the model output; JSON is extracted by stripping
# markdown code fences and parsing. A retry is performed on malformed JSON.

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .linear import LinearClient, LinearError
from .manifest import load_manifest

MODEL = "claude-sonnet-4-6"

PROMPT_TEMPLATE = """\
You are a technical project manager helping a developer turn a brain dump into a structured Linear ticket.

## Repo context
{repo_metadata}

## Repo coding conventions (CLAUDE.md)
{repo_claude_md}

## Linear ticket format reference
{linear_schema}

## Brain dump
{brain_dump}

---

Produce a single JSON object (no markdown fences, no other text) with exactly these keys:

{{
  "title": "<short imperative title, max 70 chars>",
  "description_markdown": "<full markdown description with sections: ## Acceptance Criteria (required, bulleted, testable), ## Scope Paths (optional, one glob per line), ## Budget (optional, tokens: N / minutes: N), ## Notes (optional)>",
  "scope_paths": ["<glob>", ...],
  "budget_tokens": <integer, default 10000 for small tasks>,
  "budget_minutes": <integer, default 5 for small tasks>,
  "rationale": "<one paragraph explaining scope and budget choices>"
}}

Rules:
- Acceptance criteria must be testable and specific.
- scope_paths should be the smallest plausible set of files.
- budget_tokens / budget_minutes should reflect task complexity (small: 10000/5, medium: 30000/15, large: 50000/30).
- Output ONLY the JSON object. No prose before or after.

## One-shot example output
{{"title": "Add CHANGELOG entry for v1.0 release", "description_markdown": "## Acceptance Criteria\\n\\n- A new entry is added to `CHANGELOG.md` under an \\"Unreleased\\" section.\\n- The entry reads \\"Initial v1.0 release\\".\\n- No other files are modified.", "scope_paths": ["CHANGELOG.md"], "budget_tokens": 10000, "budget_minutes": 5, "rationale": "Single-file change with a clear, verifiable criterion. Minimal scope and budget."}}
"""


@dataclass
class IdeateResult:
    title: str
    description_markdown: str
    scope_paths: list[str] = field(default_factory=list)
    budget_tokens: int = 10000
    budget_minutes: int = 5
    rationale: str = ""


def ideate(
    brain_dump: str,
    repo_key: str | None,
    manifest_path: Path | None = None,
    yes: bool = False,
    api_key: str | None = None,
) -> None:
    brain_dump = brain_dump.strip()
    if not brain_dump:
        raise ValueError("Brain dump is empty. Provide a file or pipe text via stdin.")

    manifest = load_manifest(manifest_path)

    # Resolve target repo
    if repo_key is None:
        matches = [k for k in manifest.repos if k.lower() in brain_dump.lower()]
        if len(matches) == 1:
            repo_key = matches[0]
        elif len(matches) > 1:
            raise ValueError(
                f"Brain dump mentions multiple repos: {matches}. "
                "Pass --repo explicitly to disambiguate."
            )
        else:
            raise ValueError(
                "Cannot infer target repo from brain dump. Pass --repo <repo-key> explicitly."
            )

    if repo_key not in manifest.repos:
        available = ", ".join(manifest.repos.keys())
        raise ValueError(f"Repo '{repo_key}' not found in manifest. Available: {available}")

    repo = manifest.repos[repo_key]

    # Load context
    repo_metadata = (
        f"Repo: {repo.github}\n"
        f"Default branch: {repo.default_branch}\n"
        f"Test command: {repo.test_command or 'auto-detected'}\n"
        f"Linear team: {repo.linear_team or repo_key.upper()}"
    )

    repo_claude_md = ""
    claude_md_path = repo.local_path / "CLAUDE.md"
    if claude_md_path.exists():
        repo_claude_md = claude_md_path.read_text()[:3000]  # cap to avoid context bloat
    else:
        repo_claude_md = "(no CLAUDE.md found)"

    linear_schema_path = (manifest_path or Path("manifest.yaml")).resolve().parent / "docs" / "LINEAR_SCHEMA.md"
    linear_schema = linear_schema_path.read_text() if linear_schema_path.exists() else ""

    # Call model
    print(f"Calling {MODEL} to draft ticket...", flush=True)
    result = _call_model(brain_dump, repo_metadata, repo_claude_md, linear_schema)

    # Confirm
    if not yes:
        result = _confirm(result)
        if result is None:
            print("Aborted.")
            return

    # Build description with embedded metadata sections
    description = _build_description(result, repo_key)

    # Create Linear issue
    if api_key is None:
        raise ValueError(
            "LINEAR_API_KEY is not set. Add it to .env."
        )
    client = LinearClient(api_key)
    team_key = repo.linear_team or repo_key.upper()

    team_id = client.get_team_id(team_key)
    if not team_id:
        raise ValueError(f"Team '{team_key}' not found in Linear.")

    # Use Backlog state (never Ready for Agent)
    state_id = client.get_state_id(team_key, "Backlog")
    if not state_id:
        # Fall back to any unstarted state
        state_id = client.get_state_id(team_key, "Todo")
    if not state_id:
        raise ValueError(f"Could not find Backlog or Todo state for team {team_key}.")

    issue = client.create_issue(
        team_id=team_id,
        title=result.title,
        description=description,
        state_id=state_id,
    )

    print(f"\nCreated: {issue['identifier']} — {issue['url']}")
    print("State: Backlog (mark 'Ready For AI' in Linear when you're happy with it)")


def _call_model(
    brain_dump: str,
    repo_metadata: str,
    repo_claude_md: str,
    linear_schema: str,
    retry: bool = True,
) -> IdeateResult:
    prompt = PROMPT_TEMPLATE.format(
        brain_dump=brain_dump,
        repo_metadata=repo_metadata,
        repo_claude_md=repo_claude_md,
        linear_schema=linear_schema,
    )

    proc = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "json"],
        capture_output=True,
        text=True,
    )

    raw_text = ""
    if proc.stdout:
        try:
            outer = json.loads(proc.stdout.strip())
            raw_text = outer.get("result", "")
        except json.JSONDecodeError:
            raw_text = proc.stdout

    try:
        return _parse_result(raw_text)
    except (ValueError, KeyError) as e:
        if retry:
            print(f"Model returned malformed JSON ({e}), retrying with re-prompt...")
            retry_prompt = (
                f"{prompt}\n\n"
                f"IMPORTANT: Your previous response was not valid JSON. "
                f"Output ONLY a JSON object, no markdown fences, no other text.\n"
                f"Previous (bad) response: {raw_text[:500]}"
            )
            proc2 = subprocess.run(
                ["claude", "-p", retry_prompt, "--dangerously-skip-permissions", "--output-format", "json"],
                capture_output=True,
                text=True,
            )
            raw_text2 = ""
            if proc2.stdout:
                try:
                    outer2 = json.loads(proc2.stdout.strip())
                    raw_text2 = outer2.get("result", "")
                except json.JSONDecodeError:
                    raw_text2 = proc2.stdout
            return _parse_result(raw_text2)
        raise


def _parse_result(text: str) -> IdeateResult:
    # Strip markdown code fences if present
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    data = json.loads(text)

    required = ("title", "description_markdown")
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

    return IdeateResult(
        title=str(data["title"]),
        description_markdown=str(data["description_markdown"]),
        scope_paths=list(data.get("scope_paths") or []),
        budget_tokens=int(data.get("budget_tokens", 10000)),
        budget_minutes=int(data.get("budget_minutes", 5)),
        rationale=str(data.get("rationale", "")),
    )


def _confirm(result: IdeateResult) -> IdeateResult | None:
    print("\n" + "─" * 60)
    print(f"Title:         {result.title}")
    print(f"Scope paths:   {', '.join(result.scope_paths) or '(none)'}")
    print(f"Budget:        {result.budget_tokens} tokens / {result.budget_minutes} min")
    print(f"\nAcceptance criteria (from description):")
    for line in result.description_markdown.splitlines():
        if line.strip().startswith("-"):
            print(f"  {line.strip()}")
    if result.rationale:
        print(f"\nRationale: {result.rationale}")
    print("─" * 60)

    answer = input("\nCreate this issue in Linear? [y/N/edit] ").strip().lower()

    if answer == "edit":
        result = _open_editor(result)
        if result is None:
            return None
        return _confirm(result)

    return result if answer == "y" else None


def _open_editor(result: IdeateResult) -> IdeateResult | None:
    data = {
        "title": result.title,
        "description_markdown": result.description_markdown,
        "scope_paths": result.scope_paths,
        "budget_tokens": result.budget_tokens,
        "budget_minutes": result.budget_minutes,
        "rationale": result.rationale,
    }
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(data, f, indent=2)
        tmp_path = f.name

    os.system(f"{editor} {tmp_path}")  # noqa: S605

    try:
        edited = json.loads(Path(tmp_path).read_text())
        return _parse_result(json.dumps(edited))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Invalid JSON after edit: {e}. Discarding changes.")
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _build_description(result: IdeateResult, repo_key: str) -> str:
    """Build the Linear issue description embedding all metadata as parseable sections."""
    # Start with the model-generated description (contains ## Acceptance Criteria etc.)
    desc = result.description_markdown.strip()

    # Append ## Target Repo and ## Scope Paths if not already in description
    if "## Target Repo" not in desc:
        desc += f"\n\n## Target Repo\n\n{repo_key}"

    if result.scope_paths and "## Scope Paths" not in desc:
        desc += "\n\n## Scope Paths\n\n" + "\n".join(result.scope_paths)

    if (result.budget_tokens != 10000 or result.budget_minutes != 5) and "## Budget" not in desc:
        desc += f"\n\n## Budget\n\ntokens: {result.budget_tokens}\nminutes: {result.budget_minutes}"

    return desc
