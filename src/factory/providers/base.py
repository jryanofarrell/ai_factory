from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentResult:
    exit_code: int
    cost_usd: float | None = None
    duration_ms: int | None = None
    output: str | None = None
    timed_out: bool = False
    tokens_used: int | None = None
    usage_limit_hit: bool = False
    provider: str = "unknown"
