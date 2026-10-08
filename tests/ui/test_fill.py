"""No big empty black areas: every visible panel column is filled to its bottom edge."""
import pytest

from .conftest import VIEWPORTS

MAX_GAP = 40  # px of dead space allowed under the last section of a column

_GAPS = """() => {
  const out = [];
  for (const col of document.querySelectorAll('[data-panel]')) {
    const r = col.getBoundingClientRect();
    if (r.width < 50 || r.height < 200 || r.right < 0 || r.left > innerWidth) continue;
    if (getComputedStyle(col).flexDirection !== 'column') continue;
    let bottom = r.top;
    for (const k of col.children) {
      const s = getComputedStyle(k);
      if (s.display === 'none' || s.visibility === 'hidden' || s.position === 'absolute') continue;
      const kr = k.getBoundingClientRect();
      if (kr.height > 0) bottom = Math.max(bottom, kr.bottom);
    }
    const gap = Math.round(Math.min(r.bottom, innerHeight) - bottom);
    out.push([col.dataset.panel, gap]);
  }
  return out;
}"""


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_columns_have_no_dead_space(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='0']")
    page.wait_for_timeout(600)
    gaps = page.evaluate(_GAPS)
    assert gaps, "no panel columns found"
    bad = [g for g in gaps if g[1] > MAX_GAP]
    assert bad == [], f"{name}: empty space under columns (panel, px): {bad}"


_NET_TOP = """() => {
  const c = document.getElementById('net-rc-canvas');
  const ctx = c.getContext('2d');
  const x = Math.floor(c.width / 2);
  const col = ctx.getImageData(x, 0, 1, c.height).data;
  for (let y = 0; y < c.height; y++) if (col[y * 4 + 3] > 200) return [y / c.height, document.getElementById('net-scale')?.textContent || ''];
  return [1, ''];
}"""


def test_network_graph_has_headroom_and_scale(page_at):
    page = page_at(2560, 1440)
    page.wait_for_function("getComputedStyle(document.getElementById('net-idle-label')).display === 'none'"
                           " && document.getElementById('net-rc-canvas').width > 0", timeout=20000)
    top, scale = page.evaluate(_NET_TOP)
    assert top >= 0.3, f"steady traffic fills {1 - top:.0%} of the graph, reads as a solid block"
    assert "Mbps" in scale, f"no scale label on the network graph: {scale!r}"


def test_history_draws_a_line_for_every_card(page_at):
    page = page_at(3440, 1440)
    page.wait_for_function("document.querySelectorAll('#wh-svg polyline').length > 0", timeout=15000)
    cards = page.locator("#wh-tiles > *").count()
    assert page.locator("#wh-svg polyline").count() == cards == 4


def test_prediction_list_fills_column(page_at):
    page = page_at(5120, 1440)
    page.wait_for_function("document.querySelectorAll('#wide-preds .wide-pred-row').length > 0", timeout=15000)
    page.wait_for_timeout(2500)  # map tab auto-opens after 1.5s
    gap = page.evaluate("""() => {
      const col = document.getElementById('intel-predictions').getBoundingClientRect();
      const rows = document.querySelectorAll('#wide-preds .wide-pred-row');
      return Math.round(col.bottom - rows[rows.length - 1].getBoundingClientRect().bottom);
    }""")
    assert gap <= MAX_GAP, f"{gap}px blank under the prediction list"


def test_press_room_fills_column(page_at):
    page = page_at(5120, 1440)
    page.wait_for_function("document.querySelectorAll('#pressroom-feed > *').length > 5", timeout=15000)
    page.wait_for_timeout(600)
    gap = page.evaluate("""() => {
      const col = document.getElementById('intel-pressroom').getBoundingClientRect();
      const items = document.getElementById('pressroom-feed').children;
      return Math.round(col.bottom - Math.min(col.bottom, items[items.length - 1].getBoundingClientRect().bottom));
    }""")
    assert gap <= MAX_GAP, f"{gap}px blank under the press room list"


def test_history_scale_fits_the_data(page_at):
    """Lines should use most of the chart height, not sit in the bottom half."""
    page = page_at(2560, 1440)
    page.click(".view-dot[data-view='1']")
    page.wait_for_function("document.querySelectorAll('#wh-svg polyline').length > 0", timeout=15000)
    used = page.evaluate("""() => {
      const svg = document.getElementById('wh-svg').getBoundingClientRect();
      let top = Infinity, bot = -Infinity;
      for (const p of document.querySelectorAll('#wh-svg polyline')) {
        const r = p.getBoundingClientRect(); top = Math.min(top, r.top); bot = Math.max(bot, r.bottom);
      }
      return (bot - top) / svg.height;
    }""")
    assert used >= 0.6, f"lines use only {used:.0%} of the chart height"


def test_history_shows_hot_line_when_near_limit(page_at):
    page = page_at(2560, 1440)

    def hot(route):
        d = route.fetch().json()
        d["summary"]["cpu"]["max"] = 88
        route.fulfill(json=d)
    page.route("**/api/history*", hot)
    page.reload()
    page.click(".view-dot[data-view='1']")
    page.wait_for_function("document.querySelectorAll('#wh-svg polyline').length > 0", timeout=15000)
    assert page.locator("#wh-svg line[stroke-dasharray]").count() == 1
    assert "dashed" in page.text_content("#wh-note")


@pytest.mark.parametrize("name,w,h", VIEWPORTS)
def test_predictions_load_without_a_click(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='2']")
    page.wait_for_function("document.querySelectorAll('#predictions-feed .know-empty-state').length === 0", timeout=8000)


@pytest.mark.parametrize("name,w,h", [v for v in VIEWPORTS if v[0] != "phone"])
def test_predictions_column_has_no_dead_space(page_at, name, w, h):
    page = page_at(w, h)
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='2']")
    page.wait_for_selector(".pred-worldmap-wrap svg", timeout=10000)
    page.wait_for_timeout(1000)
    gap = page.evaluate("""() => {
      const col = document.getElementById('intel-predictions').getBoundingClientRect();
      let bottom = col.top;
      for (const el of document.querySelectorAll('#intel-predictions *')) {
        const s = getComputedStyle(el); if (s.display === 'none') continue;
        const r = el.getBoundingClientRect();
        if (r.height > 0 && r.width > 0 && el.children.length === 0 && el.textContent.trim()) bottom = Math.max(bottom, Math.min(r.bottom, col.bottom));
      }
      return Math.round(col.bottom - bottom);
    }""")
    assert gap <= MAX_GAP, f"{name}: {gap}px blank at the bottom of the predictions column"
