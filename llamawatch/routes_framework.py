"""APIRouter for framework endpoints: connections, audit, SSE, press-room."""
import json
import os
import sqlite3
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from . import connections, audit, security
from .auth import is_auth_enabled
from .config import get_config_dir, reload_config, _deep_merge, encrypt_secrets
from .sse import event_stream
from .ws_hub import get_hub

# ── Docs browser ─────────────────────────────────────────────────────────────
# Docs roots come from config (docs_roots: [{path, label}, ...]). When unset,
# the docs browser simply shows nothing — no personal paths baked into source.
_DOCS_SKIP = {".git", "__pycache__", "node_modules", "worktrees", ".superpowers",
              "vendor", "dist", "build", ".next", ".cache", "coverage", ".pytest_cache",
              "__tests__", "static", "public", "reports", "brainstorm", ".lovable",
              "benchmark", "fixtures", "snapshots", "challenges", "rss", "saved-drafts",
              "job_specs", "skills", "docs", "archive", "news", "drafts"}


def _articles_db():
    """Press Room articles DB path from config, or None when unset."""
    try:
        from .config import load_config
        p = load_config().get("press_room_db")
    except Exception:
        p = None
    return Path(os.path.expanduser(p)) if p else None


def _docs_all_roots():
    try:
        from .config import load_config
        roots = load_config().get("docs_roots") or []
    except Exception:
        roots = []
    out = []
    for entry in roots:
        if isinstance(entry, dict) and entry.get("path"):
            out.append((Path(os.path.expanduser(entry["path"])), entry.get("label", entry["path"])))
    return out


def _safe_resolve_docs(path_str: str):
    """Resolve path and verify it falls within a whitelisted docs root."""
    try:
        p = Path(path_str).resolve()
    except Exception:
        return None
    for root, _ in _docs_all_roots():
        try:
            rr = root.resolve()
            p.relative_to(rr)
            return p
        except (ValueError, OSError):
            continue
    return None

router = APIRouter()


def _cfg():
    import llamawatch.server as srv
    return srv._config or {}


def _deny():
    return JSONResponse(status_code=403, content={"status": "error", "message": "not permitted from this client"})


@router.get("/api/connections")
async def list_connections():
    cfg = _cfg()
    return {"types": connections.connection_types(), "connections": connections.list_redacted(cfg)}


@router.put("/api/connections/{conn_id}")
async def upsert_connection(conn_id: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    body = await request.json()
    ok, err = connections.validate(body)
    if not ok:
        return JSONResponse(status_code=400, content={"status": "error", "message": err})
    config_dir = get_config_dir()
    local_path = config_dir / "config.local.json"
    existing = json.loads(local_path.read_text()) if local_path.is_file() else {}
    merged = _deep_merge(existing, {"connections": {conn_id: body}})
    merged = encrypt_secrets(merged)
    local_path.write_text(json.dumps(merged, indent=2))
    os.chmod(str(local_path), 0o600)
    import llamawatch.server as srv
    srv._config = reload_config(config_dir=config_dir)
    audit.append("connection_upsert", target=conn_id, outcome="ok")
    return {"status": "ok"}


@router.delete("/api/connections/{conn_id}")
async def delete_connection(conn_id: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    config_dir = get_config_dir()
    local_path = config_dir / "config.local.json"
    existing = json.loads(local_path.read_text()) if local_path.is_file() else {}
    existing.get("connections", {}).pop(conn_id, None)
    existing = encrypt_secrets(existing)
    local_path.write_text(json.dumps(existing, indent=2))
    os.chmod(str(local_path), 0o600)
    import llamawatch.server as srv
    srv._config = reload_config(config_dir=config_dir)
    audit.append("connection_delete", target=conn_id, outcome="ok")
    return {"status": "ok"}


@router.get("/api/audit")
async def get_audit(request: Request, limit: int = 100):
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    return {"events": audit.read(limit=limit)}


@router.get("/sse")
async def sse_endpoint(request: Request):
    import llamawatch.server as srv
    cfg = srv._config or {}
    enabled_ids = cfg.get("widgets", {}).get("enabled")
    hub = get_hub(cfg, srv._adapters)
    hub._registry = srv._collector_registry
    return EventSourceResponse(event_stream(hub, cfg, srv._adapters, enabled_ids))


# Note: GET /api/press-room/article/{id} is served from server.py (registered
# first, so it takes precedence). Not duplicated here.


# ── Press Room: search ────────────────────────────────────────────────────────

@router.get("/api/press-room/search")
async def press_room_search(q: str = "", limit: int = 30):
    """Search press room articles by title/topic keyword, most-recent first.
    Empty q returns the latest N articles.
    Returns {articles:[{id,title,status,when,topic}]}.
    """
    db_path = _articles_db()
    if not db_path or not db_path.exists():
        return {"articles": [], "total": 0, "query": q}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        lim = max(1, min(200, limit))
        if q.strip():
            pattern = f"%{q.strip()}%"
            rows = cur.execute(
                """SELECT id, title, topic_key, topic_display, tier, created_at
                   FROM articles
                   WHERE title LIKE ? OR topic_key LIKE ? OR topic_display LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (pattern, pattern, pattern, lim),
            ).fetchall()
            total = cur.execute(
                "SELECT COUNT(*) FROM articles WHERE title LIKE ? OR topic_key LIKE ? OR topic_display LIKE ?",
                (pattern, pattern, pattern),
            ).fetchone()[0]
        else:
            rows = cur.execute(
                "SELECT id, title, topic_key, topic_display, tier, created_at FROM articles ORDER BY created_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
            total = cur.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        con.close()

        import datetime

        def _when(created_at):
            if not created_at:
                return ""
            try:
                dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                now = datetime.datetime.now(datetime.timezone.utc)
                delta = now - dt.astimezone(datetime.timezone.utc)
                mins = int(delta.total_seconds() / 60)
                if mins < 60:
                    return f"{mins}m ago"
                hrs = mins // 60
                if hrs < 24:
                    return f"{hrs}h ago"
                return f"{delta.days}d ago"
            except Exception:
                return created_at[:10] if created_at else ""

        def _status(tier):
            tmap = {0: "tier-0", 1: "tier-1", 2: "tier-2"}
            return tmap.get(tier, "article")

        articles = []
        for row in rows:
            topic = (row["topic_display"] or row["topic_key"] or "").replace("topic:", "").replace("_", " ")
            articles.append({
                "id":     row["id"],
                "title":  row["title"] or "(untitled)",
                "topic":  topic,
                "status": _status(row["tier"] or 0),
                "when":   _when(row["created_at"]),
            })
        return {"articles": articles, "total": total, "query": q}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── RAG Library: search via knowledge hub ────────────────────────────────────────────

def _hub_config():
    """Return (hub_url, api_key) from config, or (None, '') when unset."""
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        return None, ""
    hub_url = cfg.get("hub_url")
    key = ""
    kp = cfg.get("hub_api_key_path")
    if kp:
        p = Path(os.path.expanduser(kp))
        if p.exists():
            key = p.read_text().strip()
    return hub_url, key


@router.get("/api/library/hub-search")
async def library_hub_search(q: str = Query(..., min_length=1)):
    """Search a configured knowledge hub's RAG library.
    Proxies GET <hub_url>/docs?q=<q> with the configured API key.
    Returns {results:[...]} or {results:[],error:...} when not configured/unreachable.
    """
    hub_url, key = _hub_config()
    if not hub_url:
        return {"results": [], "error": "hub not configured"}
    url = f"{hub_url.rstrip('/')}/docs?q={urllib.parse.quote(q)}"
    try:
        req = urllib.request.Request(url, headers={"X-API-Key": key})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.URLError:
        return {"results": [], "error": "hub unreachable"}
    except Exception as exc:
        return {"results": [], "error": str(exc)}

    results = []
    # Hub returns a list or {"results":[...]} or {"hits":[...]}
    hits = raw if isinstance(raw, list) else (raw.get("results") or raw.get("hits") or [])
    for hit in hits[:20]:
        results.append({
            "title":   hit.get("title") or hit.get("document_title") or hit.get("source", ""),
            "snippet": hit.get("text") or hit.get("chunk") or hit.get("content") or "",
            "source":  hit.get("source") or hit.get("collection") or "",
            "score":   hit.get("score") or hit.get("relevance") or 0,
        })
    return {"results": results, "query": q}


# ── Docs browser ──────────────────────────────────────────────────────────────

@router.get("/api/docs/tree")
async def docs_tree():
    """List all .md files under the docs roots, most-recently-modified first."""
    files = []
    seen: set = set()
    for root, label in _docs_all_roots():
        if not root.exists():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _DOCS_SKIP]
                for fname in filenames:
                    if not fname.endswith(".md"):
                        continue
                    fpath = Path(dirpath) / fname
                    key = str(fpath.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        st = fpath.stat()
                        folder = str(Path(dirpath).relative_to(root)) if dirpath != str(root) else ""
                        if folder == ".":
                            folder = ""
                        files.append({
                            "path":       str(fpath),
                            "name":       fname,
                            "folder":     folder,
                            "root_label": label,
                            "size":       st.st_size,
                            "modified":   st.st_mtime,
                        })
                    except OSError:
                        continue
        except OSError:
            continue
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"files": files, "total": len(files)}


@router.get("/api/docs/file")
async def docs_file(path: str = Query(...), download: int = 0):
    """Return a .md file's content, or serve as download if ?download=1."""
    from fastapi.responses import FileResponse as _FR
    resolved = _safe_resolve_docs(path)
    if resolved is None:
        return JSONResponse({"error": "Path not allowed"}, status_code=403)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)
    if not str(resolved).endswith(".md"):
        return JSONResponse({"error": "Only .md files are served"}, status_code=403)
    if resolved.stat().st_size > 2 * 1024 * 1024:
        return JSONResponse({"error": "File too large"}, status_code=413)
    if download:
        return _FR(str(resolved), filename=resolved.name, media_type="text/markdown")
    try:
        content = resolved.read_text(errors="replace")
        st = resolved.stat()
        return {"content": content, "name": resolved.name, "path": str(resolved), "modified": st.st_mtime}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
