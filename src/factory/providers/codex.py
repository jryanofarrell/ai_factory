from __future__ import annotations

import json
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .base import AgentResult

# Free-credit quota keywords from codex exec --json JSONL event stream.
# OpenAI surfaces exhausted free credits as a 429 with insufficient_quota or
# rate_limit_exceeded, or as a billing error (402 / payment_required).
_QUOTA_STATUSES = {429, 402}
_QUOTA_CODES = {"insufficient_quota", "rate_limit_exceeded", "billing_hard_limit_reached"}
_QUOTA_KEYWORDS = (
    "insufficient quota",
    "rate limit",
    "free quota",
    "billing",
    "exceeded",
    "quota",
)


def _load_ai_context(local_path: Path) -> str:
    """Read only hard rules for Codex.

    Broader context and skills are intentionally loaded on demand by the executor
    prompt so fallback runs do not spend quota on unrelated repo documentation.
    """
    rules_dir = local_path / ".ai" / "rules"
    if not rules_dir.exists():
        return ""

    sections: list[str] = []
    for md_file in sorted(rules_dir.rglob("*.md")):
        try:
            content = md_file.read_text().strip()
            if content:
                sections.append(f"--- {md_file.relative_to(local_path)} ---\n{content}")
        except OSError:
            pass

    return "\n\n".join(sections)


def run(
    local_path: Path,
    prompt: str,
    budget_minutes: float | None = None,
    model: str | None = None,
) -> AgentResult:
    ai_context = _load_ai_context(local_path)
    full_prompt = f"{ai_context}\n\n---\n\n{prompt}" if ai_context else prompt

    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "-C",
        str(local_path),
    ]
    if model:
        cmd += ["-m", model]
    cmd.append("--")
    cmd.append(full_prompt)

    timeout_s = budget_minutes * 60 if budget_minutes else None

    print(
        "$ codex exec --dangerously-bypass-approvals-and-sandbox --json -C <repo> <prompt>"
        + (f" (timeout {int(timeout_s)}s)" if timeout_s else "")
    )

    proc = subprocess.Popen(
        cmd,
        cwd=local_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    output_lines: list[str] = []
    usage_limit_hit = False
    last_message: str | None = None
    stderr_lines: list[str] = []

    def on_stdout(raw_line: str) -> None:
        nonlocal last_message, usage_limit_hit
        raw_line = raw_line.strip()
        if not raw_line:
            return
        try:
            event = json.loads(raw_line)
            usage_limit_hit = usage_limit_hit or _is_quota_event(event)
            progress = _extract_progress(event)
            if progress:
                print(progress, flush=True)
                output_lines.append(progress)
            message = _extract_message(event)
            if message:
                last_message = message
        except json.JSONDecodeError:
            print(raw_line, flush=True)
            output_lines.append(raw_line)

    timed_out = _stream_process(proc, timeout_s, on_stdout, stderr_lines)
    if timed_out:
        return AgentResult(exit_code=-1, timed_out=True, provider="codex")

    # Also check stderr for quota signals if stdout was empty/unparseable.
    if not usage_limit_hit and stderr_lines:
        err_lower = "\n".join(stderr_lines).lower()
        usage_limit_hit = any(kw in err_lower for kw in _QUOTA_KEYWORDS)

    if proc.returncode != 0 and not output_lines and stderr_lines:
        print("\n".join(stderr_lines).strip())

    return AgentResult(
        exit_code=proc.returncode,
        output=last_message,
        usage_limit_hit=usage_limit_hit,
        provider="codex",
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


def _is_quota_event(event: dict) -> bool:
    event_type = event.get("type", "")
    if event_type not in ("error", "api_error", "message"):
        return False

    status = event.get("status") or event.get("http_status") or event.get("code")
    if isinstance(status, int) and status in _QUOTA_STATUSES:
        return True
    if isinstance(status, str) and status in _QUOTA_CODES:
        return True

    # Walk all string values in the event looking for quota keywords
    text = json.dumps(event).lower()
    return any(kw in text for kw in _QUOTA_KEYWORDS)


def _extract_message(event: dict) -> str | None:
    # assistant/agent text content
    if event.get("role") == "assistant" or event.get("type") in ("message", "agent_message"):
        content = event.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return " ".join(p for p in parts if p) or None
    return None


def _extract_progress(event: dict) -> str | None:
    event_type = event.get("type")
    if event_type in {"thread.started", "turn.started"}:
        return f"  [codex] {event_type}"
    if event_type == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            text = item.get("text")
            return str(text) if text else None
        item_type = item.get("type")
        return f"  [codex] completed {item_type}" if item_type else None
    return _extract_message(event)
