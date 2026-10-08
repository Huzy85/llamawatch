"""Agents page in sample mode: now cards with a ticking timer, run history with
chips and search, replay of a run, the rail entry on every page, phone layout,
look references."""
import os
import shutil
from pathlib import Path

import pytest

from .helpers import diff_ratio

HERE = Path(__file__).parent
REF, OUT = HERE / "reference", HERE / "out"


def _ag(page_at, w, h, touch=False):
    page = page_at(w, h, touch=touch, path="/agents")
    page.wait_for_selector("#ag-runs-body tr", timeout=15000)
    return page


def flat(loc):
    return " ".join(loc.inner_text().split())


def test_now_cards(page_at):
    page = _ag(page_at, 1920, 1080)
    cards = page.locator(".ag-card")
    assert cards.count() == 5
    assert page.inner_text("#ag-now-hint") == "3 of 5 active"
    first = cards.first
    assert flat(first.locator(".ag-card-name")) == "Claude Code"
    assert flat(first.locator(".ag-chip")) == "demo-shop"
    assert flat(first.locator(".ag-card-line")) == "Bash: run the unit tests"
    assert first.locator(".ag-dot.working").count() == 1
    assert first.locator(".ag-timer").inner_text() == "0:42"
    assert cards.nth(1).locator(".ag-timer").inner_text() == "3:10"
    assert cards.nth(2).locator(".ag-dot.idle").count() == 1
    svc = cards.nth(3)
    assert flat(svc.locator(".ag-card-name")) == "Helper" and flat(svc.locator(".ag-chip")) == "box-2"
    assert flat(svc.locator(".ag-card-line")) == "Up 3 hours" and svc.locator(".ag-timer").count() == 0
    assert cards.nth(4).locator(".ag-dot.offline").count() == 1
    assert page.get_attribute("#rail-item-agents", "aria-current") == "page"
    assert page.inner_text("#ag-updated") == "5 live · 9 runs"


def _secs(txt):
    parts = [int(x) for x in txt.split(":")]
    return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]


def test_timer_ticks(page_at):
    page = _ag(page_at, 1920, 1080)
    assert page.inner_text(".ag-card:first-child .ag-timer") == "0:42"
    page.clock.run_for(5500)                       # the fake clock's ticks run about a second behind
    assert 46 <= _secs(page.inner_text(".ag-card:first-child .ag-timer")) <= 48
    page.clock.run_for(3000)                       # stays under the 10 s poll, which re-bases on the fixture's frozen "now"
    assert 49 <= _secs(page.inner_text(".ag-card:first-child .ag-timer")) <= 51
    assert _secs(page.inner_text(".ag-card:nth-child(2) .ag-timer")) >= 190 + 7


def test_history_table_chips_and_search(page_at):
    page = _ag(page_at, 1920, 1080)
    rows = page.locator("#ag-runs-body tr")
    assert rows.count() == 9
    first = rows.first
    assert flat(first.locator(".ag-agent")) == "Claude Code"
    assert flat(first.locator(".ag-title")) == "Add a retry to the photo sync job"
    assert flat(first.locator(".ag-project")) == "demo-shop"
    assert flat(first.locator(".ag-started")) == "11:38"                  # same day as the frozen clock
    cells = [flat(first.locator("td").nth(i)) for i in range(8)]
    assert cells[4] == "22m" and cells[5] == "38" and cells[6] == "412k" and cells[7] == "working"
    assert "Dec 31" in flat(rows.nth(5).locator(".ag-started"))
    assert flat(rows.nth(6).locator(".ag-state")) == "failed"
    chips = page.locator("#ag-chips button")
    assert [chips.nth(i).inner_text() for i in range(chips.count())] == ["All", "Claude Code", "Codex", "Helper", "Research", "Scraper"]
    page.click("#ag-chips button[data-filter='Research']")
    assert rows.count() == 3 and all(flat(rows.nth(i).locator(".ag-agent")) == "Research" for i in range(3))
    assert page.get_attribute("#ag-chips button[data-filter='Research']", "aria-pressed") == "true"
    page.click("#ag-chips button[data-filter='Helper']")
    assert rows.count() == 0 and page.is_visible("#ag-history-empty")
    assert page.inner_text("#ag-history-empty") == "No runs match."
    page.click("#ag-chips button[data-filter='all']")
    page.fill("#ag-search", "image")
    assert rows.count() == 2 and all("image-tools" in flat(rows.nth(i)) for i in range(2))
    page.fill("#ag-search", "")
    assert rows.count() == 9
    page.reload()
    page.wait_for_selector("#ag-runs-body tr", timeout=15000)
    assert page.get_attribute("#ag-chips button[data-filter='all']", "aria-pressed") == "true"


def test_replay_opens_and_closes(page_at):
    page = _ag(page_at, 1920, 1080)
    assert not page.is_visible("#ag-replay")
    page.click("#ag-runs-body tr:first-child .ag-title button")
    page.wait_for_selector("#ag-replay:not([hidden])", timeout=5000)
    assert page.inner_text("#ag-replay-h") == "Replay · Add a retry to the photo sync job"
    assert flat(page.locator("#ag-replay-sub")) == "Claude Code · demo-shop · working"
    totals = flat(page.locator("#ag-totals"))
    assert "Length 22m" in totals and "Steps 38" in totals and "Tokens 412k" in totals
    assert "In 502 · out 1.0k · cached 31k" in totals and "Thinking blocks 4" in totals
    steps = page.locator("#ag-steps .ag-step")
    assert steps.count() == 9
    assert flat(steps.first) == "0:00 4s Prompt Add a retry to the photo sync job, three tries with a short wait"
    assert flat(steps.nth(2)) == "0:06 3s Read jobs.py 72"
    assert flat(steps.nth(5)) == "0:55 28s Bash run the unit tests 110"
    assert flat(steps.last).startswith("1:53 20m Bash run the unit tests")
    assert steps.nth(1).get_attribute("class") == "ag-step text" and steps.first.get_attribute("class") == "ag-step prompt"
    assert page.get_attribute("#ag-runs-body tr:first-child", "aria-selected") == "true"
    page.keyboard.press("Escape")
    assert not page.is_visible("#ag-replay")
    assert page.get_attribute("#ag-runs-body tr:first-child", "aria-selected") == "false"
    page.click("#ag-chips button[data-filter='Research']")
    page.click("#ag-runs-body tr:nth-child(2) .ag-title button")
    page.wait_for_selector("#ag-replay:not([hidden])", timeout=5000)
    assert page.inner_text("#ag-replay-h") == "Replay · Heat pump running cost versus gas for a 1930s semi"
    assert "Cost 0.31" in flat(page.locator("#ag-totals"))
    st = page.locator("#ag-steps .ag-step")
    assert st.count() == 5 and flat(st.nth(2)) == "0:43 5m read reading 9 pages 6 calls · 31k"
    page.click("#ag-replay-close")
    assert not page.is_visible("#ag-replay")


def test_replay_of_unknown_run_shows_error(page_at):
    page = _ag(page_at, 1920, 1080)
    page.click("#ag-runs-body tr:nth-child(4) .ag-title button")        # the Codex run has no replay in the fixture
    page.wait_for_function("document.getElementById('ag-err').textContent.length > 0", timeout=5000)
    assert page.inner_text("#ag-err").strip() != ""
    assert not page.is_visible("#ag-replay")


def test_rail_entry_everywhere(page_at):
    for path in ("/studio", "/research", "/jobs", "/approvals", "/spend"):
        page = page_at(1920, 1080, path=path)
        page.wait_for_selector("#rail-item-agents", state="attached", timeout=15000)
        assert page.get_attribute("#rail-item-agents", "aria-current") in (None, "false")
    page = _ag(page_at, 1920, 1080)
    page.hover("#rail")
    page.click("#rail-item-spend")
    page.wait_for_url("**/spend")


def test_phone_layout(page_at):
    page = _ag(page_at, 390, 844, touch=True)
    assert page.get_attribute("html", "data-rail") == "bottom"
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    items = page.locator("#rail-items .rail-item")
    assert items.count() == 6          # settings only exists on the Monitor page
    last = items.last.bounding_box()
    assert last["x"] + last["width"] <= 390 and last["width"] >= 50
    assert not page.is_visible("#ag-runs-body tr:first-child .ag-project")
    assert not page.is_visible("#ag-runs thead .ag-steps-col")
    cards = page.locator(".ag-card")
    a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
    assert b["y"] > a["y"] + a["height"] - 1       # one card per row
    page.tap("#ag-runs-body tr:first-child .ag-title button")
    page.wait_for_selector("#ag-replay:not([hidden])", timeout=5000)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not page.is_visible("#ag-steps .ag-step:first-child .ag-step-dur")


def test_seven_tabs_fit_on_the_monitor_phone_bar(page_at):
    page = page_at(390, 844, touch=True, path="/studio")
    page.wait_for_selector("#rail-item-settings", state="attached", timeout=15000)
    items = page.locator("#rail-items .rail-item")
    assert items.count() == 7
    assert page.evaluate("document.getElementById('rail').scrollWidth") <= 390
    boxes = [items.nth(i).bounding_box() for i in range(7)]
    assert all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in boxes)
    assert boxes[-1]["x"] + boxes[-1]["width"] > 300
    for i in range(7):
        assert items.nth(i).locator("svg").is_visible()


@pytest.mark.parametrize("name,w,h,touch", [("agents-phone", 390, 844, True), ("agents-fhd", 1920, 1080, False), ("agents-uw", 3440, 1440, False)])
def test_look(page_at, name, w, h, touch):
    page = _ag(page_at, w, h, touch=touch)
    page.click("#ag-runs-body tr:first-child .ag-title button")
    page.wait_for_selector("#ag-replay:not([hidden])", timeout=5000)
    page.evaluate("window.scrollTo(0, 0)")
    page.mouse.move(w - 5, h // 2)
    page.wait_for_timeout(1200)
    OUT.mkdir(exist_ok=True)
    shot = OUT / f"{name}.png"
    page.screenshot(path=str(shot), animations="disabled", caret="hide")
    ref = REF / f"{name}.png"
    if os.environ.get("LW_UPDATE_REFS") == "1" or not ref.exists():
        REF.mkdir(exist_ok=True)
        shutil.copy(shot, ref)
        pytest.skip(f"reference written: {ref.name} (check it by eye before keeping it)")
    ratio = diff_ratio(ref, shot, OUT / f"{name}.diff.png")
    assert ratio <= 0.005, f"{name}: {ratio:.2%} of pixels differ, see {OUT / (name + '.diff.png')}"
