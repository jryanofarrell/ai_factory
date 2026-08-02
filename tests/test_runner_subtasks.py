"""Subtask execution flow tests.

We mock ``_run_with_fallback`` and assert the subtask loop:
- runs once per subtask
- stops on the first failing subtask
- propagates the local→opencode tier hint into provider ordering
- falls back to the single-shot path when the ticket has no subtasks
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from factory.manifest import RepoConfig
from factory.providers.base import AgentResult
from factory.runner import run_ticket
from factory.ticket import Subtask, Ticket


def _ticket_with_subtasks(*subtasks: Subtask) -> Ticket:
    return Ticket(
        id="THM-99",
        title="Subtask test",
        target_repo="thms-platform",
        acceptance_criteria="- works",
        subtasks=list(subtasks),
    )


def _repo(tmp_path: Path) -> RepoConfig:
    return RepoConfig(github="owner/repo", local_path=tmp_path, default_branch="main")


_PATCH_TARGETS = [
    ("factory.runner.check_tools", {}),
    ("factory.runner.is_dirty", {"return_value": False}),
    ("factory.runner.sync_repo", {}),
    ("factory.runner.create_branch", {}),
    ("factory.runner.has_changes", {"return_value": False}),
    ("factory.runner.delete_branch", {}),
    ("factory.runner._preserve_and_return_to_default", {}),
]


def _enter_common_patches(stack: ExitStack) -> None:
    for target, kw in _PATCH_TARGETS:
        stack.enter_context(patch(target, **kw))


def test_subtask_loop_invokes_fallback_once_per_subtask(tmp_path: Path) -> None:
    ticket = _ticket_with_subtasks(
        Subtask(id="1", title="a", changes="x", files=["a.md"]),
        Subtask(id="2", title="b", changes="y", files=["b.md"]),
        Subtask(id="3", title="c", changes="z", files=["c.md"]),
    )

    with ExitStack() as stack:
        _enter_common_patches(stack)
        rwf = stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                return_value=AgentResult(exit_code=0, provider="claude", tokens_used=10),
            )
        )
        run_ticket(ticket, _repo(tmp_path))

    assert rwf.call_count == 3


def test_subtask_loop_stops_on_first_failure(tmp_path: Path) -> None:
    ticket = _ticket_with_subtasks(
        Subtask(id="1", title="a", changes="x", files=["a.md"]),
        Subtask(id="2", title="b", changes="y", files=["b.md"]),
        Subtask(id="3", title="c", changes="z", files=["c.md"]),
    )

    side_effects = [
        AgentResult(exit_code=0, provider="claude"),
        AgentResult(exit_code=2, provider="claude"),
        AgentResult(exit_code=0, provider="claude"),
    ]
    with ExitStack() as stack:
        _enter_common_patches(stack)
        rwf = stack.enter_context(
            patch("factory.runner._run_with_fallback", side_effect=side_effects)
        )
        result = run_ticket(ticket, _repo(tmp_path))

    assert rwf.call_count == 2
    assert result.exit_code == 2
    assert result.reason == "unknown"


def test_tier_hint_local_reorders_providers(tmp_path: Path) -> None:
    ticket = _ticket_with_subtasks(
        Subtask(id="1", title="a", changes="x", files=["a.md"], tier_hint="local"),
    )

    captured: list[list[str]] = []

    def capture(*_args, **kwargs):
        captured.append(list(kwargs["providers"]))
        return AgentResult(exit_code=0, provider="opencode")

    with ExitStack() as stack:
        _enter_common_patches(stack)
        stack.enter_context(patch("factory.runner._run_with_fallback", side_effect=capture))
        run_ticket(
            ticket,
            _repo(tmp_path),
            executor_providers=["claude", "codex", "opencode"],
        )

    assert captured == [["opencode", "claude", "codex"]]


def test_tier_hint_hosted_keeps_default_order(tmp_path: Path) -> None:
    ticket = _ticket_with_subtasks(
        Subtask(id="1", title="a", changes="x", files=["a.md"], tier_hint="hosted"),
    )

    captured: list[list[str]] = []

    def capture(*_args, **kwargs):
        captured.append(list(kwargs["providers"]))
        return AgentResult(exit_code=0, provider="claude")

    with ExitStack() as stack:
        _enter_common_patches(stack)
        stack.enter_context(patch("factory.runner._run_with_fallback", side_effect=capture))
        run_ticket(
            ticket,
            _repo(tmp_path),
            executor_providers=["claude", "codex", "opencode"],
        )

    assert captured == [["claude", "codex", "opencode"]]


def test_ticket_without_subtasks_uses_single_shot_path(tmp_path: Path) -> None:
    ticket = Ticket(
        id="THM-1",
        title="No subtasks",
        target_repo="thms-platform",
        acceptance_criteria="- done",
    )

    with ExitStack() as stack:
        _enter_common_patches(stack)
        rwf = stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                return_value=AgentResult(exit_code=0, provider="claude"),
            )
        )
        run_ticket(ticket, _repo(tmp_path))

    assert rwf.call_count == 1


def test_subtask_aggregates_tokens_and_cost(tmp_path: Path) -> None:
    ticket = _ticket_with_subtasks(
        Subtask(id="1", title="a", changes="x", files=["a.md"]),
        Subtask(id="2", title="b", changes="y", files=["b.md"]),
    )

    side_effects = [
        AgentResult(exit_code=0, provider="claude", tokens_used=100, cost_usd=0.10),
        AgentResult(exit_code=0, provider="claude", tokens_used=200, cost_usd=0.30),
    ]
    with ExitStack() as stack:
        _enter_common_patches(stack)
        stack.enter_context(patch("factory.runner._run_with_fallback", side_effect=side_effects))
        result = run_ticket(ticket, _repo(tmp_path))

    assert result.tokens_used == 300
    assert result.cost_usd == 0.40
