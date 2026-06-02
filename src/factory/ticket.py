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
    skills: list[str] = field(default_factory=list)
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
        if self.skills:
            parts.append("## Skills\n\n" + "\n".join(self.skills))

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

    skills_raw = _extract_section(body, "Skills") or ""
    skills = [line.strip() for line in skills_raw.splitlines() if line.strip()]

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
        skills=skills,
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


def _parse_scope_paths(description: str) -> list[str]:
    section = _extract_section(description, "Scope Paths")
    if not section:
        return []
    paths: list[str] = []
    for line in section.splitlines():
        line = line.strip().replace("\\*", "*").lstrip("- ")
        if line and not line.startswith("#"):
            paths.append(line)
    return paths


def find_scope_skill_mismatches(description: str, repo_root: Path) -> list[str]:
    """Return one warning line per per-file-type skill whose area is touched by
    the ticket's scope paths but whose own path is not listed in those scope paths.

    The check is intentionally simple: it walks `.ai/skills/<area>/<task>.md` files
    that already exist in `repo_root`, treats the first directory under `.ai/skills/`
    as the area, and flags a scope path whose first segment equals that area without
    also listing the skill file itself. Skips `.ai/skills/ai-structure.md` (not a
    per-file-type skill) and any skill files at the top level of `.ai/skills/`.
    """
    skills_root = repo_root / ".ai" / "skills"
    if not skills_root.is_dir():
        return []

    scope_paths = _parse_scope_paths(description)
    if not scope_paths:
        return []

    normalized_scope = [p.removeprefix("./").rstrip("/") for p in scope_paths]
    scope_first_segments = {p.split("/", 1)[0] for p in normalized_scope if p}

    warnings: list[str] = []
    for skill_path in sorted(skills_root.rglob("*.md")):
        rel = skill_path.relative_to(repo_root)
        parts = rel.parts
        # Expect .ai/skills/<area>/<task>.md — skip ai-structure.md and any
        # top-level skill files (no area dimension to check against scope).
        if len(parts) < 4:
            continue
        area = parts[2]
        rel_str = str(rel)

        if area in scope_first_segments and rel_str not in normalized_scope:
            warnings.append(f"scope touches {area}/ but {rel_str} is not in Scope Paths")

    return warnings
