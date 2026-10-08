"""Command page on bigger screens: chat docked under the ring, readable agents, shorter network graph."""
import pytest

from .conftest import VIEWPORTS

BIG = [v for v in VIEWPORTS if v[2] >= 900 and v[1] > 1024]
SMALL = [v for v in VIEWPORTS if v not in BIG]


def _command(page):
    if page.is_visible("#view-nav"):
        page.click(".view-dot[data-view='0']")
    page.wait_for_timeout(600)


@pytest.mark.parametrize("name,w,h", BIG)
def test_chat_docked_under_ring(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    page.wait_for_selector("#chat-dock .chat-input", state="visible", timeout=8000)
    ring = page.locator("#hero-svg").bounding_box()
    dock = page.locator("#chat-dock").bounding_box()
    assert dock["y"] >= ring["y"] + ring["height"] - 2, f"{name}: chat overlaps the ring"
    assert dock["height"] >= 200, f"{name}: chat dock only {dock['height']:.0f}px tall"


@pytest.mark.parametrize("name,w,h", SMALL)
def test_chat_not_docked_on_small_screens(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    assert not page.is_visible("#chat-dock")


def test_chat_button_focuses_docked_chat(page_at):
    page = page_at(2560, 1440)
    page.wait_for_selector("#chat-dock .chat-input", state="visible", timeout=8000)
    page.click("#chat-new-btn")
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement === document.querySelector('#chat-dock .chat-input')")
    assert page.locator(".swm-window").count() == 0, "a floating chat window opened as well"


@pytest.mark.parametrize("name,w,h", BIG)
def test_agent_rows_are_readable(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    page.wait_for_selector("#agents-list .agent-row", timeout=8000)
    size, height = page.evaluate("""() => {
      const n = document.querySelector('#agents-list .agent-name');
      return [parseFloat(getComputedStyle(n).fontSize), n.closest('.agent-row').getBoundingClientRect().height];
    }""")
    assert size >= 14, f"{name}: agent names {size}px"
    assert height >= 40, f"{name}: agent rows {height:.0f}px tall"


@pytest.mark.parametrize("name,w,h", BIG)
def test_network_graph_is_not_the_tallest_thing(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    share = page.evaluate("""() => document.getElementById('net-section').getBoundingClientRect().height
                                  / document.getElementById('right-col').getBoundingClientRect().height""")
    assert share <= 0.40, f"{name}: network graph takes {share:.0%} of the column"


@pytest.mark.parametrize("name,w,h", BIG)
def test_chat_and_terminal_buttons_are_obvious(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    for sel, word in (("#chat-new-btn", "CHAT"), ("#term-new-btn", "TERMINAL")):
        box = page.locator(sel).bounding_box()
        assert box["height"] >= 32, f"{name}: {sel} only {box['height']:.0f}px tall"
        assert word in page.locator(sel).inner_text().upper(), f"{name}: {sel} has no visible label"


@pytest.mark.parametrize("name,w,h", BIG)
def test_chat_input_shows_three_lines(page_at, name, w, h):
    page = page_at(w, h)
    _command(page)
    page.wait_for_selector("#chat-dock .chat-input", state="visible", timeout=8000)
    lines = page.evaluate("""() => { const t = document.querySelector('#chat-dock .chat-input');
      return t.clientHeight / parseFloat(getComputedStyle(t).lineHeight); }""")
    assert lines >= 2.8, f"{name}: chat box shows {lines:.1f} lines"


# A stand-in chat socket: never opens, fires "close" a while after close() is called,
# and remembers which sockets the page closed itself.
_FAKE_WS = """(() => {
  const Real = window.WebSocket;
  window.__chatSocks = [];
  window.WebSocket = function (url, p) {
    if (!String(url).includes('/ws/chat/')) return new Real(url, p);
    const t = new EventTarget();
    t.url = url; t.readyState = 0; t.closedByPage = false;
    t.send = () => {};
    t.close = () => { if (t.closedByPage) return; t.closedByPage = true; t.readyState = 3;
      setTimeout(() => t.dispatchEvent(Object.assign(new Event('close'), {code: 1000})), 400); };
    window.__chatSocks.push(t);
    return t;
  };
  window.WebSocket.OPEN = 1; window.WebSocket.CLOSED = 3;
})()"""

_LIVE_SOCKS = "() => window.__chatSocks.filter(s => !s.closedByPage).length"


def test_floating_chat_moves_into_dock_when_window_grows(page_at):
    page = page_at(1366, 768)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.click("#chat-new-btn")
    page.wait_for_selector(".swm-window .chat-input", timeout=8000)
    page.set_viewport_size({"width": 2560, "height": 1440})
    page.wait_for_selector("#chat-dock .chat-input", state="visible", timeout=8000)
    assert not errors, errors
    assert page.locator(".swm-window").count() == 0


def test_dock_not_built_after_window_shrinks_mid_load(page_at):
    page = page_at(1366, 768)
    page.route("**/api/models", lambda r: (page.wait_for_timeout(600), r.continue_()))
    page.set_viewport_size({"width": 2560, "height": 1440})
    page.wait_for_timeout(100)
    page.set_viewport_size({"width": 1366, "height": 768})
    page.wait_for_timeout(1500)
    assert page.evaluate("document.getElementById('chat-dock').childElementCount") == 0


def test_no_orphan_chat_socket_after_crossing_breakpoint(page_at):
    page = page_at(1366, 768, init=_FAKE_WS)
    page.click("#chat-new-btn")
    page.wait_for_selector(".swm-window .chat-input", timeout=8000)
    page.set_viewport_size({"width": 2560, "height": 1440})
    page.wait_for_selector("#chat-dock .chat-input", state="visible", timeout=8000)
    page.wait_for_timeout(900)   # the old socket's late close event lands
    page.set_viewport_size({"width": 1366, "height": 768})
    page.wait_for_timeout(300)
    assert page.evaluate(_LIVE_SOCKS) == 0, "a chat socket was left open with nothing pointing at it"
