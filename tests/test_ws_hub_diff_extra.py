"""Additional tests for DashboardHub.compute_diff edge cases."""

import math
from llamawatch.ws_hub import DashboardHub, reset_hub


def setup_function():
    reset_hub()


# ── Basic diff behaviour ──────────────────────────────────────────────────────

def test_no_diff_for_identical_dicts():
    diff = DashboardHub.compute_diff({"a": 1, "b": "x"}, {"a": 1, "b": "x"})
    assert diff == {}


def test_diff_detects_changed_value():
    diff = DashboardHub.compute_diff({"cpu": 10}, {"cpu": 20})
    assert diff == {"cpu": 20}


def test_diff_detects_new_key():
    diff = DashboardHub.compute_diff({}, {"new": "value"})
    assert diff == {"new": "value"}


def test_diff_ignores_key_removed_from_new():
    """Keys present in old but missing from new are not reported."""
    diff = DashboardHub.compute_diff({"a": 1, "b": 2}, {"a": 1})
    assert diff == {}


def test_diff_empty_both():
    assert DashboardHub.compute_diff({}, {}) == {}


# ── Nested structure comparison ───────────────────────────────────────────────

def test_diff_nested_dict_equal():
    old = {"status": {"cpu": 50, "ram": 70}}
    new = {"status": {"cpu": 50, "ram": 70}}
    assert DashboardHub.compute_diff(old, new) == {}


def test_diff_nested_dict_changed():
    old = {"status": {"cpu": 50, "ram": 70}}
    new = {"status": {"cpu": 99, "ram": 70}}
    diff = DashboardHub.compute_diff(old, new)
    assert "status" in diff


def test_diff_dict_key_ordering_ignored():
    """JSON sort_keys means {"b":2,"a":1} == {"a":1,"b":2}."""
    old = {"data": {"b": 2, "a": 1}}
    new = {"data": {"a": 1, "b": 2}}
    assert DashboardHub.compute_diff(old, new) == {}


def test_diff_list_order_matters():
    old = {"items": [1, 2, 3]}
    new = {"items": [3, 2, 1]}
    diff = DashboardHub.compute_diff(old, new)
    assert "items" in diff


# ── Non-serialisable values ───────────────────────────────────────────────────

def test_diff_non_serialisable_old_always_reported():
    """When the old value can't be JSON-serialised, treat as changed."""
    obj = object()  # not serialisable
    diff = DashboardHub.compute_diff({"x": obj}, {"x": "new"})
    assert "x" in diff


def test_diff_non_serialisable_new_always_reported():
    obj = object()
    diff = DashboardHub.compute_diff({"x": "old"}, {"x": obj})
    assert "x" in diff


def test_diff_both_non_serialisable_still_reported():
    """Two different non-serialisable objects → assume changed."""
    diff = DashboardHub.compute_diff({"x": object()}, {"x": object()})
    assert "x" in diff


# ── NaN / special float values ────────────────────────────────────────────────

def test_diff_nan_vs_none():
    """NaN serialises to null in some JSON libs — treat as different from null."""
    # Python's json.dumps raises on NaN by default; this exercises the TypeError path.
    try:
        import json
        json.dumps(float("nan"))
        nan_serialisable = True
    except (ValueError, TypeError):
        nan_serialisable = False

    if not nan_serialisable:
        diff = DashboardHub.compute_diff({"v": float("nan")}, {"v": None})
        assert "v" in diff
    else:
        # If it does serialise (e.g. simplejson), NaN != null string representation
        diff = DashboardHub.compute_diff({"v": float("nan")}, {"v": None})
        assert "v" in diff


# ── add_connection / remove_connection ────────────────────────────────────────

def test_add_and_remove_connection():
    hub = DashboardHub(config={})
    ws = object()
    hub.add_connection(ws)
    assert ws in hub.connections
    assert ws in hub._connections
    hub.remove_connection(ws)
    assert ws not in hub.connections
    assert ws not in hub._connections


def test_remove_unknown_connection_is_safe():
    hub = DashboardHub(config={})
    hub.remove_connection(object())  # must not raise


# ── push_log / log_buffer ─────────────────────────────────────────────────────

def test_push_log_adds_to_buffer():
    hub = DashboardHub(config={})
    hub.push_log({"line": "hello"})
    assert len(hub.log_buffer) == 1


def test_log_buffer_maxlen_500():
    hub = DashboardHub(config={})
    for i in range(600):
        hub.push_log({"i": i})
    assert len(hub.log_buffer) == 500
    # Oldest entries are gone, newest remain
    assert hub.log_buffer[-1]["i"] == 599
