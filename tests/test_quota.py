"""Tests for quota tracking and provider fallback logic."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory.providers.base import AgentResult
from factory.providers.claude import QuotaInfo
from factory.quota_tracker import QuotaTracker
from factory.runner import _run_with_fallback

# ---------------------------------------------------------------------------
# QuotaTracker
# ---------------------------------------------------------------------------


def test_quota_tracker_new_provider_is_available(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    assert qt.is_available("claude")
    assert qt.is_available("codex")


def test_quota_tracker_mark_exhausted_makes_unavailable(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    qt.mark_exhausted("claude")
    assert not qt.is_available("claude")
    assert qt.is_available("codex")  # other providers unaffected


def test_quota_tracker_persists_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "quota_state.json"
    qt = QuotaTracker(path)
    qt.mark_exhausted("codex")

    qt2 = QuotaTracker(path)
    assert not qt2.is_available("codex")


def test_quota_tracker_reset_after_window(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json", reset_hours={"claude": 0.0001})
    qt.mark_exhausted("claude")
    time.sleep(0.5)  # 0.0001 h = 0.36 s — should have elapsed
    assert qt.is_available("claude")


def test_quota_tracker_format_reset_time_available(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    assert qt.format_reset_time("claude") == "available"


def test_quota_tracker_format_reset_time_exhausted(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    qt.mark_exhausted("claude")
    msg = qt.format_reset_time("claude")
    assert "resets in" in msg


def test_quota_tracker_corrupt_state_file_treated_as_available(tmp_path: Path) -> None:
    path = tmp_path / "quota_state.json"
    path.write_text("not json{{{")
    qt = QuotaTracker(path)
    assert qt.is_available("claude")


def test_quota_tracker_custom_reset_hours(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json", reset_hours={"claude": 10.0})
    qt.mark_exhausted("claude")
    remaining = qt.seconds_until_reset("claude")
    assert remaining is not None
    assert 9 * 3600 < remaining <= 10 * 3600


# ---------------------------------------------------------------------------
# _run_with_fallback — quota logic
# ---------------------------------------------------------------------------


def _make_quota_info(utilization: float) -> QuotaInfo:
    return QuotaInfo(
        utilization_5h=utilization,
        utilization_7d=0.0,
        utilization_overage=0.0,
        reset_5h=datetime.now(UTC) + timedelta(hours=3),
        status="allowed",
        raw_headers={},
    )


def _ok_agent(provider: str) -> AgentResult:
    return AgentResult(exit_code=0, provider=provider)


def _quota_hit_agent(provider: str) -> AgentResult:
    return AgentResult(exit_code=1, usage_limit_hit=True, provider=provider)


def test_fallback_uses_claude_when_below_threshold(tmp_path: Path) -> None:
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=_make_quota_info(0.50)),
        patch("factory.runner.claude_provider.run", return_value=_ok_agent("claude")) as mock_run,
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=None,
            max_utilization=0.90,
        )
    assert result.provider == "claude"
    mock_run.assert_called_once()


def test_fallback_skips_claude_and_uses_codex_above_threshold(tmp_path: Path) -> None:
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=_make_quota_info(0.95)),
        patch("factory.runner.claude_provider.run") as mock_claude,
        patch("factory.runner.codex_provider.run", return_value=_ok_agent("codex")) as mock_codex,
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=None,
            max_utilization=0.90,
        )
    assert result.provider == "codex"
    mock_claude.assert_not_called()
    mock_codex.assert_called_once()


def test_fallback_marks_claude_exhausted_in_tracker_when_above_threshold(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=_make_quota_info(0.95)),
        patch("factory.runner.codex_provider.run", return_value=_ok_agent("codex")),
    ):
        _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=qt,
            max_utilization=0.90,
        )
    assert not qt.is_available("claude")


def test_fallback_all_exhausted_returns_usage_limit_hit(tmp_path: Path) -> None:
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=_make_quota_info(0.99)),
        patch("factory.runner.codex_provider.run", return_value=_quota_hit_agent("codex")),
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=None,
            max_utilization=0.90,
        )
    assert result.usage_limit_hit is True
    assert result.provider == "exhausted"


def test_fallback_skips_tracker_exhausted_provider(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    qt.mark_exhausted("claude")

    with (
        patch("factory.runner.claude_provider.fetch_quota") as mock_probe,
        patch("factory.runner.codex_provider.run", return_value=_ok_agent("codex")),
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=qt,
            max_utilization=0.90,
        )
    # Probe should not have been called — tracker already knows claude is exhausted
    mock_probe.assert_not_called()
    assert result.provider == "codex"


def test_fallback_quota_probe_failure_proceeds_anyway(tmp_path: Path) -> None:
    """If fetch_quota returns None (keychain unavailable), the run still proceeds."""
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=None),
        patch("factory.runner.claude_provider.run", return_value=_ok_agent("claude")) as mock_run,
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude"],
            quota_tracker=None,
            max_utilization=0.90,
        )
    assert result.provider == "claude"
    mock_run.assert_called_once()


def test_fallback_codex_mid_run_quota_hit_marks_exhausted(tmp_path: Path) -> None:
    qt = QuotaTracker(tmp_path / "quota_state.json")
    with (
        patch("factory.runner.claude_provider.fetch_quota", return_value=_make_quota_info(0.99)),
        patch("factory.runner.codex_provider.run", return_value=_quota_hit_agent("codex")),
    ):
        result = _run_with_fallback(
            local_path=tmp_path,
            prompt="test",
            capture_cost=False,
            budget_minutes=None,
            providers=["claude", "codex"],
            quota_tracker=qt,
            max_utilization=0.90,
        )
    assert not qt.is_available("codex")
    assert result.usage_limit_hit is True
