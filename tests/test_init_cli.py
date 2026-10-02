"""Tests for init CLI and importable scan functions."""

import subprocess
import sys


def test_init_help():
    result = subprocess.run(
        [sys.executable, "-m", "llamawatch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "llamawatch" in result.stdout.lower()


def test_scan_backends_importable():
    from llamawatch.auto_detect import scan_backends
    assert callable(scan_backends)


def test_scan_services_importable():
    from llamawatch.auto_detect import scan_services
    assert callable(scan_services)


def test_detect_gpu_importable():
    from llamawatch.auto_detect import detect_gpu
    assert callable(detect_gpu)


def test_scan_backends_returns_list():
    from llamawatch.auto_detect import scan_backends
    result = scan_backends()
    assert isinstance(result, list)


def test_scan_services_returns_list():
    from llamawatch.auto_detect import scan_services
    result = scan_services()
    assert isinstance(result, list)


def test_detect_gpu_returns_dict_or_none():
    from llamawatch.auto_detect import detect_gpu
    result = detect_gpu()
    assert result is None or isinstance(result, dict)
    if result is not None:
        assert "vendor" in result
        assert "name" in result


def test_init_subcommand_runs():
    """init subcommand should run without crashing (writes config, prints summary)."""
    result = subprocess.run(
        [sys.executable, "-m", "llamawatch", "init"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "llamawatch init" in result.stdout
    assert "Config written to" in result.stdout
    assert "llamawatch" in result.stdout
