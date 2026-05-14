from __future__ import annotations

import json
import queue
import signal
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

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
        print("$ claude -p <prompt> --dangerously-skip-permissions --output-format stream-json")
        cmd += ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
        proc = subprocess.Popen(
            cmd, cwd=local_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    else:
        print("$ claude -p <prompt> --dangerously-skip-permissions")
        proc = subprocess.Popen(cmd, cwd=local_path)

    if not capture_cost:
        try:
            proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            return AgentResult(exit_code=-1, timed_out=True, provider="claude")
        return AgentResult(exit_code=proc.returncode, provider="claude")

    cost_usd = None
    duration_ms = None
    output = None
    tokens_used = None
    usage_limit_hit = False
    stderr_lines: list[str] = []

    def on_stdout(raw_line: str) -> None:
        nonlocal cost_usd, duration_ms, output, tokens_used, usage_limit_hit
        raw_line = raw_line.strip()
        if not raw_line:
            return
        try:
            data = json.loads(raw_line)
            cost_usd = data.get("total_cost_usd", cost_usd)
            duration_ms = data.get("duration_ms", duration_ms)
            output = data.get("result", output)
            usage = data.get("usage", {})
            if usage:
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
            text = _extract_stream_text(data)
            if text:
                line_end = "" if data.get("type") == "content_block_delta" else "\n"
                print(text, end=line_end, flush=True)
        except json.JSONDecodeError:
            output = raw_line
            print(raw_line, flush=True)

    timed_out = _stream_process(proc, timeout_s, on_stdout, stderr_lines)
    if timed_out:
        return AgentResult(exit_code=-1, timed_out=True, provider="claude")

    if proc.returncode != 0 and stderr_lines:
        print("\n".join(stderr_lines).strip())

    return AgentResult(
        exit_code=proc.returncode,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        output=output,
        tokens_used=tokens_used,
        usage_limit_hit=usage_limit_hit,
        provider="claude",
    )


def _stream_process(
    proc: subprocess.Popen[str],
    timeout_s: float | None,
    on_stdout: Callable[[str], None],
    stderr_lines: list[str],
) -> bool:
    q: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def read_stream(name: str, stream: TextIO | None) -> None:
        if stream is None:
            return
        for line in stream:
            q.put((name, line))
        q.put((name, None))

    threads = [
        threading.Thread(target=read_stream, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", proc.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_s if timeout_s else None
    open_streams = {"stdout", "stderr"}
    while open_streams:
        if deadline and time.monotonic() > deadline:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return True
        try:
            name, line = q.get(timeout=0.1)
        except queue.Empty:
            if proc.poll() is not None and q.empty():
                break
            continue
        if line is None:
            open_streams.discard(name)
        elif name == "stdout":
            on_stdout(line)
        else:
            stderr_lines.append(line.rstrip())

    proc.wait()
    return False


def _extract_stream_text(event: dict) -> str | None:
    if event.get("type") == "content_block_delta":
        delta = event.get("delta") or {}
        text = delta.get("text")
        return str(text) if text else None
    if event.get("type") == "assistant":
        message = event.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            return "".join(parts) or None
    return None
