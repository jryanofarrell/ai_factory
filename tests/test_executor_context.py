from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.providers.codex import _load_ai_context, run
from factory.runner import _build_prompt
from factory.ticket import Ticket


def test_codex_context_loads_rules_without_broad_context(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".ai" / "rules"
    context_dir = tmp_path / ".ai" / "context"
    rules_dir.mkdir(parents=True)
    context_dir.mkdir(parents=True)
    (rules_dir / "core.md").write_text("Always follow core rules.")
    (context_dir / "api.md").write_text("Large API context.")

    context = _load_ai_context(tmp_path)

    assert "Always follow core rules." in context
    assert "Large API context." not in context
    assert ".ai/rules/core.md" in context
    assert ".ai/context/api.md" not in context


def test_executor_prompt_uses_on_demand_repo_context(tmp_path: Path) -> None:
    ticket = Ticket(
        id="THMS-42",
        title="Add backend route",
        target_repo="thms-platform",
        acceptance_criteria="- Route exists.",
    )

    prompt = _build_prompt(ticket, tmp_path)

    assert "Read `AGENTS.md` and `CLAUDE.md` if present." in prompt
    assert "Read `.claude/memory/MEMORY.md` if present" in prompt
    assert "Backend/API work" in prompt
    assert "Frontend/web work" in prompt
    assert "List the contents of `.ai/skills/`" in prompt


def test_codex_prompt_is_separated_from_cli_options(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".ai" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "core.md").write_text("Always follow core rules.")

    proc = MagicMock()
    proc.returncode = 0

    with (
        patch("factory.providers.codex.subprocess.Popen", return_value=proc) as popen,
        patch("factory.providers.codex._stream_process", return_value=False),
    ):
        run(tmp_path, "Do the task.")

    cmd = popen.call_args.args[0]
    assert "--" in cmd
    assert cmd[cmd.index("--") + 1].startswith("--- .ai/rules/core.md ---")
