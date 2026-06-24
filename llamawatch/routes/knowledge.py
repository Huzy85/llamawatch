"""Knowledge endpoints: anomaly explain, library, web search, press room,
predictions, and file transfer."""

import asyncio
import json
import os
from pathlib import Path
import urllib.request

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import srv
from .. import security
from ..auth import is_auth_enabled
from ..config import load_config
from ..collectors import anomaly as _anomaly_mod
from ..collectors import library_collector

router = APIRouter()


# ── Anomaly LLM Explain API ────────────────────────────────────────────

@router.post("/api/anomaly/explain")
async def anomaly_explain(request: Request):
    """Send anomaly context to the local LLM for a root cause one-liner."""
    body = await request.json()
    anomalies_list = body.get("anomalies", [])
    if not anomalies_list:
        return {"explanation": "No anomalies to explain"}

    prompt = _anomaly_mod.build_llm_prompt(anomalies_list)

    if srv._adapters is None:
        return JSONResponse(status_code=503, content={"error": "No backends configured"})
    adapter = srv._adapters.get_primary()
    if adapter is None:
        return JSONResponse(status_code=503, content={"error": "No backend available"})

    url = adapter.chat_completions_url()
    model_id = adapter.model_name()

    def _ask():
        messages = [
            {"role": "system", "content": "You are a concise ops assistant. Answer in one sentence."},
            {"role": "user", "content": prompt},
        ]
        payload = json.dumps({"model": model_id, "messages": messages, "max_tokens": 150}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"].strip()

    try:
        loop = asyncio.get_running_loop()
        explanation = await loop.run_in_executor(None, _ask)
        return {"explanation": explanation}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Library / Knowledge Base API ──────────────────────────────────────────

@router.get("/api/library/search")
async def library_search(q: str = Query(..., min_length=1)):
    """Search the knowledge hub."""
    config = srv._config or load_config()
    wc = config.get("widgets", {}).get("config", {}).get("library", {})
    hub_url = wc.get("hub_url", "").rstrip("/")
    if not hub_url:
        return JSONResponse(status_code=400, content={"error": "No hub configured"})
    key_path = wc.get("hub_api_key_path", "")
    api_key = library_collector._read_key(key_path)
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None, library_collector.search_hub, hub_url, q, api_key
    )
    return {"results": results}


@router.get("/api/library/shelves")
async def library_shelves():
    """List ChromaDB collections as shelves."""
    config = srv._config or load_config()
    wc = config.get("widgets", {}).get("config", {}).get("library", {})
    chromadb_url = wc.get("chromadb_url", "").rstrip("/")
    if not chromadb_url:
        return JSONResponse(status_code=400, content={"error": "No ChromaDB configured"})
    chroma_base = f"{chromadb_url}/api/v2/tenants/default_tenant/databases/default_database"

    def _do():
        collections = library_collector._http_get(f"{chroma_base}/collections")
        if collections is None:
            return {"error": "ChromaDB unreachable", "shelves": []}
        shelves = []
        for col in collections:
            name = col.get("name", "unknown")
            col_id = col.get("id", "")
            count_data = library_collector._http_get(f"{chroma_base}/collections/{col_id}/count")
            count = int(count_data) if count_data is not None else 0
            shelves.append({
                "name": name, "id": col_id, "count": count,
                "friendly_name": library_collector._friendly_name(name),
            })
        shelves.sort(key=lambda s: s["count"], reverse=True)
        return {"shelves": shelves}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do)


@router.get("/api/library/shelf/{collection_id}")
async def library_shelf(collection_id: str):
    """Get documents from a ChromaDB collection."""
    config = srv._config or load_config()
    wc = config.get("widgets", {}).get("config", {}).get("library", {})
    chromadb_url = wc.get("chromadb_url", "").rstrip("/")
    if not chromadb_url:
        return JSONResponse(status_code=400, content={"error": "No ChromaDB configured"})
    loop = asyncio.get_running_loop()
    docs = await loop.run_in_executor(
        None, library_collector.get_shelf_documents, chromadb_url, collection_id
    )
    return {"documents": docs}


@router.post("/api/search")
async def web_search(q: str = Query(...)):
    """Search via a configured SearXNG instance and return results."""
    base = load_config().get("searxng_url")
    if not base:
        return {"results": [], "disabled": True}

    def _do_search():
        try:
            url = f"{base.rstrip('/')}/search?q={urllib.request.quote(q)}&format=json"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            results = []
            for r in (data.get("results") or [])[:5]:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                })
            # Direct answers / infoboxes (conversions, facts, sometimes weather)
            answers = [a for a in (data.get("answers") or []) if a]
            infoboxes = []
            for ib in (data.get("infoboxes") or [])[:2]:
                txt = ib.get("content") or ""
                if txt:
                    infoboxes.append(txt)
            return {"results": results, "answers": answers, "infoboxes": infoboxes}
        except Exception as e:
            return {"error": str(e), "results": []}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_search)


# ── Press Room article detail ─────────────────────────────────────────────────

@router.get("/api/press-room/article/{article_id}")
async def press_room_article(article_id: str):
    """Return full content for a single Press Room article."""
    import sqlite3 as _sq3
    _db = srv._pr_db()
    def _fetch():
        if not _db or not _db.exists():
            return {"error": "articles DB not configured"}
        try:
            con = _sq3.connect(f"file:{_db}?mode=ro", uri=True, timeout=2)
            con.row_factory = _sq3.Row
            row = con.execute(
                "SELECT id, title, hook, analysis, predictions, signal_card, topic_display, created_at FROM articles WHERE id=?",
                (article_id,)
            ).fetchone()
            con.close()
            if not row:
                return {"error": "not found"}
            return {
                "id": row["id"],
                "title": row["title"] or "",
                "hook": row["hook"] or "",
                "analysis": row["analysis"] or "",
                "predictions": row["predictions"] or "",
                "signal_card": row["signal_card"] or "",
                "topic": row["topic_display"] or "",
                "created_at": row["created_at"] or "",
            }
        except Exception as e:
            return {"error": str(e)}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


# ── Press Room draft generation ──────────────────────────────────────────────

@router.post("/api/press-room/draft/{article_id}")
async def press_room_draft(article_id: str):
    """Generate an X post draft from an article via an optional Claude proxy, falling back to the first configured LLM backend."""
    import sqlite3 as _sq3
    _db = srv._pr_db()
    _cfg = load_config()
    _claude_proxy = _cfg.get("claude_proxy_url")
    _backends = _cfg.get("backends") or []
    _backend_url = _backends[0].get("url") if _backends else None

    def _fetch_and_draft():
        # Fetch article
        if not _db or not _db.exists():
            return {"error": "articles DB not configured"}
        try:
            con = _sq3.connect(f"file:{_db}?mode=ro", uri=True, timeout=2)
            con.row_factory = _sq3.Row
            row = con.execute(
                "SELECT title, hook, analysis FROM articles WHERE id=?", (article_id,)
            ).fetchone()
            con.close()
            if not row:
                return {"error": "article not found"}
            title    = row["title"] or ""
            hook     = row["hook"] or ""
            analysis = (row["analysis"] or "")[:600]
        except Exception as e:
            return {"error": str(e)}

        prompt = (
            "Write a single X (Twitter) post about this intelligence article. "
            "Maximum 280 characters. Be specific and factual — lead with the most important fact. "
            "No hashtags. No emojis. No filler. Return only the post text, nothing else.\n\n"
            f"Title: {title}\n"
            + (f"Summary: {hook}\n" if hook else "")
            + (f"Detail: {analysis}\n" if analysis else "")
        )

        # Optionally try a Claude proxy first, then fall back to the LLM backend
        draft = None
        model_used = "Claude"
        if _claude_proxy:
            try:
                payload = json.dumps({"prompt": prompt, "timeout": 90}).encode()
                req = urllib.request.Request(
                    _claude_proxy,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                resp = urllib.request.urlopen(req, timeout=100)
                result = json.loads(resp.read().decode())
                if result.get("returncode", 1) == 0:
                    draft = (result.get("output") or "").strip() or None
            except Exception:
                pass

        if not draft:
            # Fall back to the first configured LLM backend
            if not _backend_url:
                return {"error": "No LLM backend configured for drafts"}
            model_used = "Backend"
            try:
                hp = json.dumps({
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 160,
                    "temperature": 0.7,
                    "chat_template_kwargs": {"enable_thinking": False},
                }).encode()
                hreq = urllib.request.Request(
                    _backend_url.rstrip("/") + "/v1/chat/completions",
                    data=hp,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                hresp = urllib.request.urlopen(hreq, timeout=30)
                hresult = json.loads(hresp.read().decode())
                raw = hresult["choices"][0]["message"]["content"].strip()
                if "<think>" in raw:
                    raw = raw.split("</think>")[-1].strip()
                draft = raw
            except Exception as e:
                return {"error": f"Draft generation failed: {e}"}

        return {"draft": draft, "model": model_used}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_and_draft)


# ── Predictions (optional PostgreSQL source) ──────────────────────────────────

@router.get("/api/predictions")
async def get_predictions(limit: int = 50):
    """Return recent predictions from a configured PostgreSQL source.

    Reads the full DSN (which may embed credentials) from the `predictions_dsn`
    config key. When unset, the predictions panel is simply disabled — no
    connection is attempted and no credentials live in source.
    """
    dsn = load_config().get("predictions_dsn")
    if not dsn:
        return {"predictions": [], "disabled": True}
    pw = os.environ.get("PREDICTIONS_DB_PASSWORD")  # optional, no hardcoded fallback

    def _fetch():
        try:
            import psycopg2
            kwargs = {"connect_timeout": 4}
            if pw:
                kwargs["password"] = pw
            conn = psycopg2.connect(dsn, **kwargs)
            cur = conn.cursor()
            cur.execute("""
                SELECT id::text, prediction_text, domain, geography,
                       timeframe, confidence_score, verified, generated_at,
                       brief, reasoning
                FROM predictions
                ORDER BY generated_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
            result = []
            for r in rows:
                result.append({
                    "id": r[0],
                    "text": r[1],
                    "domain": r[2] or "general",
                    "geography": r[3] or "",
                    "timeframe": r[4].isoformat() if r[4] else None,
                    "confidence": float(r[5]) if r[5] is not None else None,
                    "verified": r[6],
                    "generated_at": r[7].isoformat() if r[7] else "",
                    "brief": r[8] or "",
                    "reasoning": r[9] or "",
                })
            return {"predictions": result}
        except Exception as e:
            return {"predictions": [], "error": str(e)}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch)


# ── File transfer (upload to inbox, download from share) ──────────────────────

def _inbox_dir() -> Path:
    p = load_config().get("inbox_path", "~/inbox")
    return Path(os.path.expanduser(p))


def _share_dir() -> Path:
    p = load_config().get("share_path")
    if p:
        return Path(os.path.expanduser(p))
    return _inbox_dir() / "llamawatch-share"


_MAX_UPLOAD_MB = 100


@router.post("/api/files/upload")
async def files_upload(request: Request):
    """Accept multipart file upload and save to ~/inbox/."""
    import aiofiles
    form = await request.form()
    saved = []
    errors = []
    for field_name, field in form.multi_items():
        if not hasattr(field, "filename") or not field.filename:
            continue
        # Sanitise filename — strip path components
        safe_name = Path(field.filename).name
        if not safe_name:
            continue
        dest = _inbox_dir() / safe_name
        try:
            content = await field.read()
            if len(content) > _MAX_UPLOAD_MB * 1024 * 1024:
                errors.append(f"{safe_name}: exceeds {_MAX_UPLOAD_MB}MB limit")
                continue
            async with aiofiles.open(dest, "wb") as f:
                await f.write(content)
            saved.append(safe_name)
        except Exception as e:
            errors.append(f"{safe_name}: {e}")
    return {"saved": saved, "errors": errors}


@router.get("/api/files/list")
async def files_list():
    """List files in the share directory available for download."""
    share = _share_dir()
    share.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(share.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            st = p.stat()
            files.append({
                "name": p.name,
                "size": st.st_size,
                "modified": int(st.st_mtime),
            })
    return {"files": files}


@router.get("/api/files/download/{filename}")
async def files_download(filename: str):
    """Serve a file from the share directory for download."""
    safe = Path(filename).name
    path = _share_dir() / safe
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(path), filename=safe, media_type="application/octet-stream")


@router.delete("/api/files/share/{filename}")
async def files_delete(filename: str, request: Request):
    """Delete a file from the share directory."""
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted"}, status_code=403)
    safe = Path(filename).name
    path = _share_dir() / safe
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    return {"deleted": safe}
