"""Append-only JSONL audit log for write actions."""
import json
import os
import time
from pathlib import Path

_LOG_FILE = Path.home() / ".config" / "llamawatch" / "audit.log"


def append(action: str, target: str = "", outcome: str = "ok", actor: str = "local", **extra) -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "action": action, "target": target, "outcome": outcome, "actor": actor}
    rec.update(extra)
    with open(_LOG_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        os.chmod(_LOG_FILE, 0o600)
    except OSError:
        pass


def read(limit: int = 100) -> list:
    if not _LOG_FILE.is_file():
        return []
    lines = _LOG_FILE.read_text().splitlines()
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
