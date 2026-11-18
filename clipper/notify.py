"""macOS desktop notifications via osascript."""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(message: str, title: str = "New clip ready", subtitle: str = "") -> None:
    script = f"display notification {_quote(message)} with title {_quote(title)}"
    if subtitle:
        script += f" subtitle {_quote(subtitle)}"
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("notification failed: %r", e)
