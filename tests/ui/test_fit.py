import pytest

from .conftest import VIEWPORTS
from .helpers import probe

DIAL = {"phone": (56, 90), "laptop": (56, 80), "fhd": (88, 108),
        "qhd": (117, 143), "uw": (117, 143), "suw": (117, 143)}


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
def test_fit(page_at, name, w, h):
    page = page_at(w, h)
    page.click(".view-dot[data-view='0']") if page.is_visible("#view-nav") else None
    page.wait_for_timeout(450)
    m = probe(page)
    lo, hi = DIAL[name]
    assert lo <= m["dial"] <= hi, f"dial {m['dial']:.0f}px not in {lo}-{hi}"
    assert m["min_font"][0] >= 12, f"text below 12px: {m['min_font']}"
    assert not m["hscroll"], "page scrolls sideways"
    assert m["clipped"] == [], f"clipped text: {m['clipped'][:10]}"
    assert m["overlaps"] == [], f"overlapping text: {m['overlaps'][:10]}"
    assert m["offscreen"] == [], f"off-screen panels: {m['offscreen']}"


@pytest.mark.parametrize("view", [1, 2])
def test_fit_other_pages_fhd(page_at, view):
    page = page_at(1920, 1080)
    page.click(f".view-dot[data-view='{view}']"); page.wait_for_timeout(450)
    m = probe(page)
    assert m["min_font"][0] >= 12, m["min_font"]
    assert m["clipped"] == [] and m["overlaps"] == [] and not m["hscroll"], m


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_docker_chip_and_gpu_values_do_not_collide(page_at, name, w, h):
    page = page_at(w, h)
    page.wait_for_selector(".docker-row .dr-machine-chip", timeout=15000)
    page.wait_for_function("document.getElementById('ram-node-a-label')?.textContent.includes('/')", timeout=15000)
    r = page.evaluate("""() => {
        const c = document.querySelector('.docker-row .dr-machine-chip');
        const chip = {right: c.getBoundingClientRect().left + Math.max(c.scrollWidth, c.offsetWidth)};
        const nm = document.querySelector('.docker-row .dr-name').getBoundingClientRect();
        const v = document.getElementById('gpu-vram-val');
        const lh = parseFloat(getComputedStyle(v).lineHeight) || parseFloat(getComputedStyle(v).fontSize) * 1.4;
        const ram = [...document.querySelectorAll('.fleet-ram-val')].filter(e => e.offsetWidth)
            .filter(e => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().height > lh * 1.2).map(e => e.textContent);
        return {gap: nm.left - chip.right, vramH: v.getBoundingClientRect().height, lh, ram};
    }""")
    assert r["gap"] >= 0, f"{name}: machine chip runs {-r['gap']:.0f}px into the container name"
    assert r["vramH"] <= r["lh"] * 1.2, f"{name}: VRAM value wraps onto two lines"
    assert r["ram"] == [], f"{name}: memory values cut off: {r['ram']}"


_CUT_ROWS = """() => {
  const t = document.getElementById('docker-table');
  const r = t.getBoundingClientRect();
  if (!r.height) return [];
  return [...t.children].map(k => k.getBoundingClientRect())
    .filter(k => k.top < r.bottom - 1 && k.bottom > r.bottom + 1)
    .map(k => Math.round(r.bottom - k.top));
}"""


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_docker_list_shows_whole_rows(page_at, name, w, h):
    page = page_at(w, h)
    page.wait_for_function("document.querySelectorAll('#docker-table > *').length > 10", timeout=15000)
    page.wait_for_timeout(600)
    assert page.evaluate(_CUT_ROWS) == [], f"{name}: docker row cut in half at the list's bottom edge"


_PRED_ROWS = """() => {
  const t = document.getElementById('wide-preds');
  const r = t.getBoundingClientRect();
  const cut = [...t.querySelectorAll('.wide-pred-row')].map(k => k.getBoundingClientRect())
    .filter(k => k.top < r.bottom - 1 && k.bottom > r.bottom + 1).length;
  return [cut, getComputedStyle(t).overflowY, t.scrollHeight > t.clientHeight];
}"""


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_prediction_list_scrolls_and_shows_whole_rows(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='2']")
    page.wait_for_timeout(2500)
    if not page.locator("#wide-preds .wide-pred-row").count():
        pytest.skip("no wide prediction list at this size")
    cut, overflow, more = page.evaluate(_PRED_ROWS)
    assert cut == 0, f"{name}: prediction row cut in half at the bottom"
    if more:
        assert overflow in ("auto", "scroll"), f"{name}: more predictions than fit, but the list cannot scroll"


_FEED_CUT = """() => {
  const t = document.getElementById('pressroom-feed');
  const r = t.getBoundingClientRect();
  return [...t.children].map(k => k.getBoundingClientRect())
    .filter(k => k.top < r.bottom - 1 && k.bottom > r.bottom + 1).length;
}"""


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_press_room_shows_whole_rows(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='2']")
    page.wait_for_function("document.querySelectorAll('#pressroom-feed > *').length > 10", timeout=15000)
    page.wait_for_timeout(2500)
    assert page.evaluate(_FEED_CUT) == 0, f"{name}: press room row cut in half at the bottom"


def test_phone_network_row_not_squashed(page_at):
    page = page_at(390, 844)
    page.wait_for_timeout(1500)
    sh, ch = page.evaluate("(() => { const e = document.getElementById('net-section'); return [e.scrollHeight, e.clientHeight]; })()")
    assert sh <= ch + 1, f"network row squashed: content {sh}px in {ch}px"


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
def test_no_empty_quick_actions_strip(page_at, name, w, h):
    page = page_at(w, h)
    page.wait_for_timeout(1500)
    if page.locator("#quick-actions > *").count() == 0:
        assert not page.is_visible("#quick-actions"), f"{name}: empty quick-actions strip on show"


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
def test_map_has_no_blank_band(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='2']")
    page.wait_for_selector(".pred-worldmap-wrap svg", timeout=10000)
    page.wait_for_timeout(800)
    sw, sh = page.evaluate("(() => { const r = document.querySelector('.pred-worldmap-wrap svg').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert sh <= sw / 2 + 2, f"{name}: map box {sw:.0f}x{sh:.0f} leaves a blank band"


def test_prediction_list_refits_when_rows_change_height(page_at):
    # a late web font changes row heights without resizing the list; it must still end on a whole row
    page = page_at(1920, 1080)
    page.wait_for_selector("#wide-preds .wide-pred-row", timeout=8000)
    page.wait_for_timeout(800)
    page.add_style_tag(content=".wide-pred-row { font-size: 17px !important; line-height: 1.7 !important; }")
    page.wait_for_timeout(600)
    cut, _overflow, _more = page.evaluate(_PRED_ROWS)
    assert cut == 0, "prediction row cut in half after the rows changed height"


def test_prediction_refresh_does_not_keep_old_rows_alive(page_at):
    track = """(() => { const R = window.ResizeObserver; window.__obs = new Set();
      window.ResizeObserver = class extends R {
        observe(t, o) { window.__obs.add(t); return super.observe(t, o); }
        unobserve(t) { window.__obs.delete(t); return super.unobserve(t); }
        disconnect() { window.__obs.clear(); return super.disconnect(); } }; })()"""
    page = page_at(1920, 1080, init=track)
    page.wait_for_selector("#wide-preds .wide-pred-row", timeout=8000)
    for _ in range(3):
        page.clock.run_for(121000)
        page.wait_for_timeout(300)
    dead = page.evaluate("() => [...window.__obs].filter(t => !t.isConnected).length")
    assert dead == 0, f"{dead} removed rows still watched"
