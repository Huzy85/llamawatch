def _box(page, sel):
    return page.locator(sel).bounding_box()


def test_side_rail_collapsed_on_fhd(page_at):
    page = page_at(1920, 1080)
    assert page.get_attribute("html", "data-rail") == "side"
    assert abs(_box(page, "#rail")["width"] - 48) <= 1
    assert page.is_visible("#rail-item-monitor") and page.is_visible("#rail-item-settings")


def test_hover_opens_over_page_without_moving_dials(page_at):
    page = page_at(1920, 1080)
    # Let late data (containers, fleet) finish reflowing the page before measuring.
    page.wait_for_selector(".docker-row", timeout=15000)
    page.wait_for_function("document.getElementById('bs-node-b-cpu')?.textContent.includes('%')", timeout=15000)
    page.wait_for_timeout(300)
    before = _box(page, "#g-cpu")
    page.hover("#rail"); page.wait_for_timeout(300)
    assert "open" in page.get_attribute("#rail", "class")
    assert abs(_box(page, "#rail")["width"] - 200) <= 1
    assert _box(page, "#g-cpu") == before
    page.mouse.move(1200, 600); page.wait_for_timeout(1500)
    assert "open" in page.get_attribute("#rail", "class")  # still open before 2s
    page.wait_for_timeout(900)
    assert "open" not in page.get_attribute("#rail", "class")


def test_keyboard_focus_opens_and_leaves(page_at):
    page = page_at(1920, 1080)
    page.focus("#rail-toggle"); page.wait_for_timeout(200)
    assert "open" in page.get_attribute("#rail", "class")
    page.focus("#top-settings-btn"); page.wait_for_timeout(2300)
    assert "open" not in page.get_attribute("#rail", "class")


def test_tap_opens_and_tap_outside_closes(page_at):
    page = page_at(1366, 768, touch=True)
    page.tap("#rail-toggle"); page.wait_for_timeout(200)
    assert "open" in page.get_attribute("#rail", "class")
    assert page.get_attribute("#rail-toggle", "aria-expanded") == "true"
    page.tap("#view-port"); page.wait_for_timeout(200)
    assert "open" not in page.get_attribute("#rail", "class")


def test_pin_pushes_page_and_survives_reload(page_at):
    page = page_at(1920, 1080)
    x0 = _box(page, "#studio")["x"]
    page.hover("#rail"); page.click("#rail-pin"); page.mouse.move(1200, 600); page.wait_for_timeout(2300)
    assert page.get_attribute("#rail-pin", "aria-pressed") == "true"
    assert _box(page, "#studio")["x"] >= x0 + 150
    page.reload(); page.wait_for_selector("#g-cpu svg")
    assert "rail-pinned" in page.get_attribute("html", "class")
    page.click("#rail-pin")
    assert "rail-pinned" not in (page.get_attribute("html", "class") or "")


def test_pinned_by_default_on_ultrawide(page_at):
    page = page_at(3440, 1440)
    assert "rail-pinned" in page.get_attribute("html", "class")


def test_settings_opens_from_rail(page_at):
    page = page_at(1920, 1080)
    page.hover("#rail"); page.click("#rail-item-settings")
    page.wait_for_selector(".lw-settings-panel", timeout=5000)


def test_phone_gets_bottom_bar(page_at):
    page = page_at(390, 844, touch=True)
    assert page.get_attribute("html", "data-rail") == "bottom"
    b = _box(page, "#rail")
    assert b["y"] + b["height"] >= 843 and b["width"] >= 389
    assert not page.is_visible("#rail-pin")
    assert page.locator("#studio").bounding_box()["height"] <= 844 - b["height"] + 1


def test_storage_blocked_still_lays_out(page_at):
    # Review Focus 4
    page = page_at(3440, 1440, storage_blocked=True)
    page.wait_for_selector("#rail-item-settings", state="attached", timeout=5000)
    assert page.get_attribute("html", "data-pages") == "2"
    page.wait_for_selector("#g-cpu svg", timeout=10000)  # the dashboard itself still draws


def test_rail_under_floating_windows(page_at):
    # Review Focus 5
    page = page_at(1920, 1080)
    z = page.evaluate("+getComputedStyle(document.getElementById('rail')).zIndex")
    assert 0 < z < 500
