"""Action endpoints: shell allowlist, systemd services, Docker, timers."""

import asyncio
import re as _re
import subprocess

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import srv
from .. import security, audit
from ..auth import is_auth_enabled
from ..collectors import docker_collector

router = APIRouter()

_DOCKER_ACTIONS = {"start", "stop", "restart"}

# Container names / systemd units interpolated into ssh command strings must be
# strictly validated to prevent shell command injection.
_SAFE_NAME = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$").match

# The single shell-execution path — concurrency-capped so a flurry of clicks
# can't exhaust the host.
_shell_semaphore = asyncio.Semaphore(2)


async def run_shell_command(command: str, timeout: int = 30, cap: int = 10240) -> dict:
    """Run *command* in a shell, concurrency-capped and time-bounded.

    Returns ``{returncode, stdout, stderr}`` with each stream decoded and capped
    at *cap* bytes. Raises ``asyncio.TimeoutError`` if it overruns *timeout* (the
    process is killed and drained first).
    """
    async with _shell_semaphore:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()  # drain pipes after kill
            raise
        return {
            "returncode": proc.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace")[:cap],
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[:cap],
        }


@router.post("/api/quick-action/{action_id}")
async def run_quick_action(action_id: str, request: Request):
    """Run a configured quick-action button's shell command, looked up by id.

    The client supplies only the id; the command comes from ``quick_actions`` in
    config (the server never runs a client-supplied string). Auth-gated, audited,
    and run through the shared shell path.
    """
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(status_code=403, content={"error": "not permitted from this client"})
    cfg = srv.load_config()
    action = next((a for a in cfg.get("quick_actions", []) if a.get("id") == action_id), None)
    if not action:
        return JSONResponse(status_code=404, content={"error": "Action not found"})
    shell = (action.get("shell") or "").strip()
    if not shell:
        return JSONResponse(status_code=400, content={"error": "No command configured"})

    actor = "local" if not is_auth_enabled() else "session"
    try:
        result = await run_shell_command(shell, timeout=15)
        outcome = "ok" if result["returncode"] == 0 else "fail"
        audit.append("quick_action", target=action_id, outcome=outcome, actor=actor)
        return {"status": "ok", **result}
    except asyncio.TimeoutError:
        audit.append("quick_action", target=action_id, outcome="timeout", actor=actor)
        return JSONResponse(status_code=408, content={"error": "Command timed out (15s)"})
    except Exception as e:
        audit.append("quick_action", target=action_id, outcome="fail", actor=actor)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/api/services/{name}/{action}")
async def service_control(name: str, action: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)

    if action not in ("restart", "stop", "start"):
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Invalid action: {action}"})

    svc = srv.get_service(name)
    if not svc:
        return JSONResponse(status_code=404, content={"status": "error", "message": f"Unknown service: {name}"})

    svc_type = svc.get("type", "user")
    unit = svc.get("unit")
    container = svc.get("container")

    try:
        if svc_type == "docker":
            if not container:
                return JSONResponse(status_code=400, content={"status": "error", "message": "No container defined"})
            cmd = ["docker", action, container]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        elif svc_type == "root":
            if not unit:
                return JSONResponse(status_code=400, content={"status": "error", "message": "No unit defined"})
            cmd = ["sudo", "systemctl", action, unit]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        else:
            # user service
            if not unit:
                return JSONResponse(status_code=400, content={"status": "error", "message": "No unit defined"})
            cmd = ["systemctl", "--user", action, unit]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        actor = "local" if not is_auth_enabled() else "session"
        if result.returncode == 0:
            audit.append(f"service_{action}", target=name, outcome="ok", actor=actor)
            return {"status": "ok", "message": f"{action.capitalize()}ed {name} successfully"}
        else:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            audit.append(f"service_{action}", target=name, outcome="fail", actor=actor)
            return JSONResponse(status_code=500, content={"status": "error", "message": err})

    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"status": "error", "message": f"Timeout: {action} {name} took longer than 30s"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("/api/docker/containers")
async def list_docker_containers():
    """Return the list of Docker containers via the socket API."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, docker_collector.collect)


@router.post("/api/docker/{container_id}/{action}")
async def docker_container_action(container_id: str, action: str, request: Request):
    """Perform a lifecycle action on a Docker container.

    Only start / stop / restart are accepted.
    Requests must originate from localhost (or a trusted reverse proxy).
    """
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "not permitted from this client"},
        )

    if action not in _DOCKER_ACTIONS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Invalid action: {action}. Must be one of: start, stop, restart"},
        )

    if not _SAFE_NAME(container_id or ""):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid container id"})

    if not docker_collector.docker_available():
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Docker not available"},
        )

    path = f"/v1.41/containers/{container_id}/{action}"
    loop = asyncio.get_running_loop()
    actor = "local" if not is_auth_enabled() else "session"
    try:
        await loop.run_in_executor(None, docker_collector._docker_post, path)
        audit.append(f"docker_{action}", target=container_id, outcome="ok", actor=actor)
        return {"status": "ok", "message": f"{action.capitalize()}ed container {container_id[:12]}"}
    except PermissionError:
        audit.append(f"docker_{action}", target=container_id, outcome="fail", actor=actor)
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "Permission denied: add user to docker group"},
        )
    except RuntimeError as exc:
        audit.append(f"docker_{action}", target=container_id, outcome="fail", actor=actor)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(exc)},
        )
    except Exception as exc:
        audit.append(f"docker_{action}", target=container_id, outcome="fail", actor=actor)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(exc)},
        )


@router.post("/api/docker/{machine}/{container_id}/{action}")
async def docker_remote_action(machine: str, container_id: str, action: str, request: Request):
    """Perform a lifecycle action on a Docker container on any fleet machine.

    For the local machine behaves identically to /api/docker/{container_id}/{action}.
    For remote machines runs: ssh <user>@<host> docker <action> <container_id>
    """
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "not permitted from this client"},
        )

    if not _SAFE_NAME(container_id or ""):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid container id"})

    if action not in _DOCKER_ACTIONS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Invalid action: {action}. Must be one of: start, stop, restart"},
        )

    machine_upper = machine.upper()
    actor = "local" if not is_auth_enabled() else "session"
    remote_hosts = srv._remote_hosts_map()

    if machine_upper == srv._local_machine_name():
        # Local machine — use the local Docker socket
        if not docker_collector.docker_available():
            return JSONResponse(
                status_code=503,
                content={"status": "error", "message": "Docker not available"},
            )
        path = f"/v1.41/containers/{container_id}/{action}"
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, docker_collector._docker_post, path)
            audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="ok", actor=actor)
            return {"status": "ok", "message": f"{action.capitalize()}ed container {container_id[:12]} on {machine_upper}"}
        except PermissionError:
            audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="fail", actor=actor)
            return JSONResponse(status_code=403, content={"status": "error", "message": "Permission denied: add user to docker group"})
        except Exception as exc:
            audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="fail", actor=actor)
            return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})

    elif machine_upper in remote_hosts:
        h = remote_hosts[machine_upper]
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{h['user']}@{h['host']}",
            f"docker {action} {container_id}",
        ]
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=15),
            )
            if result.returncode == 0:
                audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="ok", actor=actor)
                return {"status": "ok", "message": f"{action.capitalize()}ed {container_id[:12]} on {machine_upper}"}
            else:
                err = (result.stderr or result.stdout or "Unknown error").strip()
                audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="fail", actor=actor)
                return JSONResponse(status_code=500, content={"status": "error", "message": err})
        except subprocess.TimeoutExpired:
            audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="fail", actor=actor)
            return JSONResponse(status_code=504, content={"status": "error", "message": f"SSH timeout for {machine_upper}"})
        except Exception as exc:
            audit.append(f"docker_{action}", target=f"{machine_upper}:{container_id}", outcome="fail", actor=actor)
            return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})

    else:
        known = ", ".join([srv._local_machine_name()] + list(remote_hosts.keys())) or "the local machine"
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Unknown machine: {machine}. Configured: {known}"},
        )


@router.post("/api/remote-service/{machine}/{unit}/{action}")
async def remote_service_action(machine: str, unit: str, action: str, request: Request):
    """Run `systemctl --user {action} {unit}` on a remote fleet machine via SSH."""
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(status_code=403, content={"status": "error", "message": "not permitted"})
    if action not in ("restart", "stop", "start"):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid action"})
    if not _SAFE_NAME(unit or ""):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid unit name"})
    machine_upper = machine.upper()
    remote_hosts = srv._remote_hosts_map()
    if machine_upper not in remote_hosts:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Unknown machine: {machine_upper}"})
    h = remote_hosts[machine_upper]
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{h['user']}@{h['host']}",
        f"systemctl --user {action} {unit}",
    ]
    actor = "local" if not is_auth_enabled() else "session"
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        )
        if result.returncode == 0:
            audit.append(f"service_{action}", target=f"{machine_upper}:{unit}", outcome="ok", actor=actor)
            return {"status": "ok", "message": f"{action} {unit} on {machine_upper}"}
        else:
            err = (result.stderr or result.stdout or "Unknown error").strip()
            audit.append(f"service_{action}", target=f"{machine_upper}:{unit}", outcome="fail", actor=actor)
            return JSONResponse(status_code=500, content={"status": "error", "message": err})
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"status": "error", "message": f"SSH timeout for {machine_upper}"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.post("/api/timers/{name}/trigger")
async def timer_trigger(name: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)

    if not _SAFE_NAME(name or ""):
        return JSONResponse({"error": "Invalid timer name"}, status_code=400)

    # Timer names map to .service units for immediate trigger
    service_unit = f"{name}.service"
    actor = "local" if not is_auth_enabled() else "session"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", service_unit],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            audit.append("timer_trigger", target=name, outcome="ok", actor=actor)
            return {"status": "ok", "message": f"Triggered {name} successfully"}
        else:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            audit.append("timer_trigger", target=name, outcome="fail", actor=actor)
            return JSONResponse(status_code=500, content={"status": "error", "message": err})
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"status": "error", "message": f"Timeout triggering {name}"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})
