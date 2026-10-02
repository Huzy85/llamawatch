"""Service status collector for llamawatch."""

WIDGET_ID = "services"
WIDGET_NAME = "Services"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "⚙️"
WIDGET_DESCRIPTION = "Service health monitoring"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import subprocess
import os
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from ..config import get_services


def collect(config=None, adapters=None) -> list[dict]:
    """Collect service status — registry-compatible entry point."""
    return collect_services()


def collect_services() -> list[dict]:
    """Check status of all configured services. Returns list of dicts with
    name, status ('active'|'inactive'|'failed'|'unknown'), and port."""
    results = []
    for svc in get_services():
        name = svc["name"]
        port = svc.get("port")
        svc_type = svc.get("type", "user")
        status = "unknown"

        try:
            if svc_type == "user":
                unit = svc.get("unit")
                if unit:
                    proc = subprocess.run(
                        ["systemctl", "--user", "is-active", unit],
                        capture_output=True, text=True, timeout=5,
                    )
                    out = proc.stdout.strip()
                    if out == "active":
                        status = "active"
                    elif out == "failed":
                        status = "failed"
                    else:
                        status = "inactive"

            elif svc_type == "root":
                unit = svc.get("unit")
                if unit:
                    proc = subprocess.run(
                        ["systemctl", "is-active", unit],
                        capture_output=True, text=True, timeout=5,
                    )
                    out = proc.stdout.strip()
                    if out == "active":
                        status = "active"
                    elif out == "failed":
                        status = "failed"
                    else:
                        status = "inactive"

            elif svc_type == "docker":
                container = svc.get("container")
                if container:
                    proc = subprocess.run(
                        ["docker", "inspect", "-f", "{{.State.Running}}", container],
                        capture_output=True, text=True, timeout=5,
                    )
                    out = proc.stdout.strip()
                    if out == "true":
                        status = "active"
                    else:
                        status = "inactive"

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            status = "unknown"

        # Health endpoint check — if service appears active but health fails,
        # mark as degraded (treated as "active" for now).
        health_url = svc.get("health")
        if health_url and status == "active":
            try:
                req = Request(health_url, method="GET")
                with urlopen(req, timeout=2) as resp:
                    if resp.status >= 400:
                        status = "degraded"
            except (URLError, OSError, ValueError):
                status = "degraded"

        results.append({"name": name, "status": status, "port": port})

    return results


