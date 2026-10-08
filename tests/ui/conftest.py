"""Browser test harness: a sample-mode llamawatch on a free port + headless Chromium."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SNAP = Path(__file__).parent / "fixtures" / "snapshot.json"
VIEWPORTS = [("phone", 390, 844), ("laptop", 1366, 768), ("fhd", 1920, 1080),
             ("qhd", 2560, 1440), ("uw", 3440, 1440), ("suw", 5120, 1440)]
FROZEN = "2026-01-01T12:00:00Z"


def pytest_collection_modifyitems(items):
    for it in items:
        if "tests/ui/" in str(it.fspath).replace(os.sep, "/"):
            it.add_marker(pytest.mark.ui)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "llamawatch", "--sample", str(SNAP), "--port", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("sample server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


_BLOCK_STORAGE = """
Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } });
"""


# Same "random" sequence every run, so particle effects draw identically in screenshots.
_SEED_RANDOM = """
(() => { let a = 0x2545F491; Math.random = () => { a |= 0; a = a + 0x6D2B79F5 | 0;
  let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
  return ((t ^ t >>> 14) >>> 0) / 4294967296; }; })();
"""


_LAYOUT_SETTLED = """() => {
  const vp = document.getElementById('view-port'), v = document.querySelector('#view-track > .view:not(.view-off)');
  const n = Math.min(window.LWLayout.pages(), document.querySelectorAll('#view-track > .view:not(.view-off)').length);
  return vp && v && Math.abs(v.getBoundingClientRect().width * n - vp.getBoundingClientRect().width) < 4;
}"""


@pytest.fixture
def page_at(browser, server):
    contexts = []

    def _open(w, h, touch=False, storage_blocked=False, path="/studio", init=None):
        ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=touch,
                                  is_mobile=touch and w < 700, reduced_motion="reduce",
                                  service_workers="block")
        contexts.append(ctx)
        ctx.add_init_script(_SEED_RANDOM)
        if storage_blocked:
            ctx.add_init_script(_BLOCK_STORAGE)
        if init:
            ctx.add_init_script(init)
        page = ctx.new_page()
        page.clock.install(time=FROZEN)
        page.goto(server + path)
        if not storage_blocked and path == "/studio":
            page.wait_for_selector("#g-cpu svg", timeout=15000)
            # Page widths can lag a moment behind the page count on first load.
            page.wait_for_function(_LAYOUT_SETTLED, timeout=5000)
        return page

    yield _open
    for c in contexts:
        c.close()
