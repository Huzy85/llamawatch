def test_fleet_values_survive_dom_rebuild(page_at):
    """Rebuilding the fleet DOM (settings load or Fleet editor) must keep the last numbers."""
    page = page_at(1920, 1080)
    page.wait_for_function("document.getElementById('bs-node-b-cpu')?.textContent.includes('%')", timeout=15000)
    page.evaluate("""() => {
        const hosts = JSON.parse(localStorage.getItem('studio-fleet'));
        hosts[0].color = '#123456';           // new signature forces a rebuild
        window.StudioApplyFleet(hosts);
    }""")
    assert "%" in page.locator("#bs-node-b-cpu").inner_text()


def test_fleet_card_ram_reads_once_in_gb(page_at):
    page = page_at(3440, 1440)
    page.wait_for_function("/\\d/.test(document.getElementById('fm-node-a-ram-gb')?.textContent)", timeout=15000)
    assert page.locator("#fm-node-a-ram-gb").inner_text().strip() == "48.2"  # label beside it says "GB used"


def test_process_donuts_survive_dom_rebuild(page_at):
    """Top-processes donuts must show data even when the donut cells are rebuilt after the data arrived."""
    page = page_at(3440, 1440)
    page.wait_for_function("document.getElementById('proc-donut-legend-node-b')", timeout=15000)
    page.wait_for_timeout(1500)
    page.evaluate("""() => {
        const hosts = JSON.parse(localStorage.getItem('studio-fleet'));
        hosts[0].color = '#123456';
        window.StudioApplyFleet(hosts);
    }""")
    for k in ("node-b", "node-c"):
        assert page.locator(f"#proc-donut-legend-{k} .proc-leg-item").count() > 0, f"{k} donut empty"
        assert "%" in page.locator(f"#proc-donut-label-{k}").text_content()


def test_agent_states_survive_rerender(page_at):
    """Agent rows rebuilt from settings after the docker data arrived must still show UP/DOWN."""
    page = page_at(1920, 1080)
    page.wait_for_function("document.querySelectorAll('#agents-list .agent-row').length === 5", timeout=15000)
    page.wait_for_timeout(1500)
    page.evaluate("() => window.StudioApplyAgents(JSON.parse(localStorage.getItem('studio-agents')))")
    states = page.locator("#agents-list .agent-state").all_inner_texts()
    assert states == ["UP", "UP", "UP", "UP", "DOWN"], states


def test_phone_agents_summary_counts_agents(page_at):
    page = page_at(390, 844)
    page.wait_for_function("document.querySelectorAll('#agents-list .agent-row').length === 5", timeout=15000)
    page.wait_for_timeout(1500)
    assert page.locator("#mob-sum-agents").inner_text().strip() == "4/5 up"
