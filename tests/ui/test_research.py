"""Research pages in sample mode: the start page, the built-in demo report and a
report read from a stored run. Phone and desktop widths, no sideways scroll,
the Social drafts panel opens and closes, and a refused drafts request ends in
a message instead of a spinner. All data here is invented (example.com)."""
import json
from pathlib import Path

import pytest

RUN_ID = "20260101-120000-abc123"
SIZES = [(390, 844, True), (1440, 900, False), (2560, 1080, False)]

_REPORT = """# Window boxes beat raised beds for a small balcony

*Question: Which is the better way to grow herbs on a small balcony, window boxes or a raised bed?*

## Summary

Window boxes suit a balcony under four square metres better than a raised bed [1]. They weigh less when wet and can be moved for the sun [2].

## Key numbers

| What | Number | Source |
|---|---|---|
| Wet weight of a 60cm window box | about 18kg | [1] |
| Wet weight of a 1m raised bed | about 160kg | [2] |

## Sources

1. Example Garden Guide, example.com
2. Example Balcony Notes, example.org
"""

_SOURCES = {
    "1": {"page": "p1", "url": "https://example.com/window-boxes", "title": "Example Garden Guide",
          "site": "example.com", "kind": "guide", "published": "2025-05-01", "read": "2026-01-01",
          "via": "fetch", "quotes": ["A 60cm window box holds about 18kg when wet."]},
    "2": {"page": "p2", "url": "https://example.org/raised-beds", "title": "Example Balcony Notes",
          "site": "example.org", "kind": "blog", "published": "2025-06-01", "read": "2026-01-01",
          "via": "fetch", "quotes": ["A 1m raised bed full of wet soil weighs about 160kg."]},
}


@pytest.fixture(scope="module", autouse=True)
def stored_run():
    # conftest points HOME at a throwaway folder, so this never touches real runs.
    d = Path.home() / ".local" / "share" / "llamawatch" / "research" / RUN_ID
    (d / "pages").mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "question": "Which is the better way to grow herbs on a small balcony, window boxes or a raised bed?",
        "model": "example-model", "depth": "quick", "status": "done",
        "started": "2026-01-01T12:00:00", "seconds": 95, "cost": 0,
        "next_questions": ["Which herbs grow best in a north-facing window box?"]}))
    (d / "report.md").write_text(_REPORT)
    (d / "sources.json").write_text(json.dumps(_SOURCES))
    yield d


def _no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def _open(page_at, w, h, touch, path):
    page = page_at(w, h, touch=touch, path=path)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page, errors


@pytest.mark.parametrize("w,h,touch", SIZES)
def test_start_page_fits(page_at, w, h, touch):
    page, errors = _open(page_at, w, h, touch, "/research")
    page.wait_for_selector("#rs-q", timeout=15000)
    assert page.locator("#rs-q").is_visible()
    assert _no_sideways_scroll(page)
    assert errors == []


@pytest.mark.parametrize("w,h,touch", SIZES)
def test_demo_report_drafts_panel(page_at, w, h, touch):
    page, errors = _open(page_at, w, h, touch, "/static/research-report.html")
    page.wait_for_selector("#sheet h1", timeout=15000)
    assert _no_sideways_scroll(page)
    page.click("#btnDrafts", timeout=5000)
    assert page.evaluate("document.body.classList.contains('drafts-open')")
    assert page.locator("#drafts").is_visible()
    page.click("#closeDrafts", timeout=5000)
    assert not page.evaluate("document.body.classList.contains('drafts-open')")
    assert errors == []


@pytest.mark.parametrize("w,h,touch", SIZES)
def test_stored_report_renders(page_at, w, h, touch):
    page, errors = _open(page_at, w, h, touch, f"/research/runs/{RUN_ID}/report")
    page.wait_for_selector("#sheet h1", timeout=15000)
    assert "Window boxes beat raised beds" in page.inner_text("#sheet h1")
    assert "example.com" in page.inner_text("#sheet")
    assert _no_sideways_scroll(page)
    assert errors == []


def test_stored_report_refused_drafts_end_in_a_message(page_at):
    # Sample mode refuses every write, which stands in for a failed request:
    # the panel must leave the spinner and say what happened.
    page, errors = _open(page_at, 1440, 900, False, f"/research/runs/{RUN_ID}/report")
    page.wait_for_selector("#sheet h1", timeout=15000)
    page.click("#btnDrafts", timeout=5000)
    page.click("#makeDrafts", timeout=5000)
    page.wait_for_selector("#makeDrafts", timeout=10000)
    page.wait_for_function("document.getElementById('toast').textContent.length > 0", timeout=10000)
    assert errors == []


def test_unknown_run_is_404(server):
    import urllib.error
    import urllib.request
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(server + "/research/runs/20260101-120000-ffffff/report")
    assert e.value.code == 404


def test_report_citation_tap_area_on_phone(page_at):
    page, _ = _open(page_at, 390, 844, True, "/static/research-report.html")
    page.wait_for_selector("sup.cite a", timeout=15000)
    box = page.locator("sup.cite a").first.evaluate(
        "e => { const s = getComputedStyle(e); return [parseFloat(s.paddingTop) + parseFloat(s.paddingBottom), e.getBoundingClientRect().height]; }")
    assert box[0] >= 20  # padding gives a tap target of at least 32px
