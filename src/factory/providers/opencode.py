from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .base import AgentResult

# OpenCode + Ollama: local fallback after claude/codex are exhausted.
# Configured via opencode.json (provider→model registry) and OPENCODE_MODEL env.
# Model must (a) advertise `tools` capability, (b) be registered with `"tools": true`
# in opencode.json, and (c) run with num_ctx >= 16384 — the 4096 ollama default
# truncates the tool schema and silently breaks tool invocation.
_DEFAULT_MODEL = "ollama/qwen3:8b-16k"


def _load_ai_context(local_path: Path) -> str:
    """Inject .ai/rules content directly into the prompt — mirrors codex.py.

    OpenCode reads project context on its own from CLAUDE.md/AGENTS.md, but the
    hard rules need to be in the prompt verbatim because the local model may
    not proactively open referenced files.
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

    selected_model = model or os.environ.get("OPENCODE_MODEL", _DEFAULT_MODEL)

    cmd = [
        "opencode",
        "run",
        "--format",
        "json",
        "--dir",
        str(local_path),
        "--dangerously-skip-permissions",
        "-m",
        selected_model,
        full_prompt,
    ]

    timeout_s = budget_minutes * 60 if budget_minutes else None

    print(
        f"$ opencode run --format json --dir <repo> --dangerously-skip-permissions "
        f"-m {selected_model} <prompt>"
        + (f" (timeout {int(timeout_s)}s)" if timeout_s else "")
    )

    proc = subprocess.Popen(
        cmd,
        cwd=local_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    tokens_used: int | None = None
    last_text: str | None = None
    error_message: str | None = None
    stderr_lines: list[str] = []

    def on_stdout(raw_line: str) -> None:
        nonlocal tokens_used, last_text, error_message
        raw_line = raw_line.strip()
        if not raw_line:
            return
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            print(raw_line, flush=True)
            return

        event_type = event.get("type")

        if event_type == "text":
            text = (event.get("part") or {}).get("text")
            if text:
                last_text = text
                print(text, flush=True)
        elif event_type == "tool_use":
            part = event.get("part") or {}
            tool = part.get("tool")
            status = (part.get("state") or {}).get("status")
            if tool and status:
                print(f"  [opencode tool] {tool} → {status}", flush=True)
        elif event_type == "step_finish":
            tokens = (event.get("part") or {}).get("tokens") or {}
            total = tokens.get("total")
            if isinstance(total, int):
                tokens_used = total
        elif event_type == "error":
            err = event.get("error") or {}
            msg = (err.get("data") or {}).get("message") or err.get("name") or "unknown error"
            if not error_message:
                error_message = str(msg)
            print(f"  [opencode error] {msg}", flush=True)

    timed_out = _stream_process(proc, timeout_s, on_stdout, stderr_lines)
    if timed_out:
        return AgentResult(exit_code=-1, timed_out=True, provider="opencode")

    if proc.returncode != 0 and stderr_lines:
        print("\n".join(stderr_lines).strip())

    return AgentResult(
        exit_code=proc.returncode,
        output=error_message or last_text,
        tokens_used=tokens_used,
        provider="opencode",
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
