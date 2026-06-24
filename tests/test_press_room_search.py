"""Tests for press_room_search — LIKE wildcard handling and edge cases."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import httpx
import pytest

import llamawatch.config as config_mod


def _make_articles_db(path: Path):
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            title TEXT,
            topic_key TEXT,
            topic_display TEXT,
            tier INTEGER,
            created_at TEXT
        )
    """)
    con.executemany(
        "INSERT INTO articles VALUES (?,?,?,?,?,?)",
        [
            ("1", "100% off sale",        "deals",      "Deals",      0, "2024-01-01T10:00:00"),
            ("2", "50_percent_off",        "bargains",   "Bargains",   1, "2024-01-02T10:00:00"),
            ("3", "Regular article",       "news",       "News",       0, "2024-01-03T10:00:00"),
            ("4", "Another news piece",    "news",       "News",       2, "2024-01-04T10:00:00"),
        ],
    )
    con.commit()
    con.close()


@pytest.fixture()
def tmp_cfg(tmp_path):
    db = tmp_path / "articles.db"
    _make_articles_db(db)
    cfg = {
        "port": 8450,
        "host": "127.0.0.1",
        "auth_enabled": False,
        "backends": [],
        "services": [],
        "fleet": {"hosts": [{"name": "Box", "local": True, "color": "#fff"}]},
        "press_room_db": str(db),
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def client(tmp_cfg):
    from llamawatch.server import app
    import llamawatch.server as srv
    from unittest.mock import MagicMock
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture(autouse=True)
def _reset_cfg():
    config_mod.reset_config()
    yield
    config_mod.reset_config()


# ── Basic search ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_empty_returns_all(client):
    async with client as c:
        r = await c.get("/api/press-room/search?q=")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4


@pytest.mark.asyncio
async def test_search_by_title_keyword(client):
    async with client as c:
        r = await c.get("/api/press-room/search?q=news")
    data = r.json()
    assert data["total"] == 2
    titles = [a["title"] for a in data["articles"]]
    assert all("news" in t.lower() or "News" in a["topic"] for a, t in zip(data["articles"], titles))


@pytest.mark.asyncio
async def test_search_no_match_returns_empty(client):
    async with client as c:
        r = await c.get("/api/press-room/search?q=xyzzy_no_match")
    data = r.json()
    assert data["total"] == 0
    assert data["articles"] == []


# ── LIKE wildcard characters in query ────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_percent_wildcard_in_title(client):
    """'%' in query is treated as a LIKE wildcard — matches everything."""
    async with client as c:
        r = await c.get("/api/press-room/search?q=%25")  # URL-encoded %
    data = r.json()
    # '%' becomes '%%' pattern → matches all rows
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_underscore_wildcard_in_title(client):
    """'_' in query matches a single character — should still return results."""
    async with client as c:
        r = await c.get("/api/press-room/search?q=_percent_")
    data = r.json()
    # Should match '50_percent_off'
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_title_contains_percent(client):
    """Article title starts with '100%' — search for '100%' must find it."""
    async with client as c:
        r = await c.get("/api/press-room/search?q=100%25")
    data = r.json()
    assert data["total"] >= 1
    assert any("100" in a["title"] for a in data["articles"])


# ── Limit clamping ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_limit_clamped_to_1_minimum(client):
    async with client as c:
        r = await c.get("/api/press-room/search?limit=0")
    assert r.status_code == 200
    # limit=0 → clamped to 1 → at most 1 article
    data = r.json()
    assert len(data["articles"]) <= 1


@pytest.mark.asyncio
async def test_limit_clamped_to_200_maximum(client):
    async with client as c:
        r = await c.get("/api/press-room/search?limit=9999")
    assert r.status_code == 200


# ── Missing DB ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_missing_db_returns_empty(tmp_path):
    cfg = {
        "port": 8450,
        "host": "127.0.0.1",
        "auth_enabled": False,
        "backends": [],
        "services": [],
        "fleet": {"hosts": [{"name": "Box", "local": True, "color": "#fff"}]},
        "press_room_db": str(tmp_path / "nonexistent.db"),
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)

    from llamawatch.server import app
    import llamawatch.server as srv
    from unittest.mock import MagicMock
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        r = await c.get("/api/press-room/search")
    data = r.json()
    assert data["articles"] == []
    assert data["total"] == 0
