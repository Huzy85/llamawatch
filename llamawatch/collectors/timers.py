"""Timer status collector for llamawatch."""

import subprocess

WIDGET_ID = "timers"
WIDGET_NAME = "Timers"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "⏰"
WIDGET_DESCRIPTION = "systemd timer status"
WIDGET_CONFIG_SCHEMA = [
    {
        "key": "filter_timers",
        "label": "Show only these timers",
        "type": "multiselect",
        "options_from": "discovered_timers",
        "description": "Leave empty to show all",
    },
]
WIDGET_MULTI_INSTANCE = False


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect timer status — registry-compatible entry point."""
    wc = widget_config or {}
    filter_timers = wc.get("filter_timers", [])
    timers = collect_timers()
    if filter_timers:
        timers = [t for t in timers if t["name"] in filter_timers]
    return {"timers": timers}


def _parse_systemd_timestamp(raw: str | None) -> str | None:
    """Extract an ISO 8601 timestamp from systemctl output.

    Input examples:
      'Mon 2026-03-23 16:25:26 GMT'
      'Mon 2026-03-23 16:25:26 GMT     1min left'
      'n/a'
    Returns ISO string like '2026-03-23T16:25:26' or None.
    """
    if not raw or raw.strip().lower() == "n/a":
        return None
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", raw)
    if m:
        return f"{m.group(1)}T{m.group(2)}"
    return None


def collect_timers() -> list[dict]:
    """List user systemd timers with next-fire time and last execution status."""
    results = []

    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-timers", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return results
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return results

    import re

    lines = proc.stdout.strip().splitlines()
    ts_pattern = r"[A-Z][a-z]{2}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\S+"

    for line in lines:
        line = line.strip()
        if not line or "NEXT" in line or "timers listed" in line:
            continue

        timestamps = re.findall(ts_pattern, line)
        next_raw = timestamps[0] if len(timestamps) >= 1 else None
        last_raw = timestamps[1] if len(timestamps) >= 2 else None

        next_iso = _parse_systemd_timestamp(next_raw)
        last_iso = _parse_systemd_timestamp(last_raw)

        service_matches = re.findall(r'(\S+\.service)', line)
        if not service_matches:
            continue
        service_unit = service_matches[-1]
        display_name = service_unit.replace(".service", "")

        last_status = None
        last_detail = None
        try:
            status_proc = subprocess.run(
                ["systemctl", "--user", "show", service_unit,
                 "-p", "ExecMainStatus", "--value"],
                capture_output=True, text=True, timeout=5,
            )
            code = status_proc.stdout.strip()
            if code == "0":
                last_status = "ok"
            elif code:
                last_status = "failed"
                last_detail = f"exit code {code}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        results.append({
            "name": display_name,
            "next": next_iso,
            "last_status": last_status,
            "last_detail": last_detail,
            "last_time": last_iso,
        })

    return results
