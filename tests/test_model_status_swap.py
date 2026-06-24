"""Tests for model_status swap-lock parsing and helper functions."""

import time
from pathlib import Path
from unittest import mock

import pytest

from llamawatch.collectors import model_status as ms


# ── _check_swap_status ────────────────────────────────────────────────────────

def test_no_lock_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "SWAP_LOCK", tmp_path / "nonexistent.lock")
    assert ms._check_swap_status() is None


def test_empty_lock_file_returns_none(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text("")
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    assert ms._check_swap_status() is None


def test_non_numeric_timestamp_returns_none(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text("not-a-number\nModelA->ModelB")
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    assert ms._check_swap_status() is None


def test_future_timestamp_gives_negative_elapsed_step1(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    # timestamp 60 seconds in the future → elapsed is negative → < 5 → step 1
    lock.write_text(str(time.time() + 60))
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    result = ms._check_swap_status()
    assert result is not None
    assert result["swap_step"] == 1
    assert result["swap_elapsed"] < 0 or result["swap_elapsed"] == 0  # round() of negative


def test_elapsed_below_first_threshold_is_step1(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text(str(time.time() - 4))  # 4s ago < 5s threshold → step 1
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    result = ms._check_swap_status()
    assert result["swap_step"] == 1
    assert result["swap_step_desc"] == "Unloading old model"


def test_elapsed_beyond_all_thresholds_is_step6(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text(str(time.time() - 1000))  # 1000s → beyond all thresholds
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    result = ms._check_swap_status()
    assert result["swap_step"] == 6
    assert result["swap_step_desc"] == "Loading new model"


def test_swap_from_to_parsed(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text(f"{time.time() - 3}\nOldModel->NewModel")
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    result = ms._check_swap_status()
    assert result["swap_from"] == "OldModel"
    assert result["swap_to"] == "NewModel"


def test_no_arrow_line_gives_none_from_to(tmp_path, monkeypatch):
    lock = tmp_path / "swap.lock"
    lock.write_text(str(time.time() - 3))
    monkeypatch.setattr(ms, "SWAP_LOCK", lock)
    result = ms._check_swap_status()
    assert result["swap_from"] is None
    assert result["swap_to"] is None


# ── _get_model_name ───────────────────────────────────────────────────────────

def test_get_model_name_empty_data_list():
    with mock.patch.object(ms, "_http_get_json", return_value={"data": []}):
        model_id, friendly = ms._get_model_name()
    assert model_id == "unknown"
    assert friendly == "Unknown"


def test_get_model_name_none_response():
    with mock.patch.object(ms, "_http_get_json", return_value=None):
        model_id, friendly = ms._get_model_name()
    assert model_id == "unknown"


def test_get_model_name_missing_data_key():
    with mock.patch.object(ms, "_http_get_json", return_value={"models": []}):
        model_id, friendly = ms._get_model_name()
    assert model_id == "unknown"


def test_get_model_name_returns_first_id():
    with mock.patch.object(ms, "_http_get_json", return_value={"data": [{"id": "gpt-4"}, {"id": "gpt-3"}]}):
        with mock.patch.object(ms, "get_model_friendly_name", return_value="GPT-4"):
            model_id, friendly = ms._get_model_name()
    assert model_id == "gpt-4"


# ── _get_n_decoded ────────────────────────────────────────────────────────────

def test_get_n_decoded_list_next_token():
    slot = {"next_token": [{"n_decoded": 42}]}
    assert ms._get_n_decoded(slot) == 42


def test_get_n_decoded_list_empty():
    slot = {"next_token": []}
    assert ms._get_n_decoded(slot) == 0


def test_get_n_decoded_dict_next_token():
    slot = {"next_token": {"n_decoded": 7}}
    assert ms._get_n_decoded(slot) == 7


def test_get_n_decoded_fallback_to_top_level():
    slot = {"n_decoded": 99}
    assert ms._get_n_decoded(slot) == 99


def test_get_n_decoded_no_keys():
    assert ms._get_n_decoded({}) == 0


# ── _read_kv_usage ────────────────────────────────────────────────────────────

def test_read_kv_usage_empty_slots():
    assert ms._read_kv_usage([]) is None


def test_read_kv_usage_all_zero_ctx():
    slots = [{"n_ctx": 0, "n_decoded": 10}]
    assert ms._read_kv_usage(slots) is None


def test_read_kv_usage_calculates_pct():
    slots = [{"n_ctx": 100, "next_token": {"n_decoded": 50}}]
    result = ms._read_kv_usage(slots)
    assert result["kv_total"] == 100
    assert result["kv_used"] == 50
    assert result["kv_pct"] == 50.0


def test_read_kv_usage_multiple_slots_summed():
    slots = [
        {"n_ctx": 100, "n_decoded": 20},
        {"n_ctx": 200, "n_decoded": 30},
    ]
    result = ms._read_kv_usage(slots)
    assert result["kv_total"] == 300
    assert result["kv_used"] == 50


# ── _check_generating_from_slots ─────────────────────────────────────────────

def test_generating_true_when_slot_processing():
    slots = [{"is_processing": False}, {"is_processing": True}]
    assert ms._check_generating_from_slots(slots) is True


def test_generating_false_when_none_processing():
    slots = [{"is_processing": False}, {"is_processing": False}]
    assert ms._check_generating_from_slots(slots) is False


def test_generating_false_for_none():
    assert ms._check_generating_from_slots(None) is False


def test_generating_false_for_empty():
    assert ms._check_generating_from_slots([]) is False
