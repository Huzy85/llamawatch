"""Fleet collector — per-machine system stats (local + SSH).

Returns one entry per configured fleet machine with CPU%, RAM, temp, disk,
load and uptime. The local machine is read from /proc; remote machines are
polled via a single SSH call each (inline-python piped over stdin).

Results are cached for _CACHE_TTL seconds (default 15s) so rapid SSE ticks
don't hammer SSH on every call.
"""

import json
import subprocess
import time
from pathlib import Path

WIDGET_ID = "fleet"
WIDGET_NAME = "Fleet"
WIDGET_DEFAULT_SIZE = {"w": 8, "h": 3, "minW": 4, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "🖥️"
WIDGET_DESCRIPTION = "CPU, RAM, disk, temp and uptime for each fleet machine"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False
WIDGET_CONFIG_REQUIRED = False

# Hosts come from config (fleet.hosts). When unset, get_fleet_hosts() returns
# a single auto-detected local machine — see config.get_fleet_hosts().

# Python script piped over SSH stdin — avoids all shell quoting complexity.
# Uses
# `python3 /dev/stdin` so the script body needs no escaping at all.
_REMOTE_SCRIPT = """\
import json, subprocess, re, time as _t

def sh(c):
    try: return subprocess.check_output(c, shell=True, text=True, timeout=2).strip()
    except: return ""

mem = sh("free -m | awk 'NR==2{print $2,$3}'").split()
disk_raw = sh("df / | awk 'NR==2{print $5}'").strip().rstrip("%")
load_raw = sh("cat /proc/loadavg").split()
sens = sh("sensors 2>/dev/null")
ct = [float(m) for m in re.findall(r"Core \\d+:\\s+\\+([\\d\\.]+).C", sens)]
try: up = int(float(open("/proc/uptime").read().split()[0]))
except: up = None

rtm = int(mem[0]) if len(mem) >= 2 else None
rum = int(mem[1]) if len(mem) >= 2 else None
rtg = round(rtm / 1024, 1) if rtm else None
rug = round(rum / 1024, 1) if rum else None
rp  = round(rum / rtm * 100, 1) if rtm else None
try: dp = float(disk_raw)
except: dp = None
l1 = float(load_raw[0]) if load_raw else None
ctemp = round(max(ct), 1) if ct else None

us = None
if up:
    d, r = divmod(up, 86400); h, r2 = divmod(r, 3600); m = r2 // 60
    us = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")

def rs():
    ln = open("/proc/stat").readline().split()
    v = [int(x) for x in ln[1:]]; i = v[3] + v[4]; return i, sum(v)

s1 = rs(); _t.sleep(0.2); s2 = rs()
td = s2[1] - s1[1]
cp = round((1 - (s2[0] - s1[0]) / td) * 100, 1) if td else None

print(json.dumps({"cpu_pct": cp, "ram_pct": rp, "ram_used_gb": rug,
                  "ram_total_gb": rtg, "cpu_temp": ctemp, "disk_pct": dp,
                  "load1": l1, "uptime": us}))
"""

_CACHE_TTL = 15  # seconds
_cache: dict = {"ts": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Local machine reads (no SSH)
# ---------------------------------------------------------------------------


def _read_proc_stat_pair():
    """Return (idle, total) jiffies from /proc/stat."""
    try:
        content = Path("/proc/stat").read_text()
        parts = content.split("\n")[0].split()
        vals = [int(v) for v in parts[1:]]
        idle = vals[3] + vals[4]
        return idle, sum(vals)
    except Exception:
        return None


def _local_cpu_pct():
    """Measure CPU% over a 0.2s window using two /proc/stat reads."""
    s1 = _read_proc_stat_pair()
    if s1 is None:
        return None
    time.sleep(0.2)
    s2 = _read_proc_stat_pair()
    if s2 is None:
        return None
    idle_d = s2[0] - s1[0]
    total_d = s2[1] - s1[1]
    if total_d == 0:
        return 0.0
    return round((1.0 - idle_d / total_d) * 100.0, 1)


def _local_ram():
    """Parse /proc/meminfo; returns (ram_used_gb, ram_total_gb, ram_pct)."""
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                parts = val.strip().split()
                if parts:
                    meminfo[key.strip()] = int(parts[0])
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        used_kb = total_kb - available_kb
        total_gb = round(total_kb / (1024 * 1024), 1)
        used_gb = round(used_kb / (1024 * 1024), 1)
        pct = round(used_kb / total_kb * 100, 1) if total_kb else None
        return used_gb, total_gb, pct
    except Exception:
        return None, None, None


def _local_cpu_temp():
    """Read CPU temp from hwmon — works on AMD (k10temp) and Intel (coretemp)."""
    # Preferred CPU sensor drivers, in priority order
    cpu_drivers = ("k10temp", "coretemp", "zenpower", "cpu_thermal")
    try:
        sensors = {}
        for hwmon_dir in Path("/sys/class/hwmon").iterdir():
            name_file = hwmon_dir / "name"
            if not name_file.exists():
                continue
            sensors[name_file.read_text().strip()] = hwmon_dir
        # Try known CPU drivers first, then any sensor with a temp1_input
        for drv in cpu_drivers:
            if drv in sensors and (sensors[drv] / "temp1_input").exists():
                raw = int((sensors[drv] / "temp1_input").read_text().strip())
                return round(raw / 1000.0, 1)
        for hwmon_dir in sensors.values():
            tin = hwmon_dir / "temp1_input"
            if tin.exists():
                return round(int(tin.read_text().strip()) / 1000.0, 1)
    except Exception:
        pass
    return None


def _local_disk_pct():
    """Root filesystem usage percentage."""
    import os
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used = total - free
        return round(used / total * 100, 1) if total > 0 else None
    except Exception:
        return None


def _local_load1():
    """First load average from /proc/loadavg."""
    try:
        return float(Path("/proc/loadavg").read_text().split()[0])
    except Exception:
        return None


def _local_uptime():
    """Uptime string like '5d 12h' from /proc/uptime."""
    try:
        secs = int(float(Path("/proc/uptime").read_text().split()[0]))
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return f"{d}d {h}h"
        elif h:
            return f"{h}h {m}m"
        else:
            return f"{m}m"
    except Exception:
        return None


def _collect_local(host_def: dict) -> dict:
    ram_used, ram_total, ram_pct = _local_ram()
    return {
        "name": host_def.get("name", "local"),
        "host": host_def.get("host", "localhost"),
        "online": True,
        "cpu_pct": _local_cpu_pct(),
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "cpu_temp": _local_cpu_temp(),
        "disk_pct": _local_disk_pct(),
        "load1": _local_load1(),
        "uptime": _local_uptime(),
    }


# ---------------------------------------------------------------------------
# Remote SSH collect
# ---------------------------------------------------------------------------


def _collect_ssh(host_def: dict) -> dict:
    """SSH into a remote host and collect stats. Returns online:False on any failure.

    The Python script is piped over stdin to avoid shell-quoting issues with
    inline -c arguments (uses
    stdin delivery for cleaner quoting).
    """
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=3",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{host_def['user']}@{host_def['host']}",
        "python3 /dev/stdin",
    ]
    _null = {"cpu_pct": None, "ram_pct": None, "ram_used_gb": None,
             "ram_total_gb": None, "cpu_temp": None, "disk_pct": None,
             "load1": None, "uptime": None}
    try:
        result = subprocess.run(
            cmd, input=_REMOTE_SCRIPT,
            capture_output=True, text=True, timeout=4,
        )
        if result.returncode != 0:
            return {"name": host_def["name"], "host": host_def["host"],
                    "online": False, **_null}
        data = json.loads(result.stdout)
        return {"name": host_def["name"], "host": host_def["host"],
                "online": True, **data}
    except subprocess.TimeoutExpired:
        return {"name": host_def["name"], "host": host_def["host"],
                "online": False, **_null}
    except Exception:
        return {"name": host_def["name"], "host": host_def["host"],
                "online": False, **_null}


# ---------------------------------------------------------------------------
# Public collect
# ---------------------------------------------------------------------------


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect stats for all machines; returns {"machines": [...]}."""
    global _cache

    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    # Resolve host list from config (falls back to a single local machine)
    if config and config.get("fleet", {}).get("hosts"):
        hosts = config["fleet"]["hosts"]
    else:
        from ..config import get_fleet_hosts
        hosts = get_fleet_hosts()

    machines = []
    for h in hosts:
        if h.get("local"):
            machines.append(_collect_local(h))
        else:
            machines.append(_collect_ssh(h))

    result = {"machines": machines}
    _cache = {"ts": now, "data": result}
    return result
