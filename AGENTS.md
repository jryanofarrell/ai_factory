# ai_factory

A control plane that turns Linear tickets into GitHub PRs, running entirely on the local machine.

## Read first
- `docs/ARCHITECTURE.md` — system overview and component boundaries
- `docs/DECISIONS.md` — architectural decisions and rationale (append-only)
- `docs/PHASES.md` — phase summary and current status

## Rules
Read `.ai/rules/factory.md` before writing any code. These always apply.

## Stack
- Python 3.12+, `uv` for environment management (`uv sync` to install)
- Typer CLI (`src/factory/cli.py`)
- `ruff` for lint/format — `uv run ruff check .` / `uv run ruff format .`
- `pytest` for tests — `uv run pytest`

## Target repositories
Projects the factory manages live under `repos/`. Before any work on a target project, read that project's `CLAUDE.md` and `AGENTS.md`.

## Skills
- `/ideate` — collaborative product discussion
- `/ticket` — draft and create Linear tickets
- `/run` — process the ticket queue end-to-end
