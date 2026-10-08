"""Tests for the multi-backend slots collector."""

from unittest.mock import patch, call
import pytest

from llamawatch.collectors import slots as slots_mod


# ── Fake data fixtures ───────────────────────────────────────────────────────

# 8-slot primary-backend response — slot 2 busy, with decoded/remain activity
FAKE_PRIMARY_RAW = [
    {"id": i, "n_ctx": 49152, "is_processing": False,
     "id_task": 1000 + i,
     "next_token": [{"has_next_token": False, "n_decoded": 10, "n_remain": 90}]}
    for i in range(8)
]
# Make slot 2 busy with activity
FAKE_PRIMARY_RAW[2] = {
    "id": 2, "n_ctx": 49152, "is_processing": True,
    "id_task": 1002,
    "next_token": [{"has_next_token": True, "n_decoded": 3200, "n_remain": 1540}],
}

# 2-slot secondary-backend response — all idle
FAKE_SECONDARY_RAW = [
    {"id": i, "n_ctx": 131072, "is_processing": False,
     "id_task": 2000 + i,
     "next_token": [{"has_next_token": False, "n_decoded": 5, "n_remain": 295}]}
    for i in range(2)
]

FAKE_CONFIG = {
    "backends": [
        {"type": "llamacpp", "name": "Main", "url": "http://localhost:8081",
         "swap_proxy": True, "direct_port": 8080},
        {"type": "llamacpp", "name": "Secondary", "url": "http://localhost:8082"},
    ]
}


# ── Unit: _resolve_model_id ──────────────────────────────────────────────────

class TestResolveModelId:
    def test_returns_id_when_reachable(self):
        resp = {"data": [{"id": "Qwen3.6-35B-A3B"}]}
        with patch.object(slots_mod, "_http_get_json", return_value=resp):
            assert slots_mod._resolve_model_id("http://localhost:8080") == "Qwen3.6-35B-A3B"

    def test_returns_none_when_unreachable(self):
        with patch.object(slots_mod, "_http_get_json", return_value=None):
            assert slots_mod._resolve_model_id("http://localhost:8080") is None

    def test_returns_none_when_data_empty(self):
        with patch.object(slots_mod, "_http_get_json", return_value={"data": []}):
            assert slots_mod._resolve_model_id("http://localhost:8080") is None


# ── Unit: _friendly_name ─────────────────────────────────────────────────────

class TestFriendlyName:
    def test_uses_configured_friendly_name(self):
        backend = {"name": "Main", "swap_proxy": True, "direct_port": 8080}
        with patch("llamawatch.config.get_model_friendly_name",
                   return_value="Big-Model"):
            assert slots_mod._friendly_name(backend, "model-xyz") == "Big-Model"

    def test_falls_back_to_backend_name_when_no_mapping(self):
        # get_model_friendly_name returns the model_id unchanged → use backend name.
        backend = {"name": "CustomLLM"}
        with patch("llamawatch.config.get_model_friendly_name",
                   side_effect=lambda m: m):
            assert slots_mod._friendly_name(backend, "UnknownModel") == "CustomLLM"

    def test_falls_back_to_backend_name_when_no_model_id(self):
        backend = {"name": "CustomLLM"}
        assert slots_mod._friendly_name(backend, None) == "CustomLLM"

    def test_generic_label_when_no_name(self):
        backend = {}
        assert slots_mod._friendly_name(backend, None) == "Backend"


# ── Unit: _extract_activity ──────────────────────────────────────────────────

class TestExtractActivity:
    def test_extracts_task_id_and_decoded_remain(self):
        slot = {
            "id": 2, "n_ctx": 49152, "is_processing": True,
            "id_task": 9999,
            "next_token": [{"has_next_token": True, "n_decoded": 3200, "n_remain": 1540}],
        }
        act = slots_mod._extract_activity(slot)
        assert act["task_id"] == 9999
        assert act["tokens_decoded"] == 3200
        assert act["tokens_remain"] == 1540

    def test_missing_next_token_gives_partial(self):
        slot = {"id": 0, "n_ctx": 49152, "id_task": 5}
        act = slots_mod._extract_activity(slot)
        assert act["task_id"] == 5
        assert "tokens_decoded" not in act

    def test_empty_next_token_list(self):
        slot = {"id": 0, "n_ctx": 49152, "id_task": 5, "next_token": []}
        act = slots_mod._extract_activity(slot)
        assert "tokens_decoded" not in act


# ── Unit: _slots_base_url ────────────────────────────────────────────────────

class TestSlotsBaseUrl:
    def test_swap_proxy_uses_direct_port(self):
        b = {"url": "http://localhost:8081", "swap_proxy": True, "direct_port": 8080}
        assert slots_mod._slots_base_url(b) == "http://localhost:8080"

    def test_non_proxy_uses_url(self):
        b = {"url": "http://localhost:8082"}
        assert slots_mod._slots_base_url(b) == "http://localhost:8082"


# ── Integration: collect() multi-backend ────────────────────────────────────

class TestCollect:
    @pytest.fixture(autouse=True)
    def _no_friendly_mapping(self):
        # With no model_names map configured, get_model_friendly_name returns the
        # model_id unchanged, so backend names come from the backend config.
        # Also clear the per-backend cache so unreachable-backend tests don't see
        # totals left over from a prior reachable run.
        slots_mod._last_known.clear()
        with patch("llamawatch.config.get_model_friendly_name",
                   side_effect=lambda m: m):
            yield
        slots_mod._last_known.clear()

    def _make_http_get(self, primary_model="model-primary", secondary_model="model-secondary",
                       primary_raw=None, secondary_raw=None,
                       primary_reachable=True, secondary_reachable=True):
        """Return a mock for _http_get_json that routes by URL."""
        if primary_raw is None:
            primary_raw = FAKE_PRIMARY_RAW
        if secondary_raw is None:
            secondary_raw = FAKE_SECONDARY_RAW

        def side_effect(url, timeout=2.0):
            if "8080/v1/models" in url:
                return {"data": [{"id": primary_model}]} if primary_reachable else None
            if "8082/v1/models" in url:
                return {"data": [{"id": secondary_model}]} if secondary_reachable else None
            if "8080/slots" in url:
                return primary_raw if primary_reachable else None
            if "8082/slots" in url:
                return secondary_raw if secondary_reachable else None
            return None

        return side_effect

    def test_returns_both_backends(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        assert "backends" in result
        assert len(result["backends"]) == 2

    def test_total_sums_both_backends(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        assert result["total"] == 10   # 8 primary + 2 secondary
        assert result["busy"] == 1     # only slot 2 on the primary backend is busy

    def test_primary_backend_shape(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        primary = next(b for b in result["backends"] if b["name"] == "Main")
        assert primary["total"] == 8
        assert primary["busy"] == 1
        assert primary["reachable"] is True
        assert primary["model"] == "model-primary"

    def test_secondary_backend_shape(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        secondary = next(b for b in result["backends"] if b["name"] == "Secondary")
        assert secondary["total"] == 2
        assert secondary["busy"] == 0
        assert secondary["reachable"] is True

    def test_busy_slot_has_activity_fields(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        primary = next(b for b in result["backends"] if b["name"] == "Main")
        busy = [s for s in primary["slots"] if s["busy"]]
        assert len(busy) == 1
        s = busy[0]
        assert s["id"] == 2
        assert s["task_id"] == 1002
        assert s["tokens_decoded"] == 3200
        assert s["tokens_remain"] == 1540
        assert s["ctx_total"] == 49152

    def test_idle_slot_has_no_activity_fields(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        primary = next(b for b in result["backends"] if b["name"] == "Main")
        idle = [s for s in primary["slots"] if not s["busy"]]
        for s in idle:
            assert "tokens_decoded" not in s
            assert "tokens_remain" not in s

    def test_unreachable_backend_marked_reachable_false(self):
        with patch.object(slots_mod, "_http_get_json",
                          side_effect=self._make_http_get(secondary_reachable=False)):
            result = slots_mod.collect(config=FAKE_CONFIG)
        # The second backend (8082) is unreachable; its name comes from config
        # since the model ID could not be resolved.  Either way, reachable must be False.
        assert len(result["backends"]) == 2
        unreachable = [b for b in result["backends"] if not b["reachable"]]
        assert len(unreachable) == 1
        assert unreachable[0]["total"] == 0
        assert unreachable[0]["slots"] == []

    def test_unreachable_backend_does_not_break_totals(self):
        with patch.object(slots_mod, "_http_get_json",
                          side_effect=self._make_http_get(secondary_reachable=False)):
            result = slots_mod.collect(config=FAKE_CONFIG)
        # Only the primary backend counts
        assert result["total"] == 8
        assert result["busy"] == 1

    def test_all_backends_unreachable_returns_empty_backends(self):
        with patch.object(slots_mod, "_http_get_json",
                          side_effect=self._make_http_get(
                              primary_reachable=False, secondary_reachable=False)):
            result = slots_mod.collect(config=FAKE_CONFIG)
        # Should still return the backends key (with reachable=False entries)
        assert "backends" in result
        for b in result["backends"]:
            assert b["reachable"] is False
        assert result["total"] == 0
        assert result["busy"] == 0

    def test_returns_empty_when_no_config(self):
        result = slots_mod.collect(config=None)
        assert result == {}

    def test_returns_empty_when_no_llamacpp_backends(self):
        cfg = {"backends": [{"type": "other", "name": "X", "url": "http://x"}]}
        result = slots_mod.collect(config=cfg)
        assert result == {}

    def test_ctx_total_on_all_slots(self):
        with patch.object(slots_mod, "_http_get_json", side_effect=self._make_http_get()):
            result = slots_mod.collect(config=FAKE_CONFIG)
        primary = next(b for b in result["backends"] if b["name"] == "Main")
        for s in primary["slots"]:
            assert s["ctx_total"] == 49152
