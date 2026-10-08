"""Spend page in sample mode: the month dials, free local share, stacked bars by
provider and by job with the day tip, totals tables, the price strip, the rail
entry on every page, phone layout, look references."""
import os
import shutil
from pathlib import Path

import pytest

from .helpers import diff_ratio

HERE = Path(__file__).parent
REF, OUT = HERE / "reference", HERE / "out"


def _sp(page_at, w, h, touch=False):
    page = page_at(w, h, touch=touch, path="/spend")
    page.wait_for_selector("#sp-chart .sp-day", timeout=15000)
    return page


def flat(loc):
    return " ".join(loc.inner_text().split())


def test_month_cards(page_at):
    page = _sp(page_at, 1920, 1080)
    assert page.inner_text("#sp-money") == "$131"
    assert flat(page.locator("#sp-money-sub")) == "spent, plus £0.17"
    assert page.inner_text("#sp-month-label") == "January 2026"
    assert page.inner_text("#sp-tokens") == "5.35M"
    assert "requests" in page.inner_text("#sp-tokens-sub")
    assert page.inner_text("#sp-local") == "19.7M"
    assert flat(page.locator("#sp-share-text")).startswith("78% of all tokens this month ran locally")
    assert page.locator("#sp-share-fill").evaluate("e => e.style.width") == "78%"
    # money ring = 14/31 of the circle; tokens ring = paid share
    dash = page.locator("#sp-ring-money").evaluate("e => e.style.strokeDasharray").replace(",", " ").split()
    assert abs(float(dash[0]) / float(dash[1]) - 14 / 31) < 0.01
    assert "No price set for Codex" in page.inner_text("#sp-month-note")
    assert page.get_attribute("#rail-item-spend", "aria-current") == "page"


def test_chart_by_provider_then_by_job(page_at):
    page = _sp(page_at, 1920, 1080)
    assert page.locator("#sp-chart .sp-day").count() == 30
    legend = flat(page.locator("#sp-legend"))
    assert "local-main" in legend and "claude-opus-5-5" in legend and "Other" not in legend   # five providers fit
    assert page.locator("#sp-chart .sp-day").last.get_attribute("data-date") == "2026-01-14"
    page.hover("#sp-chart .sp-day:last-child")
    tip = page.locator("#sp-tip")
    assert tip.is_visible() and "14 Jan" in tip.inner_text() and "local-main" in tip.inner_text()
    page.click(".sp-seg button[data-by='job']")
    assert page.get_attribute(".sp-seg button[data-by='job']", "aria-pressed") == "true"
    legend = flat(page.locator("#sp-legend"))
    assert "Chat" in legend and "relay" in legend and "Other (" in legend          # more than six jobs fold
    page.hover("#sp-chart .sp-day:nth-child(4)")                                  # the research run day
    assert "battery chemistry" in page.inner_text("#sp-tip") and "£0.144" in page.inner_text("#sp-tip")
    page.reload()
    page.wait_for_selector("#sp-chart .sp-day", timeout=15000)
    assert page.get_attribute(".sp-seg button[data-by='job']", "aria-pressed") == "true"      # choice kept


def test_tables(page_at):
    page = _sp(page_at, 1920, 1080)
    rows = page.locator("#sp-sources tbody tr")
    assert rows.count() == 5
    first = flat(rows.nth(0))
    assert first.startswith("claude-opus-5-5 tool") and "$" in first
    assert flat(rows.nth(1)).startswith("Codex tool") and "no price" in flat(rows.nth(1))
    assert "example-api API" in flat(rows.nth(2)) and "£" in flat(rows.nth(2))
    assert flat(rows.nth(4)).endswith("free")
    jobs = page.locator("#sp-jobs tbody tr")
    assert jobs.count() == 11
    assert "Which battery chemistry suits a home store?" in flat(page.locator("#sp-jobs"))
    assert "Other on local-main" in flat(page.locator("#sp-jobs"))


def test_price_strip(page_at):
    page = _sp(page_at, 1920, 1080)
    rows = page.locator("#sp-price-table tbody tr")
    assert rows.count() == 3          # claude, Codex, example-api; local sources have no price row
    api_row = rows.filter(has_text="example-api")
    assert api_row.locator("input").first.is_disabled() and "set on Research page" in flat(api_row)
    assert api_row.locator("[data-save]").count() == 0
    codex = rows.filter(has_text="Codex")
    assert codex.locator("input[data-k='in']").input_value() == ""
    codex.locator("input[data-k='in']").fill("1.5")
    codex.locator("[data-save]").click()
    page.wait_for_selector("#sp-toast:not([hidden])", timeout=5000)
    assert "read-only" in page.inner_text("#sp-toast")    # sample mode refuses every write


def test_rail_entry_everywhere(page_at):
    for path in ("/studio", "/research", "/jobs", "/approvals"):
        page = page_at(1920, 1080, path=path)
        page.wait_for_selector("#rail-item-spend", state="attached", timeout=15000)
        assert page.get_attribute("#rail-item-spend", "aria-current") in (None, "false")
    page = _sp(page_at, 1920, 1080)
    page.hover("#rail")
    page.click("#rail-item-approvals")
    page.wait_for_url("**/approvals")


def test_phone_layout(page_at):
    page = _sp(page_at, 390, 844, touch=True)
    assert page.get_attribute("html", "data-rail") == "bottom"
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    items = page.locator("#rail-items .rail-item")
    assert items.count() == 6          # settings only exists on the Monitor page
    last = items.last.bounding_box()
    assert last["x"] + last["width"] <= 390 and last["width"] >= 50
    month = page.locator("#sp-month-card").bounding_box()
    chart = page.locator(".sp-chart-card").bounding_box()
    assert chart["y"] > month["y"] + month["height"] - 1   # stacked
    page.tap("#sp-chart .sp-day:last-child")
    assert page.is_visible("#sp-tip")
    page.tap("#sp-chart .sp-day:last-child")
    assert not page.is_visible("#sp-tip")
    page.click(".sp-seg button[data-by='job']")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_seven_tabs_fit_on_the_monitor_phone_bar(page_at):
    page = page_at(390, 844, touch=True, path="/studio")
    page.wait_for_selector("#rail-item-settings", state="attached", timeout=15000)
    items = page.locator("#rail-items .rail-item")
    assert items.count() == 7
    assert page.evaluate("document.getElementById('rail').scrollWidth") <= 390
    boxes = [items.nth(i).bounding_box() for i in range(7)]
    assert all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in boxes)
    assert boxes[-1]["x"] + boxes[-1]["width"] > 300        # the row is spread, not bunched left
    assert all(page.evaluate("e => getComputedStyle(e).overflow", items.nth(i).locator(".rail-label").element_handle()) for i in range(7))


@pytest.mark.parametrize("name,w,h,touch", [("spend-phone", 390, 844, True), ("spend-fhd", 1920, 1080, False), ("spend-uw", 3440, 1440, False)])
def test_look(page_at, name, w, h, touch):
    page = _sp(page_at, w, h, touch=touch)
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


def test_provider_card_lists_and_opens_form(page_at):
    page = _sp(page_at, 1920, 1080)
    page.wait_for_selector("#sp-prov-list .sp-prov", timeout=15000)
    assert page.locator("#sp-prov-list .sp-prov").count() == 9
    assert "DeepSeek" in page.inner_text("#sp-prov-list") and "OpenAI" in page.inner_text("#sp-prov-list")
    page.click("[data-open='anthropic']")
    assert page.locator("#sp-prov-list input[name='api_key']").get_attribute("type") == "password"
    page.click("[data-cancel]")
    assert page.locator("#sp-prov-list form").count() == 0
    page.click("[data-open='xai']")
    assert page.locator("#sp-prov-list input[name='team_id']").count() == 1
    assert "docs/SPEND-PROVIDERS.md" in page.get_attribute(".sp-providers a", "href")
