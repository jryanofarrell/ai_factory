from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# Default hours to assume a provider's quota window lasts before retrying.
_DEFAULT_RESET_HOURS: dict[str, float] = {
    "claude": 5.0,   # Claude Pro 5-hour rolling window
    "codex": 4.0,    # OpenAI Plus free credits replenish roughly every 4 hours
}


class QuotaTracker:
    """Persists per-provider quota exhaustion timestamps to a JSON state file.

    Providers marked exhausted are skipped until their reset window has elapsed.
    The state file is created on first write; missing file means no recorded exhaustion.
    """

    def __init__(self, state_file: Path, reset_hours: dict[str, float] | None = None) -> None:
        self._path = state_file
        self._reset_hours = {**_DEFAULT_RESET_HOURS, **(reset_hours or {})}

    def mark_exhausted(self, provider: str) -> None:
        state = self._load()
        state[provider] = {"exhausted_at": datetime.now(UTC).isoformat()}
        self._save(state)

    def is_available(self, provider: str) -> bool:
        """Return True if the provider has no recorded exhaustion or the window has passed."""
        remaining = self.seconds_until_reset(provider)
        return remaining is None or remaining <= 0

    def seconds_until_reset(self, provider: str) -> float | None:
        """Return seconds until the provider's quota resets, or None if not exhausted."""
        state = self._load()
        record = state.get(provider)
        if not record:
            return None
        try:
            exhausted_at = datetime.fromisoformat(record["exhausted_at"])
        except (KeyError, ValueError):
            return None
        reset_hours = self._reset_hours.get(provider, 4.0)
        elapsed = (datetime.now(UTC) - exhausted_at).total_seconds()
        remaining = reset_hours * 3600 - elapsed
        return remaining if remaining > 0 else None

    def format_reset_time(self, provider: str) -> str:
        remaining = self.seconds_until_reset(provider)
        if remaining is None:
            return "available"
        h, rem = divmod(int(remaining), 3600)
        m = rem // 60
        if h:
            return f"resets in {h}h {m}m"
        return f"resets in {m}m"

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(state, indent=2))
