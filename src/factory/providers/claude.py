from __future__ import annotations

import json
import signal
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .base import AgentResult

# Token cap: claude CLI does not expose a --max-tokens flag for total context budget.
# Token enforcement is wall-clock time only; token usage is captured for reporting.
_MODEL = "claude-sonnet-4-6"

# Claude Pro 5-hour rolling window quota keywords detected from --output-format json payload.
_QUOTA_STATUSES = (429, 529, 402)
_QUOTA_KEYWORDS = ("usage limit", "rate limit", "overloaded", "exceeded", "quota")

# Cheapest available model for the quota probe (1 token, haiku pricing).
_PROBE_MODEL = "claude-haiku-4-5-20251001"
_PROBE_URL = "https://api.anthropic.com/v1/messages"


@dataclass
class QuotaInfo:
    utilization_5h: float  # 0.0–1.0  (matches "X% used" in claude.ai UI)
    utilization_7d: float  # 0.0–1.0
    utilization_overage: float  # 0.0–1.0  (paid-credit overage bucket)
    reset_5h: datetime | None  # when the 5-hour window resets
    status: str  # "allowed", "rate_limited", etc.
    raw_headers: dict[str, str]


def fetch_quota() -> QuotaInfo | None:
    """Make a 1-token probe call to api.anthropic.com and parse the rate-limit headers.

    Returns None if the token can't be read from the keychain or the call fails.
    The probe consumes a negligible amount of quota (one haiku completion).
    """
    token = _read_oauth_token()
    if not token:
        return None

    body = json.dumps(
        {
            "model": _PROBE_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()

    req = urllib.request.Request(
        _PROBE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        headers = dict(e.headers)
    except Exception:
        return None

    def hdr(name: str) -> str:
        return headers.get(name) or headers.get(name.lower()) or ""

    def float_hdr(name: str) -> float:
        try:
            return float(hdr(name))
        except (ValueError, TypeError):
            return 0.0

    reset_ts = hdr("anthropic-ratelimit-unified-5h-reset")
    reset_dt: datetime | None = None
    if reset_ts:
        try:
            reset_dt = datetime.fromtimestamp(int(reset_ts), tz=UTC)
        except (ValueError, OSError):
            pass

    return QuotaInfo(
        utilization_5h=float_hdr("anthropic-ratelimit-unified-5h-utilization"),
        utilization_7d=float_hdr("anthropic-ratelimit-unified-7d-utilization"),
        utilization_overage=float_hdr("anthropic-ratelimit-unified-overage-utilization"),
        reset_5h=reset_dt,
        status=(
            hdr("anthropic-ratelimit-unified-status")
            or hdr("anthropic-ratelimit-unified-5h-status")
        ),
        raw_headers={k: v for k, v in headers.items() if "ratelimit" in k.lower()},
    )


def _read_oauth_token() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout.strip())
        return data["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def run(
    local_path: Path,
    prompt: str,
    capture_cost: bool = False,
    budget_minutes: float | None = None,
) -> AgentResult:
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--model", _MODEL]
    timeout_s = budget_minutes * 60 if budget_minutes else None

    if capture_cost:
        print("$ claude -p <prompt> --dangerously-skip-permissions --output-format json")
        cmd += ["--output-format", "json"]
        proc = subprocess.Popen(
            cmd, cwd=local_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    else:
        print("$ claude -p <prompt> --dangerously-skip-permissions")
        proc = subprocess.Popen(cmd, cwd=local_path)

    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return AgentResult(exit_code=-1, timed_out=True, provider="claude")

    if not capture_cost:
        return AgentResult(exit_code=proc.returncode, provider="claude")

    cost_usd = None
    duration_ms = None
    output = None
    tokens_used = None
    usage_limit_hit = False

    if stdout:
        try:
            data = json.loads(stdout.strip())
            cost_usd = data.get("total_cost_usd")
            duration_ms = data.get("duration_ms")
            output = data.get("result")
            usage = data.get("usage", {})
            tokens_used = (
                usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
            if data.get("is_error"):
                api_status = data.get("api_error_status")
                error_text = (output or "").lower()
                usage_limit_hit = api_status in _QUOTA_STATUSES or any(
                    kw in error_text for kw in _QUOTA_KEYWORDS
                )
            if output:
                print(output)
        except json.JSONDecodeError:
            output = stdout
            print(output)

    return AgentResult(
        exit_code=proc.returncode,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        output=output,
        tokens_used=tokens_used,
        usage_limit_hit=usage_limit_hit,
        provider="claude",
    )
