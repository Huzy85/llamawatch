"""Jobs page in sample mode: list, filters, hour strip, detail, phone layout, look references."""
import os
import shutil
from pathlib import Path

import pytest

from .helpers import diff_ratio

HERE = Path(__file__).parent
REF, OUT = HERE / "reference", HERE / "out"


def _jobs(page_at, w, h, touch=False):
    page = page_at(w, h, touch=touch, path="/jobs")
    page.wait_for_selector(".jb-item", timeout=15000)
    return page


def _names(page):
    return page.eval_on_selector_all(".jb-item .jb-name b", "els => els.map(e => e.firstChild.textContent)")


def test_list_renders_failed_first(page_at):
    page = _jobs(page_at, 1920, 1080)
    names = _names(page)
    # running first (by name), then failed by next run, then the rest
    assert len(names) == 12 and names[:2] == ["Research: which e-ink tablets handle handwritten notes best under £300?", "weather-cache"]
    assert names[2:4] == ["photo-sync", "disk-report"]
    assert page.inner_text("#jb-count") == "12 jobs"
    assert " ".join(page.inner_text(".jb-chip.failed").split()) == "Failed 2"
    assert "failed 19 min ago, exit code 1" in page.inner_text(".jb-item:nth-child(3) .jb-last")
    assert page.get_attribute("#rail-item-jobs", "aria-current") == "page"


def test_chips_filter_and_remember(page_at):
    page = _jobs(page_at, 1920, 1080)
    page.click(".jb-chip.failed")
    assert _names(page) == ["photo-sync", "disk-report"]
    assert page.inner_text("#jb-count") == "2 of 12"
    page.reload(); page.wait_for_selector(".jb-item")
    assert _names(page) == ["photo-sync", "disk-report"]
    page.click(".jb-chip.all")
    assert len(_names(page)) == 12


def test_hour_strip_filters(page_at):
    page = _jobs(page_at, 1920, 1080)
    # photo-sync (+11 min), feed-fetch (+6), check-mail (+15) are all due in the first hour
    assert "3 jobs" in page.get_attribute(".jb-hour[data-hour='0']", "aria-label")
    page.click(".jb-hour[data-hour='0']")
    assert sorted(_names(page)) == ["check-mail.sh", "feed-fetch", "photo-sync"]
    assert "12:00 to 13:00" in page.inner_text("#jb-strip-note")
    page.click(".jb-hour[data-hour='0']")
    assert len(_names(page)) == 12


def test_search(page_at):
    page = _jobs(page_at, 1920, 1080)
    page.fill("#jb-search", "nas")
    assert sorted(_names(page)) == ["nightly-backup", "photo-sync"]
    page.fill("#jb-search", "zzz")
    assert page.is_visible("#jb-empty")


def test_detail_opens_with_log(page_at):
    page = _jobs(page_at, 1920, 1080)
    page.click(".jb-item[data-id='user:photo-sync'] .jb-row")
    page.wait_for_selector("#jb-detail")
    txt = page.inner_text("#jb-detail")
    assert "photo-sync.timer" in txt and "photo-sync.service" in txt and "Result\nfailed, exit code 1" in txt and "Took\n4 s" in txt
    page.wait_for_function("document.getElementById('jb-log')?.textContent.includes('host unreachable')")
    page.click(".jb-item[data-id='user:photo-sync'] .jb-row")
    assert not page.is_visible("#jb-detail")


def test_read_only_kinds_have_no_buttons(page_at):
    page = _jobs(page_at, 1920, 1080)
    assert page.inner_text(".jb-item[data-id='system:fstrim'] .jb-acts") == "read-only"
    assert page.inner_text(".jb-item[data-id='cron:1'] .jb-acts") == "read-only"
    assert page.inner_text(".jb-item[data-id='research:r-demo-01'] .jb-acts") == "Open"
    assert page.inner_text(".jb-item[data-id='user:db-vacuum'] .jb-acts") == "Resume\nRun now"
    assert page.inner_text(".jb-item[data-id='user:weather-cache'] .jb-acts") == "Pause"   # running: no Run now


def test_action_asks_first_and_sample_mode_refuses(page_at):
    page = _jobs(page_at, 1920, 1080)
    page.click(".jb-item[data-id='user:photo-sync'] [data-act='run']")
    assert page.is_visible("#jb-scrim") and not page.is_visible("#jb-detail")
    assert "photo-sync" in page.inner_text("#jb-dlg-text")
    page.keyboard.press("Escape")
    assert not page.is_visible("#jb-scrim")
    page.click(".jb-item[data-id='user:photo-sync'] [data-act='pause']")
    assert "next login or reboot" in page.inner_text("#jb-dlg-text")
    page.click("#jb-dlg-ok")
    page.wait_for_selector("#jb-toast.bad")
    assert "read-only sample mode" in page.inner_text("#jb-toast")


def test_phone_layout(page_at):
    page = _jobs(page_at, 390, 844, touch=True)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not page.is_visible(".jb-cols")
    row = page.locator(".jb-item[data-id='user:photo-sync'] .jb-row")
    assert row.locator(".jb-meta").is_visible() and "every 30 min" in row.inner_text()
    row.click(); page.wait_for_selector("#jb-detail")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


# ── add / edit / delete ──────────────────────────────────────────────
def test_add_form_previews_and_sample_mode_refuses(page_at):
    page = _jobs(page_at, 1920, 1080)
    assert page.is_visible("#jb-add") and not page.is_visible("#jb-add-note")
    page.click("#jb-add")
    assert page.is_visible("#jb-form-scrim") and page.inner_text("#jb-form-title") == "New job"
    assert page.evaluate("document.activeElement.id") == "jb-f-name"
    assert not page.is_visible("#jb-f-delete") and not page.is_visible("#jb-f-linger")
    assert page.inner_text("#jb-f-save") == "Create job"
    # empty submit: client-side message, nothing sent
    page.click("#jb-f-save")
    assert "lowercase letters" in page.inner_text("#jb-form-err")
    page.fill("#jb-f-name", "disk-report-2")
    page.click("#jb-f-save")
    assert "command is needed" in page.inner_text("#jb-form-err").lower()
    page.fill("#jb-f-cmd", "/srv/demo/bin/disk-report.sh")
    page.fill("#jb-f-sched", "daily 06:30")
    page.wait_for_selector("#jb-f-preview.good")
    assert "Runs daily 06:30" in page.inner_text("#jb-f-preview") and "06:30" in page.inner_text("#jb-f-preview")
    page.click("#jb-f-save")
    page.wait_for_function("document.getElementById('jb-form-err').textContent.includes('read-only sample mode')")
    assert page.is_visible("#jb-form-scrim")   # stays open so nothing typed is lost
    page.keyboard.press("Escape")
    assert not page.is_visible("#jb-form-scrim")


def test_edit_only_on_managed_jobs_and_prefills(page_at):
    page = _jobs(page_at, 1920, 1080)
    assert page.inner_text(".jb-item[data-id='user:nightly-backup'] .jb-acts") == "Pause\nRun now\nEdit"
    assert page.inner_text(".jb-item[data-id='user:db-vacuum'] .jb-acts") == "Resume\nRun now"   # not made here
    page.click(".jb-item[data-id='user:nightly-backup'] .jb-row")
    assert "Made from this page" in page.inner_text("#jb-detail")
    page.click(".jb-item[data-id='user:nightly-backup'] [data-act='edit']")
    assert page.inner_text("#jb-form-title") == "Edit nightly-backup"
    assert page.input_value("#jb-f-name") == "nightly-backup" and page.get_attribute("#jb-f-name", "readonly") is not None
    assert page.input_value("#jb-f-cmd") == "/srv/demo/bin/backup.sh --quiet"
    assert page.input_value("#jb-f-sched") == "*-*-* 02:00:00"
    assert page.inner_text("#jb-f-save") == "Save changes" and page.is_visible("#jb-f-delete")
    page.click("#jb-f-delete")
    assert page.is_visible("#jb-scrim") and "nightly-backup" in page.inner_text("#jb-dlg-text") and page.inner_text("#jb-dlg-ok") == "Delete"
    page.click("#jb-dlg-ok")
    page.wait_for_selector("#jb-toast.bad")
    assert "read-only sample mode" in page.inner_text("#jb-toast")


def test_add_hidden_when_systemd_missing(page_at):
    init = """
      const orig = window.fetch;
      window.fetch = async (u, o) => {
        const r = await orig(u, o);
        if (String(u).startsWith('/api/jobs') && !String(u).includes('/log') && !String(u).includes('preview') && (!o || !o.method)) {
          const d = await r.json();
          d.setup = {available: false, linger: null, reason: 'systemd is not available on this system, so jobs can only be viewed here.'};
          return new Response(JSON.stringify(d), {status: 200, headers: {'Content-Type': 'application/json'}});
        }
        return r;
      };"""
    page = page_at(1920, 1080, path="/jobs", init=init)
    page.wait_for_selector(".jb-item", timeout=15000)
    assert not page.is_visible("#jb-add")
    assert "Adding jobs is off" in page.inner_text("#jb-add-note") and "systemd is not available" in page.inner_text("#jb-add-note")


def test_linger_note_when_off(page_at):
    init = """
      const orig = window.fetch;
      window.fetch = async (u, o) => {
        const r = await orig(u, o);
        if (String(u).startsWith('/api/jobs') && !String(u).includes('/log') && !String(u).includes('preview') && (!o || !o.method)) {
          const d = await r.json(); d.setup = {available: true, linger: false, reason: ''};
          return new Response(JSON.stringify(d), {status: 200, headers: {'Content-Type': 'application/json'}});
        }
        return r;
      };"""
    page = page_at(1920, 1080, path="/jobs", init=init)
    page.wait_for_selector(".jb-item", timeout=15000)
    page.click("#jb-add")
    assert page.is_visible("#jb-f-linger") and "loginctl enable-linger" in page.inner_text("#jb-f-linger")


def test_form_on_phone_has_no_sideways_scroll(page_at):
    page = _jobs(page_at, 390, 844, touch=True)
    page.click("#jb-add")
    page.fill("#jb-f-cmd", "rsync -a /srv/demo/phone/ nas.example.com:/photos/ && echo done-a-very-long-line-that-must-wrap-or-scroll-inside")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.locator("#jb-form").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["height"] <= 844


@pytest.mark.parametrize("name,w,h,touch", [("jobs-phone", 390, 844, True), ("jobs-fhd", 1920, 1080, False), ("jobs-uw", 3440, 1440, False), ("jobs-form-fhd", 1920, 1080, False)])
def test_look(page_at, name, w, h, touch):
    page = _jobs(page_at, w, h, touch=touch)
    if name == "jobs-form-fhd":
        page.click(".jb-item[data-id='user:photo-sync'] [data-act='edit']")
        page.wait_for_selector("#jb-f-preview.good")
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
