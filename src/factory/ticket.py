from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Ticket:
    id: str
    title: str
    target_repo: str
    acceptance_criteria: str
    scope_paths: list[str] = field(default_factory=list)
    budget_tokens: int = 50_000
    budget_minutes: int = 30
    linear_url: str | None = None
    linear_id: str | None = None  # UUID used for Linear API write-back
    notes: str = ""
    raw_body: str = ""

    def to_markdown(self) -> str:
        fm: dict = {"id": self.id, "title": self.title, "target_repo": self.target_repo}
        if self.scope_paths:
            fm["scope_paths"] = self.scope_paths
        if self.budget_tokens != 50_000:
            fm["budget_tokens"] = self.budget_tokens
        if self.budget_minutes != 30:
            fm["budget_minutes"] = self.budget_minutes
        if self.linear_url:
            fm["linear_url"] = self.linear_url
        if self.linear_id:
            fm["linear_id"] = self.linear_id

        parts = [f"## Acceptance Criteria\n\n{self.acceptance_criteria}"]
        if self.notes:
            parts.append(f"## Notes\n\n{self.notes}")

        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
        return f"---\n{fm_str}---\n\n" + "\n\n".join(parts) + "\n"


def parse_ticket(path: Path) -> Ticket:
    text = path.read_text()

    if not text.startswith("---"):
        raise ValueError(f"{path}: ticket must start with YAML frontmatter (---)")

    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path}: could not find closing --- in frontmatter")

    _, fm_text, body = parts

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML frontmatter: {e}") from e

    for required in ("id", "title", "target_repo"):
        if not fm.get(required):
            raise ValueError(f"{path}: missing required frontmatter field '{required}'")

    body = body.strip()

    acceptance_criteria = _extract_section(body, "Acceptance Criteria")
    if acceptance_criteria is None:
        raise ValueError(f"{path}: missing required '## Acceptance Criteria' section")

    return Ticket(
        id=str(fm["id"]),
        title=str(fm["title"]),
        target_repo=str(fm["target_repo"]),
        acceptance_criteria=acceptance_criteria,
        scope_paths=[p.replace("\\*", "*") for p in (fm.get("scope_paths") or [])],
        budget_tokens=int(fm.get("budget_tokens", 50_000)),
        budget_minutes=int(fm.get("budget_minutes", 30)),
        linear_url=fm.get("linear_url"),
        linear_id=fm.get("linear_id"),
        notes=_extract_section(body, "Notes") or "",
        raw_body=body,
    )


def _extract_section(body: str, heading: str) -> str | None:
    lines = body.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return None

    section_lines: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section_lines.append(line)

    return "\n".join(section_lines).strip()
