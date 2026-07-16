from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Subtask:
    """A single-file unit of work within a ticket. Executed sequentially.

    files: paths (relative to repo root) the subtask is allowed to touch — usually one.
    changes: free-form description of what should change in those files.
    recipe: path to the .ai/recipes/<area>/<task>.md file the subtask should follow.
    tier_hint: optional 'local' | 'hosted'; routes to that provider first, falls back normally.
    depends_on: list of subtask ids (e.g. ['1', '2']) that must precede this one in execution.
    """

    id: str
    title: str
    changes: str
    files: list[str] = field(default_factory=list)
    recipe: str | None = None
    tier_hint: str | None = None
    depends_on: list[str] = field(default_factory=list)


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
    recipes: list[str] = field(default_factory=list)
    subtasks: list[Subtask] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
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
        if self.recipes:
            parts.append("## Recipes\n\n" + "\n".join(self.recipes))
        if self.depends_on:
            parts.append("## Depends On\n\n" + "\n".join(self.depends_on))

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

    recipes_raw = _extract_section(body, "Recipes") or ""
    recipes = [line.strip() for line in recipes_raw.splitlines() if line.strip()]

    subtasks = _parse_subtasks(_extract_section(body, "Subtasks") or "")

    depends_on = _parse_depends_on(_extract_section(body, "Depends On") or "")

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
        recipes=recipes,
        subtasks=subtasks,
        depends_on=depends_on,
        raw_body=body,
    )


_SUBTASK_HEADER_RE = re.compile(r"^###\s+(?P<id>[^.\s]+)\.\s+(?P<title>.+?)\s*$")
_SUBTASK_FIELD_RE = re.compile(r"^[-*]\s*(?P<key>Files?|Recipe|Tier|Depends on)\s*:\s*(?P<val>.*)$",
                               re.IGNORECASE)


def _parse_depends_on(section: str) -> list[str]:
    """Parse the ticket-level ``## Depends On`` body.

    Format is one ticket ID per line (per /ticket skill description spec).
    Tolerant of comma-separated IDs on a single line, leading bullet markers,
    and the literal ``(none)`` / ``none`` sentinels which collapse to an empty list.
    Linear auto-linkifies bare issue IDs in saved descriptions
    (``BIL-5`` becomes ``[BIL-5](https://linear.app/...)``) — unwrap those
    links back to the ID before tokenizing. Returns deduplicated IDs in
    document order.
    """
    if not section.strip():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in section.splitlines():
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
        line = line.strip().lstrip("-*").strip()
        if not line:
            continue
        if line.lower() in ("(none)", "none"):
            continue
        for tok in re.split(r"[,\s]+", line):
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def _parse_subtasks(section: str) -> list[Subtask]:
    """Parse the ``## Subtasks`` body.

    Each subtask is a ``### N. Title`` block followed by bullet fields
    (Files, Recipe, Tier, Depends on) and a free-form changes paragraph.
    Returns [] if the section is empty.
    """
    if not section.strip():
        return []

    lines = section.splitlines()
    out: list[Subtask] = []
    current: dict | None = None
    changes_buf: list[str] = []

    def flush() -> None:
        if current is None:
            return
        current["changes"] = "\n".join(changes_buf).strip()
        out.append(
            Subtask(
                id=current["id"],
                title=current["title"],
                files=current.get("files", []),
                changes=current["changes"],
                recipe=current.get("recipe"),
                tier_hint=current.get("tier_hint"),
                depends_on=current.get("depends_on", []),
            )
        )

    for line in lines:
        header = _SUBTASK_HEADER_RE.match(line)
        if header:
            flush()
            current = {"id": header.group("id"), "title": header.group("title").strip()}
            changes_buf = []
            continue
        if current is None:
            continue
        field_match = _SUBTASK_FIELD_RE.match(line)
        if field_match:
            key = field_match.group("key").lower()
            val = field_match.group("val").strip()
            if key.startswith("file"):
                current["files"] = [p.strip() for p in re.split(r"[,\s]+", val) if p.strip()]
            elif key == "recipe":
                current["recipe"] = val or None
            elif key == "tier":
                current["tier_hint"] = val.lower() or None
            elif key.startswith("depends"):
                low = val.lower()
                if low in ("", "(none)", "none"):
                    current["depends_on"] = []
                else:
                    current["depends_on"] = [
                        p.strip() for p in re.split(r"[,\s]+", val) if p.strip()
                    ]
        else:
            changes_buf.append(line)

    flush()
    return out


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


def find_scope_recipe_mismatches(description: str, repo_root: Path) -> list[str]:
    """Return one warning line per per-file-type recipe whose area is touched by
    the ticket's scope paths but whose own path is not listed in those scope paths.

    The check is intentionally simple: it walks `.ai/recipes/<area>/<task>.md` files
    that already exist in `repo_root`, treats the first directory under `.ai/recipes/`
    as the area, and flags a scope path whose first segment equals that area without
    also listing the recipe file itself. Skips `.ai/recipes/ai-structure.md` (not a
    per-file-type recipe) and any recipe files at the top level of `.ai/recipes/`.
    """
    recipes_root = repo_root / ".ai" / "recipes"
    if not recipes_root.is_dir():
        return []

    scope_paths = _parse_scope_paths(description)
    if not scope_paths:
        return []

    normalized_scope = [p.removeprefix("./").rstrip("/") for p in scope_paths]
    scope_first_segments = {p.split("/", 1)[0] for p in normalized_scope if p}

    warnings: list[str] = []
    for recipe_path in sorted(recipes_root.rglob("*.md")):
        rel = recipe_path.relative_to(repo_root)
        parts = rel.parts
        # Expect .ai/recipes/<area>/<task>.md — skip ai-structure.md and any
        # top-level recipe files (no area dimension to check against scope).
        if len(parts) < 4:
            continue
        area = parts[2]
        rel_str = str(rel)

        if area in scope_first_segments and rel_str not in normalized_scope:
            warnings.append(f"scope touches {area}/ but {rel_str} is not in Scope Paths")

    return warnings
