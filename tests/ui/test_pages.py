import json

import pytest

EXPECT = {(390, 844): 1, (1366, 768): 1, (1920, 1080): 1, (1920, 950): 1,
          (2560, 1440): 1, (3440, 1440): 2, (3440, 1300): 2, (2560, 1080): 2, (5120, 1440): 3}


@pytest.mark.parametrize("w,h", list(EXPECT))
def test_pages_shown(page_at, w, h):
    page = page_at(w, h)
    assert page.get_attribute("#studio", "data-shown") == str(EXPECT[(w, h)])


def test_pages_for_is_pure(page_at):
    page = page_at(1920, 1080)
    assert page.evaluate("[LWLayout.pagesFor(2199,900), LWLayout.pagesFor(2200,1000), LWLayout.pagesFor(3599,1000), LWLayout.pagesFor(3600,1200), LWLayout.pagesFor(800,1200)]") == [1, 2, 2, 3, 1]


def _visible_views(page):
    return page.evaluate("""() => { const vp = document.getElementById('view-port').getBoundingClientRect();
      return [...document.querySelectorAll('#view-track > .view')].filter(v => {
        const r = v.getBoundingClientRect(); return r.width > 0 && r.left >= vp.left - 2 && r.right <= vp.right + 2; }).map(v => v.id); }""")


def test_three_pages_no_nav(page_at):
    page = page_at(5120, 1440)
    assert _visible_views(page) == ["view-0", "view-1", "view-2"]
    assert not page.is_visible("#view-nav")


def test_two_pages_step_one_at_a_time(page_at):
    page = page_at(3440, 1440)
    page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-0", "view-1"]
    page.click("#view-next"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-1", "view-2"]
    assert page.is_disabled("#view-next")
    page.click(".view-dot[data-view='0']"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-0", "view-1"]


def test_one_page_arrows_keys_swipe(page_at):
    page = page_at(390, 844, touch=True)
    page.click(".view-dot[data-view='0']"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-0"]
    page.click("#view-next"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-1"]
    page.evaluate("""() => { const vp = document.getElementById('view-port');
      const t = (x) => new Touch({identifier: 1, target: vp, clientX: x, clientY: 300});
      vp.dispatchEvent(new TouchEvent('touchstart', {touches: [t(300)], changedTouches: [t(300)], bubbles: true}));
      vp.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [t(100)], bubbles: true})); }""")
    page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-2"]


def test_last_page_remembered(page_at):
    page = page_at(1920, 1080)
    page.click(".view-dot[data-view='1']"); page.wait_for_timeout(450)
    page.reload(); page.wait_for_selector("#g-cpu svg"); page.wait_for_timeout(450)
    assert _visible_views(page) == ["view-1"]


def test_knowledge_collapse_only_on_one_page(page_at):
    page = page_at(5120, 1440)
    assert "knowledge-active" not in page.get_attribute("#studio", "class")


def test_hidden_page_leaves_no_gap(page_at, server):
    # Review Focus 2: knowledge hidden in settings, 3-page screen shows the 2 that remain, full width
    page = page_at(5120, 1440, path="/static/manifest.json")
    def _settings(route):
        cfg = json.loads(route.fetch().text()); cfg["studio_panels"] = {"views": ["command", "system"]}
        route.fulfill(json=cfg)
    page.route("**/api/settings", _settings)
    page.goto(server + "/studio"); page.wait_for_selector("#g-cpu svg"); page.wait_for_timeout(600)
    assert page.get_attribute("#studio", "data-shown") == "2"
    assert _visible_views(page) == ["view-0", "view-1"]
    w = page.evaluate("document.getElementById('view-1').getBoundingClientRect().right")
    vp = page.evaluate("document.getElementById('view-port').getBoundingClientRect().right")
    assert abs(w - vp) < 2


def test_resize_keeps_page(page_at):
    # Review Focus 3: ultrawide on System -> 24 inch keeps System aligned
    page = page_at(3440, 1440)
    page.click(".view-dot[data-view='1']"); page.wait_for_timeout(450)
    page.set_viewport_size({"width": 1920, "height": 1080})
    # Wait for the slide to settle rather than a fixed time: the page count and
    # the side menu change a moment apart, and on a busy machine 700ms was
    # sometimes not enough. Settled = System alone, filling the view port.
    try:
        page.wait_for_function("""() => { const vp = document.getElementById('view-port').getBoundingClientRect();
          const v = document.getElementById('view-1').getBoundingClientRect();
          return document.getElementById('studio').dataset.shown === '1'
            && Math.abs(v.left - vp.left) < 2 && Math.abs(v.width - vp.width) < 2; }""", timeout=5000)
    except Exception:
        pass
    assert page.get_attribute("#studio", "data-shown") == "1"
    assert _visible_views(page) == ["view-1"]
