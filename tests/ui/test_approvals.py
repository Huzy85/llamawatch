"""Approvals page in sample mode: waiting cards, rules, blocklist, audit trail, settings,
the rail badge on every page, phone layout, look references."""
import os
import shutil
from pathlib import Path

import pytest

from .helpers import diff_ratio

HERE = Path(__file__).parent
REF, OUT = HERE / "reference", HERE / "out"


def _ap(page_at, w, h, touch=False):
    page = page_at(w, h, touch=touch, path="/approvals")
    page.wait_for_selector(".ap-req", timeout=15000)
    return page


def flat(loc):
    """inner_text with every run of whitespace squashed to one space."""
    return " ".join(loc.inner_text().split())


def test_waiting_cards(page_at):
    page = _ap(page_at, 1920, 1080)
    cards = page.locator(".ap-req")
    assert cards.count() == 3 and page.inner_text("#ap-waiting-count") == "3"
    first = cards.nth(0)
    t = flat(first)
    assert "Restart the relay after the config change" in t
    assert "asked by relay-bot" in t and "2 min ago" in t and "28 min left" in t and "from 192.0.2.20" in t
    assert first.locator(".ap-cmd").inner_text() == "systemctl --user restart relay.service"
    assert first.locator("[data-always]").count() == 1          # agent shell request gets the tick
    assert cards.nth(1).locator("[data-always]").count() == 0   # a job run does not
    assert cards.nth(1).locator(".ap-kind").inner_text().lower() == "job runs" and "on photo-sync" in flat(cards.nth(1))
    assert flat(cards.nth(2).locator(".warn")) == "5 min left"
    assert page.get_attribute("#rail-item-approvals", "aria-current") == "page"
    assert page.inner_text("#rail-item-approvals .rail-badge") == "3"
    assert "only this machine can approve" in page.inner_text("#ap-local-note")


def test_approve_and_deny_refused_in_sample_mode_keep_the_card(page_at):
    page = _ap(page_at, 1920, 1080)
    page.click(".ap-req[data-id='a1b2c3d4e5f6'] [data-decide='approve']")
    page.wait_for_selector("#ap-toast.bad")
    assert "read-only sample mode" in page.inner_text("#ap-toast")
    assert page.locator(".ap-req").count() == 3
    assert page.is_enabled(".ap-req[data-id='a1b2c3d4e5f6'] [data-decide='approve']")
    page.click(".ap-req[data-id='0f9e8d7c6b5a'] [data-decide='deny']")
    page.wait_for_selector("#ap-toast.bad")
    assert page.locator(".ap-req").count() == 3


def test_rules_control_reverts_when_save_fails(page_at):
    page = _ap(page_at, 1920, 1080)
    seg = page.locator(".ap-rule[data-kind='service'] .ap-seg")
    assert seg.locator("[aria-pressed='true']").get_attribute("data-policy") == "ask"
    assert page.locator(".ap-rule[data-kind='agent'] [aria-pressed='true']").get_attribute("data-policy") == "ask"
    assert page.locator(".ap-rule[data-kind='docker'] [aria-pressed='true']").get_attribute("data-policy") == "allow"
    assert page.locator(".ap-rule").count() == 7
    seg.locator("[data-policy='block']").click()
    page.wait_for_selector("#ap-toast.bad")
    assert seg.locator("[aria-pressed='true']").get_attribute("data-policy") == "ask"


def test_blocklist_shows_builtin_and_own_patterns(page_at):
    page = _ap(page_at, 1920, 1080)
    assert page.inner_text("#ap-builtin-count") == "(9)"
    assert not page.is_visible("#ap-builtin li")
    page.click(".ap-builtin summary")
    assert page.is_visible("#ap-builtin li") and "fork bomb" in page.inner_text("#ap-builtin")
    assert page.eval_on_selector_all("#ap-patterns code", "els => els.map(e => e.textContent)") == ["docker system prune", "git push --force"]
    page.fill("#ap-pattern-input", "mkfs")
    page.click("#ap-pattern-form button")
    page.wait_for_selector("#ap-toast.bad")
    assert page.locator("#ap-patterns li").count() == 2 and page.input_value("#ap-pattern-input") == "mkfs"
    refusals = flat(page.locator("#ap-refusals"))
    assert "night-shift: rm -rf / --no-preserve-root" in refusals and "removes the root" in refusals


def test_trail_chips_search_and_request_links(page_at):
    page = _ap(page_at, 1920, 1080)
    assert page.locator(".ap-ev").count() == 22 and page.inner_text("#ap-trail-count") == "22 events"
    assert flat(page.locator(".ap-chip[data-chip='approvals']")) == "Approvals 14"
    page.click(".ap-chip[data-chip='jobs']")
    assert page.eval_on_selector_all(".ap-ev .what", "els => els.map(e => e.textContent.trim().replace(/\\s+/g, ' '))") == ["job pause db-vacuum", "job create nightly-backup"]
    page.reload(); page.wait_for_selector(".ap-ev")
    assert page.get_attribute(".ap-chip[data-chip='jobs']", "aria-pressed") == "true"   # remembered
    page.click(".ap-chip[data-chip='all']")
    page.fill("#ap-search", "relay-bot")
    assert page.locator(".ap-ev").count() == 3 and page.inner_text("#ap-trail-count") == "3 of 22"
    page.fill("#ap-search", "zzz")
    assert page.is_visible("#ap-trail-empty")
    page.fill("#ap-search", "")
    assert "denied" in flat(page.locator(".ap-ev[data-id='334455667788'] .outcome.bad"))
    page.click(".ap-ev[data-id='a1b2c3d4e5f6'] [data-req]")
    assert "flash" in page.get_attribute("#req-a1b2c3d4e5f6", "class")
    page.click(".ap-ev[data-id='334455667788'] [data-req]")
    page.wait_for_selector("#ap-toast.bad")
    assert "denied by local" in page.inner_text("#ap-toast")


def test_settings_strip(page_at):
    page = _ap(page_at, 1920, 1080)
    assert "A token is set" in page.inner_text("#ap-token-state") and page.is_visible("#ap-token-revoke")
    assert not page.is_visible("#ap-token-show")
    page.click("#ap-token-new")
    page.wait_for_selector("#ap-toast.bad")
    assert not page.is_visible("#ap-token-show")
    page.click("#ap-token-revoke")
    assert page.inner_text("#ap-token-revoke") == "Revoke, sure?"
    assert "ntfy.example.com" in page.input_value("#ap-notify") and page.input_value("#ap-ttl") == "30"
    page.fill("#ap-ttl", "0")
    page.click("#ap-ttl-form button")
    assert "between 1 and 1440" in page.inner_text("#ap-err")
    page.fill("#ap-ttl", "45")
    page.click("#ap-ttl-form button")
    page.wait_for_function("document.getElementById('ap-err').textContent.includes('read-only sample mode')")


def test_badge_shows_on_other_pages(page_at):
    page = page_at(1920, 1080, path="/jobs")
    page.wait_for_selector("#rail-item-approvals .rail-badge:not([hidden])", timeout=15000)
    assert page.inner_text("#rail-item-approvals .rail-badge") == "3"
    assert page.get_attribute("#rail-item-approvals", "aria-label") == "Approvals, 3 waiting"
    page.click("#rail-item-approvals")
    page.wait_for_url("**/approvals")


def test_badge_hides_when_nothing_waits(page_at):
    init = """
      const orig = window.fetch;
      window.fetch = async (u, o) => {
        if (String(u).startsWith('/api/approvals/summary')) return new Response('{"pending":0}', {status: 200, headers: {'Content-Type': 'application/json'}});
        return orig(u, o);
      };"""
    page = page_at(1920, 1080, path="/jobs", init=init)
    page.wait_for_selector("#rail-item-approvals .rail-badge", state="attached", timeout=15000)
    assert not page.is_visible("#rail-item-approvals .rail-badge")


def test_phone_layout(page_at):
    page = _ap(page_at, 390, 844, touch=True)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    waiting = page.locator("#ap-waiting-card").bounding_box()
    trail = page.locator(".ap-trail-card").bounding_box()
    assert trail["y"] > waiting["y"] + waiting["height"] - 1   # stacked, not side by side
    assert page.locator(".ap-req").first.locator(".rs-actions .rs-btn").first.bounding_box()["width"] > 120
    page.click(".ap-chip[data-chip='approvals']")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


@pytest.mark.parametrize("name,w,h,touch", [("approvals-phone", 390, 844, True), ("approvals-fhd", 1920, 1080, False), ("approvals-uw", 3440, 1440, False)])
def test_look(page_at, name, w, h, touch):
    page = _ap(page_at, w, h, touch=touch)
    page.mouse.move(w - 5, h // 2)
    page.wait_for_timeout(1200)   # rail closed
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
