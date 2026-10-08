import os
import shutil
from pathlib import Path

import pytest

from .conftest import VIEWPORTS
from .helpers import diff_ratio

HERE = Path(__file__).parent
REF, OUT = HERE / "reference", HERE / "out"


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
def test_look(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='0']")
    page.wait_for_function("document.getElementById('bs-node-b-cpu')?.textContent.includes('%')", timeout=15000)
    page.mouse.move(w - 5, h // 2)
    page.wait_for_timeout(2500)  # rail closed, sparks drawn
    shot = OUT / f"{name}.png"
    OUT.mkdir(exist_ok=True)
    # The hero particles and the clock move; data lands at slightly different moments.
    # Mask the particles and wait until two frames in a row match.
    mask = [page.locator("#hero-neural"), page.locator("#top-clock")]
    prev = OUT / f"{name}.prev.png"
    for _ in range(10):
        page.screenshot(path=str(prev), animations="disabled", caret="hide", mask=mask)
        page.wait_for_timeout(700)
        page.screenshot(path=str(shot), animations="disabled", caret="hide", mask=mask)
        if diff_ratio(prev, shot, OUT / f"{name}.settle.png") == 0:
            break
    ref = REF / f"{name}.png"
    if os.environ.get("LW_UPDATE_REFS") == "1" or not ref.exists():
        REF.mkdir(exist_ok=True)
        shutil.copy(shot, ref)
        pytest.skip(f"reference written: {ref.name} (check it by eye before keeping it)")
    ratio = diff_ratio(ref, shot, OUT / f"{name}.diff.png")
    assert ratio <= 0.005, f"{name}: {ratio:.2%} of pixels differ, see {OUT / (name + '.diff.png')}"
