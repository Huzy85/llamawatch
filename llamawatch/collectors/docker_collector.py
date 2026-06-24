"""Docker container monitoring collector for llamawatch.

Local containers are read via the local Docker socket. Remote fleet
machines (from config) are fetched via SSH, reusing the same
BatchMode/ConnectTimeout pattern as fleet.py. Results are merged and each
container tagged with its machine name. Remote results are cached for 15
seconds to avoid SSH on every tick.
"""

import http.client
import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

WIDGET_ID = "docker"
WIDGET_NAME = "Docker"
WIDGET_ICON = "\U0001f433"
WIDGET_DESCRIPTION = "Docker container monitoring and controls"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

_DOCKER_SOCKET = "/var/run/docker.sock"

# Remote hosts to query via SSH
def _remote_hosts() -> list[dict]:
    """Remote fleet machines to query over SSH (from config)."""
    try:
        from ..config import get_remote_fleet_hosts
        return get_remote_fleet_hosts()
    except Exception:
        return []


def _local_name() -> str:
    """Label for containers on the local machine (from fleet config)."""
    try:
        from ..config import get_fleet_hosts
        for h in get_fleet_hosts():
            if h.get("local") and h.get("name"):
                return h["name"]
    except Exception:
        pass
    return socket.gethostname().split(".")[0]

# SSH docker ps format string
_DOCKER_PS_CMD = "docker ps -a --format '{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}'"

# Cache for remote containers: {"ts": float, "data": list[dict]}
_CACHE_TTL = 15
_remote_cache: dict = {"ts": 0.0, "data": None}

# Cache for CPU stats: {container_id: cpu_pct}.  Refreshed every 8 s in background.
_STATS_TTL = 8
_stats_cache: dict = {"ts": 0.0, "data": {}, "refreshing": False}



def _do_refresh_stats() -> dict:
    """Run 'docker stats --no-stream' and return {short_id: cpu_pct}."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.ID}}|{{.CPUPerc}}"],
            capture_output=True, text=True, timeout=8,
        )
        out: dict = {}
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split("|", 1)
            if len(parts) != 2:
                continue
            short_id = parts[0][:12]
            pct_str = parts[1].replace("%", "").strip()
            try:
                out[short_id] = round(float(pct_str), 1)
            except ValueError:
                pass
        return out
    except Exception:
        return {}


def _maybe_refresh_stats(running_ids: list) -> None:
    """Trigger a background refresh of CPU stats if the cache is stale."""
    if not running_ids:
        return
    now = time.monotonic()
    if _stats_cache["refreshing"]:
        return
    if now - _stats_cache["ts"] < _STATS_TTL:
        return

    _stats_cache["refreshing"] = True

    def _worker() -> None:
        data = _do_refresh_stats()
        _stats_cache["data"] = data
        _stats_cache["ts"] = time.monotonic()
        _stats_cache["refreshing"] = False

    threading.Thread(target=_worker, daemon=True).start()


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP connection routed through a Unix domain socket."""

    def __init__(self, socket_path: str = _DOCKER_SOCKET, timeout: int = 5):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def docker_available() -> bool:
    """Return True if the Docker socket exists and is readable."""
    return os.path.exists(_DOCKER_SOCKET) and os.access(_DOCKER_SOCKET, os.R_OK)


def _docker_get(path: str) -> object:
    """GET *path* from the Docker socket API. Returns parsed JSON."""
    conn = UnixHTTPConnection()
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"Docker API {resp.status}: {body[:200]}")
        return json.loads(body) if body.strip() else {}
    finally:
        conn.close()


def _docker_post(path: str) -> object:
    """POST *path* to the Docker socket API (empty body). Returns parsed JSON."""
    conn = UnixHTTPConnection()
    try:
        conn.request("POST", path, body=b"", headers={"Content-Length": "0"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"Docker API {resp.status}: {body[:200]}")
        return json.loads(body) if body.strip() else {}
    finally:
        conn.close()


def _fetch_remote_containers(host_def: dict) -> list[dict]:
    """SSH into a remote host and return its containers. Returns [] on any failure."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=3",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{host_def['user']}@{host_def['host']}",
        _DOCKER_PS_CMD,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6,
        )
        if result.returncode != 0:
            return []
        containers = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 3:
                continue
            name, image, state = parts[0], parts[1], parts[2]
            status = parts[3] if len(parts) > 3 else ""
            containers.append({
                "id": "",
                "name": name,
                "image": image,
                "state": state,
                "status": status,
                "created": "",
                "machine": host_def["name"],
            })
        return containers
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


def _collect_remote_all() -> list[dict]:
    """Collect containers from all remote hosts (cached 15s)."""
    global _remote_cache

    now = time.monotonic()
    if _remote_cache["data"] is not None and (now - _remote_cache["ts"]) < _CACHE_TTL:
        return _remote_cache["data"]

    all_remote: list[dict] = []
    for h in _remote_hosts():
        all_remote.extend(_fetch_remote_containers(h))

    _remote_cache = {"ts": now, "data": all_remote}
    return all_remote


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect Docker container data from the local machine (socket) + remote fleet machines (SSH).

    Returns::

        {
            "available": bool,
            "error": str | None,
            "containers": [
                {
                    "id": "abc123......",   # 12 chars (empty for remote)
                    "name": "myapp",
                    "image": "python:3.12",
                    "state": "running",
                    "status": "Up 3 hours",
                    "created": "2026-03-20T12:00:00",
                    "machine": "<machine name from fleet config>",
                },
                ...
            ]
        }
    """
    containers: list[dict] = []
    local_error: str | None = None

    # --- local machine (Unix socket) ---
    if not docker_available():
        local_error = "Docker not available"
    else:
        try:
            raw = _docker_get("/v1.41/containers/json?all=true")
        except PermissionError:
            local_error = "Permission denied: add user to docker group"
            raw = None
        except Exception as exc:
            local_error = str(exc)
            raw = None

        if raw is not None:
            for c in raw:
                names = c.get("Names") or [""]
                name = names[0].lstrip("/") if names else ""
                created_ts = c.get("Created", 0)
                try:
                    created_iso = datetime.fromtimestamp(
                        created_ts, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%S")
                except (OSError, OverflowError, ValueError):
                    created_iso = ""

                containers.append({
                    "id": (c.get("Id") or "")[:12],
                    "name": name,
                    "image": c.get("Image", ""),
                    "state": c.get("State", ""),
                    "status": c.get("Status", ""),
                    "created": created_iso,
                    "machine": _local_name(),
                })

    # --- remote machines via SSH (cached, fault-tolerant) ---
    containers.extend(_collect_remote_all())

    # --- CPU stats for local running containers (background-cached) ---
    local_name = _local_name()
    running_ids = [
        c["id"] for c in containers
        if c.get("machine") == local_name and c.get("state") == "running" and c.get("id")
    ]
    _maybe_refresh_stats(running_ids)
    stats_data = _stats_cache["data"]
    for c in containers:
        if c.get("id") and c["id"] in stats_data:
            c["cpu_pct"] = stats_data[c["id"]]

    available = local_error is None or len(containers) > 0
    return {
        "available": available,
        "error": local_error if not available else None,
        "containers": containers,
    }
