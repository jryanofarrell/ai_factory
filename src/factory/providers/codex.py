from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

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
    """Read .ai/rules/ and .ai/context/ from the repo and return combined text.

    This gives Codex the same project context Claude gets from reading CLAUDE.md
    and memory files automatically. Returns empty string if .ai/ doesn't exist.
    """
    ai_dir = local_path / ".ai"
    if not ai_dir.exists():
        return ""

    sections: list[str] = []

    for subdir in ("rules", "context"):
        folder = ai_dir / subdir
        if not folder.exists():
            continue
        for md_file in sorted(folder.rglob("*.md")):
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
        "-C", str(local_path),
    ]
    if model:
        cmd += ["-m", model]
    cmd.append(full_prompt)

    timeout_s = budget_minutes * 60 if budget_minutes else None

    print("$ codex exec --dangerously-bypass-approvals-and-sandbox --json -C <repo> <prompt>"
          + (f" (timeout {int(timeout_s)}s)" if timeout_s else ""))

    proc = subprocess.Popen(
        cmd,
        cwd=local_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return AgentResult(exit_code=-1, timed_out=True, provider="codex")

    output_lines: list[str] = []
    usage_limit_hit = False
    last_message: str | None = None

    for raw_line in (stdout or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
            usage_limit_hit = usage_limit_hit or _is_quota_event(event)
            msg = _extract_message(event)
            if msg:
                last_message = msg
                output_lines.append(msg)
        except json.JSONDecodeError:
            output_lines.append(raw_line)

    # Also check stderr for quota signals if stdout was empty/unparseable
    if not usage_limit_hit and stderr:
        err_lower = stderr.lower()
        usage_limit_hit = any(kw in err_lower for kw in _QUOTA_KEYWORDS)

    if output_lines:
        print("\n".join(output_lines[-20:]))  # tail the output

    return AgentResult(
        exit_code=proc.returncode,
        output=last_message,
        usage_limit_hit=usage_limit_hit,
        provider="codex",
    )


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
