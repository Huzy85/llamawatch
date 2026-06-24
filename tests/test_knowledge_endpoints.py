"""Tests for the Knowledge-page search/filter endpoints:
- GET /api/press-room/search
- GET /api/library/search
- GET /api/docs/tree
- GET /api/docs/file
"""

import os
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import llamawatch.config as config_mod


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def auth_disabled_config(tmp_path):
    """Load a tmp config with auth disabled for every test in this module.

    These endpoints (/api/docs/*, /api/press-room/*) sit behind the auth
    middleware, which returns 401 unless the loaded config disables auth.
    """
    defaults = {
        "port": 8451,
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


# ── helpers ────────────────────────────────────────────────────────────────

def _safe_cwd():
    try:
        return os.getcwd()
    except FileNotFoundError:
        safe = tempfile.gettempdir()
        os.chdir(safe)
        return safe


def _make_articles_db(path: Path, rows: list) -> None:
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
            updated_at TEXT
        )"""
    )
    con.executemany(
        "INSERT INTO articles (id, topic_key, title, tier, is_read, created_at) VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


# ── press-room search ─────────────────────────────────────────────────────

def test_press_room_search_empty_q_returns_latest():
    """Empty q returns latest articles."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    db = Path(db_file.name)
    try:
        _make_articles_db(db, [
            ("id1", "topic:ai",      "AI Release",   0, 0, "2026-01-03T10:00:00+00:00"),
            ("id2", "topic:geo",     "Geo Sitrep",   1, 0, "2026-01-02T10:00:00+00:00"),
            ("id3", "topic:finance", "Finance News", 0, 0, "2026-01-01T10:00:00+00:00"),
        ])
        with mock.patch.object(rf, "_articles_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/press-room/search")
    finally:
        db.unlink(missing_ok=True)

    assert resp.status_code == 200
    data = resp.json()
    assert "articles" in data
    assert data["total"] == 3
    assert len(data["articles"]) == 3
    # Most-recent first
    assert data["articles"][0]["title"] == "AI Release"
    # Each article has required fields
    for a in data["articles"]:
        assert "id" in a
        assert "title" in a
        assert "status" in a
        assert "when" in a


def test_press_room_search_keyword_filters():
    """q= filters by title."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    db = Path(db_file.name)
    try:
        _make_articles_db(db, [
            ("id1", "topic:ai",  "AI Release",   0, 0, "2026-01-03T10:00:00+00:00"),
            ("id2", "topic:geo", "Geo Sitrep",   1, 0, "2026-01-02T10:00:00+00:00"),
            ("id3", "topic:ai",  "Another AI",   0, 0, "2026-01-01T10:00:00+00:00"),
        ])
        with mock.patch.object(rf, "_articles_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/press-room/search?q=ai")
    finally:
        db.unlink(missing_ok=True)

    assert resp.status_code == 200
    data = resp.json()
    titles = [a["title"] for a in data["articles"]]
    assert "AI Release" in titles
    assert "Another AI" in titles
    assert "Geo Sitrep" not in titles


def test_press_room_search_missing_db():
    """Missing DB returns empty list, not a 500."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with mock.patch.object(rf, "_articles_db", return_value=Path("/nonexistent/articles.db")):
        client = TestClient(app)
        resp = client.get("/api/press-room/search?q=anything")

    assert resp.status_code == 200
    data = resp.json()
    assert data["articles"] == []


def test_press_room_search_limit():
    """limit param is respected."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    db = Path(db_file.name)
    try:
        rows = [
            (f"id{i}", "topic:t", f"Article {i}", 0, 0, f"2026-01-{i:02d}T10:00:00+00:00")
            for i in range(1, 11)
        ]
        _make_articles_db(db, rows)
        with mock.patch.object(rf, "_articles_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/press-room/search?limit=3")
    finally:
        db.unlink(missing_ok=True)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["articles"]) == 3
    assert data["total"] == 10


# ── library search ────────────────────────────────────────────────────────

def test_library_search_hub_unreachable():
    """Graceful empty result when Hub is unreachable."""
    _safe_cwd()
    import urllib.error
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    def _bad_urlopen(*a, **kw):
        raise urllib.error.URLError("connection refused")

    with mock.patch.object(rf, "_hub_config", return_value=("http://hub.local:8300", "testkey")), \
         mock.patch("urllib.request.urlopen", side_effect=_bad_urlopen):
        client = TestClient(app)
        resp = client.get("/api/library/hub-search?q=python")

    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert "error" in data
    assert "unreachable" in data["error"]


def test_library_search_returns_results():
    """Returns parsed results from Hub JSON."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    hub_payload = json.dumps([
        {"title": "FastAPI docs", "text": "FastAPI is a modern web framework", "source": "docs/fastapi.md", "score": 0.9},
        {"title": "Python basics", "text": "Python is easy", "source": "docs/python.md", "score": 0.7},
    ]).encode()

    class _FakeResp:
        def read(self): return hub_payload
        def __enter__(self): return self
        def __exit__(self, *a): pass

    with mock.patch.object(rf, "_hub_config", return_value=("http://hub.local:8300", "testkey")), \
         mock.patch("urllib.request.urlopen", return_value=_FakeResp()):
        client = TestClient(app)
        resp = client.get("/api/library/hub-search?q=python")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert data["results"][0]["title"] == "FastAPI docs"
    assert data["results"][0]["snippet"] == "FastAPI is a modern web framework"
    assert data["results"][0]["source"] == "docs/fastapi.md"


def test_library_search_no_hub_configured():
    """Returns an error result when no hub is configured."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with mock.patch.object(rf, "_hub_config", return_value=(None, "")):
        client = TestClient(app)
        resp = client.get("/api/library/hub-search?q=test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert "error" in data


# ── docs tree ─────────────────────────────────────────────────────────────

def test_docs_tree_lists_md_files():
    """Tree endpoint lists .md files under roots."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "notes.md").write_text("# Notes")
        (root / "sub").mkdir()
        (root / "sub" / "deep.md").write_text("# Deep")
        (root / "ignore.py").write_text("# not markdown")

        fake_roots = [(root, "Test")]
        with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
            client = TestClient(app)
            resp = client.get("/api/docs/tree")

    assert resp.status_code == 200
    data = resp.json()
    names = [f["name"] for f in data["files"]]
    assert "notes.md" in names
    assert "deep.md" in names
    assert "ignore.py" not in names
    assert data["total"] == 2


def test_docs_tree_skips_hidden_dirs():
    """Tree skips directories in _DOCS_SKIP."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "ok.md").write_text("ok")
        (root / ".git").mkdir()
        (root / ".git" / "hidden.md").write_text("hidden")

        with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
            client = TestClient(app)
            resp = client.get("/api/docs/tree")

    assert resp.status_code == 200
    data = resp.json()
    names = [f["name"] for f in data["files"]]
    assert "ok.md" in names
    assert "hidden.md" not in names


# ── docs file ────────────────────────────────────────────────────────────

def test_docs_file_returns_content():
    """File endpoint returns markdown content."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "hello.md").write_text("# Hello World")

        with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
            client = TestClient(app)
            resp = client.get(f"/api/docs/file?path={root / 'hello.md'}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "# Hello World"
    assert data["name"] == "hello.md"


def test_docs_file_path_traversal_blocked():
    """Path traversal outside docs roots returns 403."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # Create a file OUTSIDE the docs root
        outside = Path(tempfile.gettempdir()) / "outside.md"
        outside.write_text("secret")
        try:
            with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
                client = TestClient(app)
                resp = client.get(f"/api/docs/file?path={outside}")
            assert resp.status_code == 403
        finally:
            outside.unlink(missing_ok=True)


def test_docs_file_not_found():
    """Non-existent file returns 404."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
            client = TestClient(app)
            resp = client.get(f"/api/docs/file?path={root / 'nonexistent.md'}")

    assert resp.status_code == 404


def test_docs_file_non_md_blocked():
    """Requesting a .py file is blocked even within the docs root."""
    _safe_cwd()
    from fastapi.testclient import TestClient
    import llamawatch.routes_framework as rf
    from llamawatch.server import app

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "script.py").write_text("import os")
        with mock.patch.object(rf, "_docs_all_roots", return_value=[(root, "Test")]):
            client = TestClient(app)
            resp = client.get(f"/api/docs/file?path={root / 'script.py'}")

    assert resp.status_code == 403
