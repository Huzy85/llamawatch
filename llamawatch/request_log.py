"""Request logger — JSONL-based request history for llamawatch."""

import json
import os
import time
from datetime import datetime
from pathlib import Path


class RequestLog:
    """Append-only JSONL logger for LLM requests."""

    def __init__(self, log_dir: str | None = None):
        self._log_dir = Path(log_dir or os.path.expanduser("~/.config/llamawatch/request_history"))
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _log_path(self) -> Path:
        """One file per day: requests-YYYY-MM-DD.jsonl"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"requests-{date_str}.jsonl"

    def log_request(
        self,
        model: str,
        prompt_preview: str,
        response_preview: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        duration_ms: int | None = None,
        source: str = "chat",
    ) -> None:
        """Append a request entry to today's log file."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "prompt_preview": prompt_preview[:200],
            "response_preview": response_preview[:200],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "duration_ms": duration_ms,
            "source": source,
        }
        try:
            with open(self._log_path(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def get_recent(self, limit: int = 50) -> list[dict]:
        """Read the most recent N entries across all log files. Most recent first."""
        entries: list[dict] = []
        try:
            files = sorted(self._log_dir.glob("requests-*.jsonl"), reverse=True)
            for f in files:
                if len(entries) >= limit:
                    break
                lines = f.read_text().strip().split("\n")
                for line in reversed(lines):
                    if len(entries) >= limit:
                        break
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return entries[:limit]
