"""Tests for the press_room collector and article detail endpoint."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import llamawatch.config as config_mod


@pytest.fixture()
def auth_disabled_config(tmp_path):
    """Point the config module at a tmp dir with auth disabled.

    The article endpoint runs behind the auth middleware; without a config
    that explicitly disables auth the middleware returns 401 for API calls.
    """
    defaults = {
        "port": 8400,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "auth_password_hash": "",
        "widgets": {"enabled": []},
        "services": [],
        "model_names": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(defaults))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    yield tmp_path
    config_mod.reset_config()


def _make_articles_db(path: Path, rows: list[tuple]) -> None:
    """Create a minimal articles.db with the given rows."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            topic_key TEXT,
            title TEXT,
            hook TEXT,
            analysis TEXT,
            predictions TEXT,
            signal_card TEXT,
            topic_display TEXT,
            tier INTEGER DEFAULT 0,
            is_read INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            last_written_at TEXT
        )"""
    )
    con.executemany(
        "INSERT INTO articles (topic_key, title, tier, is_read, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def _make_rich_db(path: Path) -> None:
    """Create an articles.db with full text fields for article detail tests."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            topic_key TEXT,
            title TEXT,
            hook TEXT,
            analysis TEXT,
            predictions TEXT,
            signal_card TEXT,
            topic_display TEXT,
            tier INTEGER DEFAULT 0,
            is_read INTEGER DEFAULT 0,
            created_at TEXT,
            last_written_at TEXT
        )"""
    )
    con.execute(
        """INSERT INTO articles
            (id, topic_key, title, hook, analysis, predictions, signal_card, topic_display, tier, is_read, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "abc123",
            "topic:ai_releases",
            "Big AI Drop",
            "Something happened",
            "Deep analysis here.",
            "Prediction 1",
            "Signal summary",
            "AI Releases",
            0,
            0,
            "2026-01-01T10:00:00+00:00",
        ),
    )
    con.commit()
    con.close()


def test_basic_collect():
    """Returns articles list with expected fields."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "articles.db"
        _make_articles_db(
            db,
            [
                ("topic:ai_releases", "Big AI Drop", 0, 0, "2026-01-01T10:00:00+00:00"),
                ("topic:geopolitics", "War Room Sitrep", 1, 1, "2026-01-01T09:00:00+00:00"),
                ("topic:tech_news",   "Tech Roundup",    0, 0, "2026-01-01T08:00:00+00:00"),
            ],
        )
        import llamawatch.collectors.press_room as pr
        result = pr.collect(config={"press_room_db": str(db)})

    assert "articles" in result
    assert "total" in result
    assert result["total"] == 3
    assert len(result["articles"]) == 3

    first = result["articles"][0]
    assert first["title"] == "Big AI Drop"
    assert "when" in first
    assert "status" in first
    assert "topic" in first
    # topic_key prefix stripped
    assert "ai releases" in first["topic"] or "ai_releases" in first["topic"]


def test_missing_db_returns_empty():
    """Missing DB returns {} without raising."""
    import llamawatch.collectors.press_room as pr
    result = pr.collect(config={"press_room_db": "/nonexistent/articles.db"})
    assert result == {}


def test_empty_db():
    """Empty articles table returns valid shape with zero total."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "articles.db"
        _make_articles_db(db, [])
        import llamawatch.collectors.press_room as pr
        result = pr.collect(config={"press_room_db": str(db)})

    assert result.get("total") == 0
    assert result.get("articles") == []


def test_limit_15():
    """Only the most recent 15 articles are returned even if more exist."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "articles.db"
        rows = [
            (f"topic:topic_{i}", f"Article {i}", 0, 0, f"2026-01-{i:02d}T10:00:00+00:00")
            for i in range(1, 21)
        ]
        _make_articles_db(db, rows)
        import llamawatch.collectors.press_room as pr
        result = pr.collect(config={"press_room_db": str(db)})

    assert result["total"] == 20
    assert len(result["articles"]) == 15


def test_collect_includes_id():
    """Each article in the list now includes an id field."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "articles.db"
        _make_articles_db(
            db,
            [("topic:ai_releases", "Big AI Drop", 0, 0, "2026-01-01T10:00:00+00:00")],
        )
        import llamawatch.collectors.press_room as pr
        result = pr.collect(config={"press_room_db": str(db)})

    assert len(result["articles"]) == 1
    # id field is present (may be None for legacy rows without explicit id)
    assert "id" in result["articles"][0]


# ── Article detail endpoint tests ─────────────────────────────────────────────

def _safe_cwd():
    """Return a stable absolute path even if CWD was deleted by a previous test."""
    try:
        return os.getcwd()
    except FileNotFoundError:
        safe = tempfile.gettempdir()
        os.chdir(safe)
        return safe


def test_article_endpoint_returns_full_fields(auth_disabled_config):
    """GET /api/press-room/article/{id} returns all text fields.

    The live route is defined in server.py and resolves the DB via
    server._pr_db() (config-driven press_room_db), so the test points that
    function at a temp DB.
    """
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.server as srv
    from llamawatch.server import app

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    db = Path(db_file.name)
    try:
        _make_rich_db(db)
        with mock.patch.object(srv, "_pr_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/press-room/article/abc123")
    finally:
        db.unlink(missing_ok=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "abc123"
    assert data["title"] == "Big AI Drop"
    assert data["analysis"] == "Deep analysis here."
    assert data["hook"] == "Something happened"
    assert data["predictions"] == "Prediction 1"
    assert data["topic"] == "AI Releases"


def test_article_endpoint_404_missing(auth_disabled_config):
    """Unknown article id returns an explicit not-found error body."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.server as srv
    from llamawatch.server import app

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    db = Path(db_file.name)
    try:
        _make_rich_db(db)
        with mock.patch.object(srv, "_pr_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/press-room/article/does_not_exist")
    finally:
        db.unlink(missing_ok=True)

    assert resp.status_code == 200
    assert resp.json() == {"error": "not found"}


def test_article_endpoint_db_missing(auth_disabled_config):
    """Returns an explicit error body when the DB file does not exist."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.server as srv
    from llamawatch.server import app

    with mock.patch.object(srv, "_pr_db", return_value=Path("/nonexistent/articles.db")):
        client = TestClient(app)
        resp = client.get("/api/press-room/article/anything")

    assert resp.json() == {"error": "articles DB not configured"}
