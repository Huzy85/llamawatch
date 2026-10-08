"""Top processes by CPU and RAM usage collector for llamawatch.

Returns local-machine processes plus top CPU processes from remote fleet machines via SSH.
Remote process data is cached for 15 seconds (fault-tolerant).
"""

import os
import subprocess
import time

# Cache: {pid: (prev_proc_jiffies, prev_total_jiffies)}
_prev_cpu: dict[int, tuple[int, int]] = {}

WIDGET_ID = "resource-hogs"
WIDGET_NAME = "Resource Hogs"
WIDGET_ICON = "\U0001f525"
WIDGET_DESCRIPTION = "Top processes by CPU and RAM usage"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

# Remote hosts for per-machine process donuts
def _remote_proc_hosts() -> list[dict]:
    """Remote fleet machines to query over SSH (from config)."""
    try:
        from ..config import get_remote_fleet_hosts
        return get_remote_fleet_hosts()
    except Exception:
        return []


def _local_name() -> str:
    """Label for the local machine (from fleet config)."""
    try:
        from ..config import get_fleet_hosts
        for h in get_fleet_hosts():
            if h.get("local") and h.get("name"):
                return h["name"]
    except Exception:
        pass
    import socket
    return socket.gethostname().split(".")[0]

_PROC_CACHE_TTL = 15  # seconds
_remote_proc_cache: dict = {"ts": 0.0, "data": None}


def _list_pids() -> list[int]:
    """Return all numeric PID entries from /proc."""
    pids = []
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                pids.append(int(entry))
    except Exception:
        pass
    return pids


def _total_cpu_jiffies() -> int:
    """Read /proc/stat first line and return total jiffies across all fields."""
    try:
        with open("/proc/stat") as f:
            first_line = f.readline()
        parts = first_line.split()
        # parts[0] == "cpu", parts[1:] == user nice system idle iowait irq softirq steal ...
        return sum(int(v) for v in parts[1:])
    except Exception:
        return 0


def _read_proc(pid: int) -> dict | None:
    """Read /proc/PID/stat, /proc/PID/status, and /proc/PID/cmdline.

    Returns a dict with keys: pid, name, cmdline, cpu_jiffies, ram_mb.
    Returns None on any failure (process may have exited).
    """
    base = f"/proc/{pid}"
    try:
        # --- /proc/PID/stat ---
        with open(f"{base}/stat") as f:
            stat_data = f.read()

        # Process name sits between first '(' and last ')' to handle parens in names
        lparen = stat_data.index("(")
        rparen = stat_data.rindex(")")
        comm = stat_data[lparen + 1 : rparen]
        after_comm = stat_data[rparen + 2 :]  # skip ') '
        fields = after_comm.split()
        # fields[0] is state, fields[11] is utime, fields[12] is stime (0-indexed after comm)
        if len(fields) < 13:
            return None
        utime = int(fields[11])
        stime = int(fields[12])
        cpu_jiffies = utime + stime

        # --- /proc/PID/status (VmRSS) ---
        ram_kb = 0
        try:
            with open(f"{base}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            ram_kb = int(parts[1])
                        break
        except Exception:
            pass

        # --- /proc/PID/cmdline ---
        cmdline = ""
        try:
            with open(f"{base}/cmdline", "rb") as f:
                raw = f.read(512)
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except Exception:
            pass

        return {
            "pid": pid,
            "name": comm,
            "cmdline": cmdline,
            "cpu_jiffies": cpu_jiffies,
            "ram_mb": round(ram_kb / 1024.0, 1),
        }
    except Exception:
        return None


def _read_all_procs() -> list[dict]:
    """Collect process info for all PIDs, compute cpu_pct, filter noise.

    Uses delta-based CPU calculation (same interval pattern as system.py) so
    the percentage matches what top/htop shows over the polling window.

    Filters out processes with 0 cpu_pct and less than 10 MB RAM.
    """
    global _prev_cpu

    current_total = _total_cpu_jiffies()
    current_pids = _list_pids()

    procs = []
    new_cache: dict[int, tuple[int, int]] = {}

    for pid in current_pids:
        info = _read_proc(pid)
        if info is None:
            continue

        cur_proc = info["cpu_jiffies"]

        if pid in _prev_cpu:
            prev_proc, prev_total = _prev_cpu[pid]
            delta_proc = cur_proc - prev_proc
            delta_total = current_total - prev_total
            if delta_total > 0:
                cpu_pct = round(delta_proc / delta_total * 100, 2)
            else:
                cpu_pct = 0.0
        else:
            # No baseline yet — report 0 on first poll
            cpu_pct = 0.0

        new_cache[pid] = (cur_proc, current_total)

        if cpu_pct == 0.0 and info["ram_mb"] < 10:
            continue

        procs.append(
            {
                "pid": info["pid"],
                "name": info["name"],
                "cmdline": info["cmdline"],
                "cpu_pct": cpu_pct,
                "ram_mb": info["ram_mb"],
            }
        )

    # Replace cache, discarding stale PIDs automatically
    _prev_cpu = new_cache

    return procs


def _get_top_processes(n: int = 8) -> list[dict]:
    """Return up to n processes sorted by cpu_pct descending."""
    procs = _read_all_procs()
    procs.sort(key=lambda p: (p["cpu_pct"], p["ram_mb"]), reverse=True)
    return procs[:n]


def _fetch_remote_procs(host_def: dict, n: int = 5) -> dict:
    """SSH into a remote host and return top-N CPU processes + actual machine CPU%.

    ps %cpu is per-core, so we also read /proc/stat to get true machine-wide CPU%.
    Returns {"procs": [...], "machine_cpu_pct": float} on success,
            {"procs": [], "machine_cpu_pct": None} on failure.
    """
    # Single SSH call: get ps output + /proc/stat snapshot (two reads, 200ms apart)
    # to compute actual CPU%. Simpler: use mpstat if available, otherwise top -bn2.
    # Most reliable cross-distro: read /proc/stat idle vs total.
    remote_script = (
        # Snapshot 1
        "s1=$(cat /proc/stat | head -1); "
        "sleep 0.4; "
        # Snapshot 2
        "s2=$(cat /proc/stat | head -1); "
        "echo \"STAT $s1 ||| $s2\"; "
        f"ps -eo comm,%cpu --sort=-%cpu --no-headers | head -{n + 5}"
    )
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=3",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{host_def['user']}@{host_def['host']}",
        remote_script,
    ]
    empty = {"procs": [], "machine_cpu_pct": None}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if result.returncode != 0:
            return empty

        machine_cpu_pct = None
        procs = []

        for line in result.stdout.strip().splitlines():
            if line.startswith("STAT "):
                # Parse the two /proc/stat snapshots to get actual CPU%
                try:
                    rest = line[5:]
                    s1_raw, s2_raw = rest.split("|||")
                    def _parse_stat(s):
                        nums = [int(x) for x in s.split()[1:]]
                        total = sum(nums)
                        idle = nums[3] if len(nums) > 3 else 0
                        return total, idle
                    t1, i1 = _parse_stat(s1_raw)
                    t2, i2 = _parse_stat(s2_raw)
                    dt = t2 - t1
                    di = i2 - i1
                    if dt > 0:
                        machine_cpu_pct = round((1 - di / dt) * 100, 1)
                except Exception:
                    pass
                continue

            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            name = parts[0]
            try:
                cpu_pct = float(parts[1])
            except ValueError:
                continue
            if cpu_pct == 0.0:
                continue
            procs.append({"name": name, "cpu_pct": cpu_pct})

        procs.sort(key=lambda p: p["cpu_pct"], reverse=True)
        return {"procs": procs[:n], "machine_cpu_pct": machine_cpu_pct}

    except subprocess.TimeoutExpired:
        return empty
    except Exception:
        return empty


def _collect_remote_procs_all() -> list[dict]:
    """Return per-machine top process list for remote fleet machines (cached 15s)."""
    global _remote_proc_cache

    now = time.monotonic()
    if _remote_proc_cache["data"] is not None and (now - _remote_proc_cache["ts"]) < _PROC_CACHE_TTL:
        return _remote_proc_cache["data"]

    results = []
    for h in _remote_proc_hosts():
        data = _fetch_remote_procs(h)
        results.append({
            "name": h["name"],
            "procs": data["procs"],
            "machine_cpu_pct": data["machine_cpu_pct"],
        })

    _remote_proc_cache = {"ts": now, "data": results}
    return results


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect top process data for the local machine + remote fleet machines.

    Returns::

        {
            "processes": [  # local-machine processes (legacy shape, kept for compat)
                {
                    "pid": 1234,
                    "name": "llama-server",
                    "cmdline": "./llama-server -m model.gguf",
                    "cpu_pct": 42.5,
                    "ram_mb": 8192.0,
                },
                ...
            ],
            "machines": [   # Per-machine top processes for donuts
                {"name": "<machine>", "procs": [{"name": "llama-server", "cpu_pct": 42.5}, ...]},
                ...
            ]
        }
    """
    try:
        local_procs = _get_top_processes()
    except Exception:
        local_procs = []

    # Build local-machine compact shape for the donut (top 5 by CPU)
    local_donut = [{"name": p["name"], "cpu_pct": p["cpu_pct"]} for p in local_procs[:5]]

    try:
        remote_machines = _collect_remote_procs_all()
    except Exception:
        remote_machines = [{"name": h["name"], "procs": []} for h in _remote_proc_hosts()]

    machines = [{"name": _local_name(), "procs": local_donut, "machine_cpu_pct": None}] + remote_machines

    return {
        "processes": local_procs,
        "machines": machines,
    }
