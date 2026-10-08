from .conftest import VIEWPORTS


def test_page_loads_with_sample_data(page_at):
    page = page_at(1920, 1080)
    assert page.locator("#sc-model").inner_text().strip() != "—"
    assert page.locator(".view").count() == 3
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.reload(); page.wait_for_selector("#g-cpu svg")
    assert errors == []


def test_viewport_list_is_the_spec_list():
    assert [(w, h) for _, w, h in VIEWPORTS] == [(390, 844), (1366, 768), (1920, 1080),
                                                 (2560, 1440), (3440, 1440), (5120, 1440)]
