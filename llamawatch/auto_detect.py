"""Auto-detection for LLM backends, sensors, and services."""

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path


def _probe_url(url, timeout=2.0):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False

def _get_json(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

def scan_backends():
    """Probe common ports for LLM backends. Returns list of backend dicts."""
    found = []
    seen_urls = set()
    # llama.cpp on common ports
    for port in [8080, 8081, 8082, 8083, 8084]:
        url = f"http://localhost:{port}"
        if url in seen_urls:
            continue
        if _probe_url(f"{url}/health"):
            models = _get_json(f"{url}/v1/models")
            model_name = "unknown"
            if models and "data" in models and models["data"]:
                model_name = models["data"][0].get("id", "unknown")
            # Use the model ID as the backend name (trimmed to 30 chars)
            name = model_name if model_name and model_name != "unknown" else f"llama.cpp:{port}"
            name = name[:30]
            found.append({
                "type": "llamacpp", "url": url, "name": name,
                "swap_proxy": port == 8081,
                "detected_model": model_name,
            })
            seen_urls.add(url)
    # Ollama on 11434
    tags = _get_json("http://localhost:11434/api/tags")
    if tags and "models" in tags:
        models = tags["models"]
        name = models[0]["name"] if models else "Ollama"
        found.append({
            "type": "ollama", "url": "http://localhost:11434",
            "name": name[:30],
            "detected_models": len(models),
        })
    # Generic OpenAI-compatible on common ports
    for port in [8000, 5000, 3000]:
        url = f"http://localhost:{port}"
        if url in seen_urls:
            continue
        models = _get_json(f"{url}/v1/models")
        if models and "data" in models:
            data = models["data"]
            name = data[0].get("id", f"openai:{port}")[:30] if data else f"openai:{port}"
            found.append({
                "type": "openai", "url": url, "name": name,
                "detected_models": len(data),
            })
            seen_urls.add(url)
    return found

def scan_sensors():
    """Detect available hardware sensors. Returns dict."""
    sensors = {"temps": [], "gpu_util": False, "nvidia": False}
    hwmon = Path("/sys/class/hwmon")
    if hwmon.exists():
        known = {"k10temp": "cpu", "coretemp": "cpu", "amdgpu": "gpu", "nvme": "ssd"}
        for d in hwmon.iterdir():
            name_file = d / "name"
            if name_file.exists():
                try:
                    name = name_file.read_text().strip()
                    if name in known:
                        sensors["temps"].append({"driver": name, "type": known[name]})
                    elif any((d / f"temp{i}_input").exists() for i in range(1, 4)):
                        sensors["temps"].append({"driver": name, "type": "other"})
                except Exception:
                    pass
    drm = Path("/sys/class/drm")
    if drm.exists():
        for card in sorted(drm.iterdir()):
            if (card / "device" / "gpu_busy_percent").exists():
                sensors["gpu_util"] = True
                break
    if shutil.which("nvidia-smi"):
        sensors["nvidia"] = True
    return sensors

def detect_gpu():
    """Detect GPU make, model, and VRAM. Returns dict or None."""
    # AMD via /sys/class/drm
    drm = Path("/sys/class/drm")
    if drm.exists():
        for card in sorted(drm.iterdir()):
            vendor_file = card / "device" / "vendor"
            product_file = card / "device" / "product"
            vram_file = card / "device" / "mem_info_vram_total"
            if not vendor_file.exists():
                continue
            try:
                vendor = vendor_file.read_text().strip()
            except Exception:
                continue
            if vendor != "0x1002":  # AMD PCI vendor ID
                continue
            name = "AMD GPU"
            # Try to get a friendly name from hwmon
            hwmon = Path("/sys/class/hwmon")
            if hwmon.exists():
                for d in hwmon.iterdir():
                    nf = d / "name"
                    if nf.exists() and nf.read_text().strip() == "amdgpu":
                        # Try device label from uevent
                        uevent = d / "device" / "uevent"
                        if uevent.exists():
                            for line in uevent.read_text().splitlines():
                                if line.startswith("PCI_ID="):
                                    name = f"AMD GPU ({line.split('=')[1]})"
                        break
            # Try rocm-smi for a friendlier name
            if shutil.which("rocm-smi"):
                try:
                    out = subprocess.run(
                        ["rocm-smi", "--showproductname", "--csv"],
                        capture_output=True, text=True, timeout=5,
                    )
                    for line in out.stdout.splitlines():
                        if "," in line and not line.startswith("GPU"):
                            parts = line.split(",")
                            if len(parts) >= 2:
                                candidate = parts[1].strip()
                                if candidate:
                                    name = candidate
                                    break
                except Exception:
                    pass
            vram_mb = None
            if vram_file.exists():
                try:
                    vram_bytes = int(vram_file.read_text().strip())
                    vram_mb = vram_bytes // (1024 * 1024)
                except Exception:
                    pass
            return {"vendor": "AMD", "name": name, "vram_mb": vram_mb}

    # NVIDIA via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            line = out.stdout.strip().split("\n")[0] if out.stdout.strip() else ""
            if line:
                parts = line.split(",")
                gpu_name = parts[0].strip()
                vram_mb = None
                if len(parts) >= 2:
                    try:
                        vram_mb = int(parts[1].strip())
                    except ValueError:
                        pass
                return {"vendor": "NVIDIA", "name": gpu_name, "vram_mb": vram_mb}
        except Exception:
            pass

    return None


def scan_services():
    """Detect LLM-related systemd services and Docker containers."""
    found = []
    keywords = ["llama", "ollama", "vllm"]
    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--all", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=5)
        for line in out.stdout.split("\n"):
            for kw in keywords:
                if kw in line.lower():
                    parts = line.split()
                    if parts:
                        found.append({"name": parts[0].replace(".service", ""), "type": "user", "unit": parts[0]})
                    break
    except Exception:
        pass
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=5)
        for name in out.stdout.strip().split("\n"):
            if name:
                found.append({"name": name, "type": "docker", "container": name})
    except Exception:
        pass
    return found

def run_init(guided=False):
    """Run full auto-detection, write config, print summary."""
    print("llamawatch init — scanning your system...\n")
    backends = scan_backends()
    sensors = scan_sensors()
    services = scan_services()
    gpu = detect_gpu()

    config = {}
    found_lines = []

    # Backends
    if backends:
        config["backends"] = backends
        for b in backends:
            if b["type"] == "llamacpp":
                model = b.get("detected_model", "")
                label = "llama.cpp"
                host = b["url"].replace("http://", "")
                extra = f" (model: {model})" if model and model != "unknown" else ""
                found_lines.append(f"  \u2713 {label} on {host}{extra}")
            elif b["type"] == "ollama":
                n = b.get("detected_models", 0)
                host = b["url"].replace("http://", "")
                found_lines.append(f"  \u2713 Ollama on {host} ({n} model{'s' if n != 1 else ''} available)")
            else:
                host = b["url"].replace("http://", "")
                n = b.get("detected_models", "")
                extra = f" ({n} models)" if n else ""
                found_lines.append(f"  \u2713 OpenAI-compatible on {host}{extra}")
    elif guided:
        url = input("\n? Enter backend URL manually: ").strip()
        btype = input("? Backend type [llamacpp/ollama/openai]: ").strip() or "openai"
        config["backends"] = [{"type": btype, "url": url}]

    # GPU
    if gpu:
        config["gpu"] = gpu
        vram_str = ""
        if gpu.get("vram_mb"):
            mb = gpu["vram_mb"]
            vram_str = f" ({round(mb / 1024, 1)}GB VRAM)" if mb >= 1024 else f" ({mb}MB VRAM)"
        found_lines.append(f"  \u2713 {gpu['name']}{vram_str}")
    elif sensors.get("nvidia"):
        found_lines.append("  \u2713 NVIDIA GPU (nvidia-smi available)")

    # Services — split Docker vs systemd for cleaner output
    if services:
        config["services"] = services
        docker = [s for s in services if s["type"] == "docker"]
        systemd = [s for s in services if s["type"] == "user"]
        if docker:
            found_lines.append(f"  \u2713 {len(docker)} Docker container{'s' if len(docker) != 1 else ''} running")
        if systemd:
            found_lines.append(f"  \u2713 {len(systemd)} LLM-related systemd service{'s' if len(systemd) != 1 else ''}")

    if found_lines:
        print("Found:")
        for line in found_lines:
            print(line)
    else:
        print("  No LLM backends or services found on common ports.")

    config_dir = Path.home() / ".config" / "llamawatch"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.local.json"
    config_path.write_text(json.dumps(config, indent=2))

    print(f"\nConfig written to {config_path}")
    print("\nRun 'llamawatch' to start the dashboard.")
    return config
