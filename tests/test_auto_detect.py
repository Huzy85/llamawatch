from llamawatch.auto_detect import scan_backends, scan_sensors, scan_services


def test_scan_backends_returns_list():
    result = scan_backends()
    assert isinstance(result, list)
    for entry in result:
        assert "type" in entry
        assert "url" in entry


def test_scan_sensors_returns_dict():
    result = scan_sensors()
    assert isinstance(result, dict)
    assert "temps" in result
    assert "gpu_util" in result
    assert "nvidia" in result


def test_scan_services_returns_list():
    result = scan_services()
    assert isinstance(result, list)
