"""Claude Code process collector for llamawatch."""

WIDGET_ID = "claude-code"
WIDGET_NAME = "Terminal"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "🖥️"
WIDGET_DESCRIPTION = "Running terminal processes"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import subprocess
import os
from pathlib import Path


def collect(config=None, adapters=None) -> list[dict]:
    """Collect Claude Code processes — registry-compatible entry point."""
    return collect_claude_code()


def collect_claude_code() -> list[dict]:
    """Find running Claude Code processes and return their details."""
    results = []

    # Use ps with specific columns for richer data
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,tty,%cpu,%mem,rss,etime,args", "--no-headers"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return results
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return results

    for line in proc.stdout.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue

        # Match only processes whose command is exactly "claude"
        command = parts[6].strip()
        if command != "claude":
            continue

        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue

        # Verify process still exists
        if not Path(f"/proc/{pid}").exists():
            continue

        tty = parts[1] if parts[1] != "?" else None
        try:
            cpu_pct = float(parts[2])
        except (ValueError, IndexError):
            cpu_pct = 0.0
        try:
            mem_pct = float(parts[3])
        except (ValueError, IndexError):
            mem_pct = 0.0
        try:
            rss_kb = int(parts[4])
            mem_mb = round(rss_kb / 1024)
        except (ValueError, IndexError):
            mem_mb = 0
        uptime = parts[5].strip()  # e.g. "2-04:59:33" or "12:57:32"

        # Working directory
        cwd = ""
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except (OSError, PermissionError):
            cwd = "unknown"

        # Status
        status = "running" if cpu_pct > 50.0 else "active"

        # Build a meaningful description from the session context
        desc = _build_description(pid, cwd, tty)

        results.append({
            "pid": pid,
            "cwd": cwd,
            "desc": desc,
            "cpu_pct": cpu_pct,
            "mem_mb": mem_mb,
            "uptime": _format_uptime(uptime),
            "tty": tty,
            "status": status,
        })

    return results


def _format_uptime(raw: str) -> str:
    """Convert ps etime format to human readable. '2-04:59:33' -> '2d 4h', '12:57:32' -> '12h 57m', '03:22' -> '3m'."""
    try:
        if "-" in raw:
            days, rest = raw.split("-", 1)
            parts = rest.split(":")
            return f"{days}d {int(parts[0])}h"
        parts = raw.split(":")
        if len(parts) == 3:
            h, m, _ = parts
            h, m = int(h), int(m)
            if h > 0:
                return f"{h}h {m}m"
            return f"{m}m"
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return f"{m}m" if m > 0 else f"{s}s"
    except (ValueError, IndexError):
        pass
    return raw


def _build_description(pid: int, cwd: str, tty: str | None) -> str:
    """Build a description for a Claude Code session by reading recent activity."""
    # Try to find the session's most recent task output
    task_base = Path(f"/tmp/claude-{os.getuid()}")
    best_file = None
    best_mtime = 0.0

    # Map session dir by finding which one was most recently active
    # and has task files matching our rough timeframe
    try:
        if task_base.is_dir():
            for session_dir in task_base.glob("*/*/tasks"):
                for output_file in session_dir.glob("*.output"):
                    try:
                        mtime = output_file.stat().st_mtime
                        if mtime > best_mtime:
                            # Check if the output file is recent (last 30 min)
                            import time
                            if time.time() - mtime < 1800:
                                best_mtime = mtime
                                best_file = output_file
                    except OSError:
                        continue
    except (OSError, PermissionError):
        pass

    if best_file is not None:
        try:
            text = best_file.read_text(errors="replace").strip()
            # Get meaningful lines (skip empty, JSON blobs, very short)
            lines = []
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("{") or ln.startswith("[") or len(ln) < 10:
                    continue
                lines.append(ln)
            if lines:
                # Take the last meaningful line as description
                snippet = lines[-1]
                if len(snippet) > 100:
                    snippet = snippet[:97] + "..."
                return snippet
        except (OSError, PermissionError):
            pass

    # Fallback: describe by working directory
    if cwd and cwd != "unknown":
        basename = Path(cwd).name
        home = str(Path.home())
        if cwd == home:
            return "Home directory session"
        return f"Working in {basename}/"
    return "Claude Code session"
