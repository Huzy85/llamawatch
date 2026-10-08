"""Research endpoints: pick a model, approve the cost, start a run, follow it,
read the report and the pages behind it, make social drafts.

One run at a time: a run uses the web and the model for many minutes, and a
second run would slow both down for little gain.
"""

import asyncio
import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import srv
from .. import audit, security
from ..auth import is_auth_enabled
from ..config import encrypt_secrets, get_config_dir, load_config, reload_config
from ..research import prompts as P
from ..research.llm import Model, ModelError, Usage, chat_url, estimate, spec_from_adapter, spec_from_config
from ..research.pipeline import DEPTH_NOTE, DEPTHS, ResearchRun
from ..research.rerank import Reranker
from ..research.store import RunStore, list_runs
from ..research.style import prompt_rules, style_problems, tidy
from ..research.trust import GENERAL, model_note
from ..research.web import Reader, Searcher

router = APIRouter()

_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_PAGE_ID = re.compile(r"^P[0-9]{1,5}$")
_active: dict[str, ResearchRun] = {}
_active_lock = threading.Lock()
_STATIC = Path(__file__).resolve().parent.parent / "static"


def _cfg() -> dict:
    return load_config().get("research", {}) or {}


def _root() -> Path:
    d = _cfg().get("data_dir") or str(Path.home() / ".local" / "share" / "llamawatch" / "research")
    p = Path(d).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _models() -> dict:
    """Every model a run can use: watched local backends, then configured APIs."""
    out = {}
    skip = set(_cfg().get("exclude") or [])      # watched backends kept for other work
    if srv._adapters is not None:
        for name, ad in srv._adapters.adapters.items():
            if name not in skip:
                out[name] = ("adapter", name, ad)
    for entry in _cfg().get("models", []) or []:
        if entry.get("base_url") and entry.get("model"):
            out[entry.get("name") or entry["model"]] = ("config", entry, None)
    return out


def _spec(name: str):
    m = _models().get(name)
    if not m:
        return None
    kind, a, b = m
    return spec_from_adapter(a, b) if kind == "adapter" else spec_from_config(a)


def _deny():
    return JSONResponse({"error": "not permitted"}, status_code=403)


def _store(run_id: str) -> RunStore | None:
    if not _RUN_ID.match(run_id or "") or not (_root() / run_id / "run.json").exists():
        return None
    return RunStore(_root(), run_id)


@router.get("/api/research/models")
async def research_models():
    def _do():
        rows = []
        for name, (kind, a, _) in _models().items():
            if kind == "adapter":
                sp = spec_from_adapter(a, _)
                rows.append({"name": name, "local": True, "paid": False,
                             **model_note(f"{name} {sp.model}", sp.size, sp.paid)})
            else:
                rows.append({"name": name, "local": not a.get("api_key"),
                             **model_note(f"{name} {a.get('model', '')}", a.get("size_class", ""),
                                          bool(float(a.get("price_in", 0) or 0) or float(a.get("price_out", 0) or 0))),
                             "paid": bool(a.get("price_in") or a.get("price_out")),
                             "added": bool(a.get("added")),
                             "price_in": a.get("price_in", 0), "price_out": a.get("price_out", 0),
                             "currency": a.get("currency", "")})
        return {"models": rows, "warning": GENERAL, "default": _cfg().get("default_model") or (rows[0]["name"] if rows else ""),
                "depths": [{"id": k, "label": v["label"], "time": v["time"], "about": v["about"]}
                           for k, v in DEPTHS.items()],
                "depth_note": DEPTH_NOTE}
    return await asyncio.to_thread(_do)


@router.post("/api/research/estimate")
async def research_estimate(request: Request):
    body = await request.json()
    spec = await asyncio.to_thread(_spec, body.get("model", ""))
    if spec is None:
        return JSONResponse({"error": "unknown model"}, status_code=404)
    return {**estimate(spec, body.get("depth", "quick")), **model_note(f"{spec.name} {spec.model}", spec.size, spec.paid)}


_NAME = re.compile(r"^[\w .()+-]{1,40}$")


def _save_models(change) -> None:
    """Apply change(list) to research.models in config.local.json, keys encrypted."""
    path = get_config_dir() / "config.local.json"
    local = json.loads(path.read_text()) if path.is_file() else {}
    research = local.setdefault("research", {})
    research["models"] = change(list(research.get("models") or []))
    path.write_text(json.dumps(encrypt_secrets(local), indent=2) + "\n")
    os.chmod(str(path), 0o600)
    srv._config = reload_config(config_dir=get_config_dir())


def _try_model(entry: dict) -> str:
    """One tiny call so a wrong address, model name or key shows up now, not
    ten minutes into a run. Returns "" when it worked, otherwise the reason."""
    import httpx
    headers = {"Content-Type": "application/json"}
    if entry.get("api_key"):
        headers["Authorization"] = f"Bearer {entry['api_key']}"
    body = {"model": entry["model"], "max_tokens": 16, "stream": False,
            "messages": [{"role": "user", "content": "Reply with the word ok."}]}
    try:
        r = httpx.post(chat_url(entry["base_url"]), json=body, headers=headers, timeout=45)
    except httpx.HTTPError as e:
        return f"could not reach {entry['base_url']}: {type(e).__name__}"
    if r.status_code >= 400:
        text = r.text[:200].replace(entry.get("api_key") or "\0", "[key]")
        return f"the API answered {r.status_code}: {text}"
    try:
        r.json()["choices"][0]
    except Exception:
        return "the address answered, but not like a chat API (no choices in the reply)"
    return ""


@router.post("/api/research/models")
async def research_add_model(request: Request):
    """Add an online API (any OpenAI-style chat endpoint) as a research model."""
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    body = await request.json()
    name = (body.get("name") or "").strip()
    base = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    key = (body.get("api_key") or "").strip()
    if not _NAME.match(name):
        return JSONResponse({"error": "give it a short name: letters, numbers, spaces, up to 40"}, status_code=400)
    if name in _models():
        return JSONResponse({"error": f"there is already a model called {name}"}, status_code=400)
    if not re.match(r"^https?://[^\s/]+", base):
        return JSONResponse({"error": "the API address must start with https://"}, status_code=400)
    if not model or len(model) > 200:
        return JSONResponse({"error": "enter the model name the API expects"}, status_code=400)
    try:
        price_in = max(0.0, float(body.get("price_in") or 0))
        price_out = max(0.0, float(body.get("price_out") or 0))
        context = int(body.get("context") or 128000)
    except (TypeError, ValueError):
        return JSONResponse({"error": "prices and context must be numbers"}, status_code=400)
    entry = {"name": name, "base_url": base, "model": model,
             "price_in": price_in, "price_out": price_out,
             "currency": (body.get("currency") or "$").strip()[:3],
             "context": max(8192, min(context, 2_000_000)), "size_class": "large", "added": True}
    if key:
        entry["api_key"] = key
    problem = await asyncio.to_thread(_try_model, entry)
    if problem:
        return JSONResponse({"error": "Not saved. Test call failed: " + problem}, status_code=400)
    await asyncio.to_thread(_save_models, lambda ms: ms + [entry])
    return {"ok": True, "name": name}


@router.post("/api/research/models/remove")
async def research_remove_model(request: Request):
    """Remove an online API added from the Research page."""
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    name = ((await request.json()).get("name") or "").strip()
    m = _models().get(name)
    if not m or m[0] != "config" or not m[1].get("added"):
        return JSONResponse({"error": "only APIs added here can be removed here"}, status_code=400)
    await asyncio.to_thread(_save_models, lambda ms: [x for x in ms if x.get("name") != name])
    return {"ok": True}


@router.post("/api/research/start")
async def research_start(request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    body = await request.json()
    question = (body.get("question") or "").strip()
    if len(question) < 8 or len(question) > 2000:
        return JSONResponse({"error": "ask a question of 8 to 2,000 characters"}, status_code=400)
    depth = body.get("depth", "quick")
    name = body.get("model") or _cfg().get("default_model", "")
    spec = await asyncio.to_thread(_spec, name)
    if spec is None:
        return JSONResponse({"error": "unknown model"}, status_code=404)
    vname = body.get("verifier") or _cfg().get("verifier_model", "")
    vspec = await asyncio.to_thread(_spec, vname) if vname and vname != name else None

    # paid models need the user to approve a spending cap first
    cap = None
    paid = [s for s in (spec, vspec) if s and s.paid]
    if paid:
        est = sum(estimate(s, depth)["cost"] for s in paid)
        approved = body.get("approved_cost")
        if approved is None:
            return JSONResponse({"error": "approval needed", "estimate": round(est, 2)}, status_code=402)
        cap = float(approved)
        if cap <= 0:
            return JSONResponse({"error": "the spending cap must be above zero"}, status_code=400)

    with _active_lock:
        if any(r.store.meta.get("status") == "running" for r in _active.values()):
            return JSONResponse({"error": "a research run is already going"}, status_code=409)
        cfg = _cfg()
        top = load_config()
        search_cfg = {"searxng_url": top.get("searxng_url", ""), **(cfg.get("search") or {})}
        reader_cfg = dict(cfg.get("reader") or {})
        search_cfg.setdefault("webclaw_path", reader_cfg.get("webclaw_path", ""))
        usage = Usage()
        model = Model(spec, cap=cap, usage=usage)
        verifier = Model(vspec, cap=cap, usage=usage) if vspec else None
        store = RunStore(_root())
        run = ResearchRun(question, depth, model, store, Searcher(search_cfg),
                          Reader(reader_cfg, cache_dir=_root() / "_cache"), verifier=verifier,
                          context=body.get("context", ""), workers=int(cfg.get("workers", 3)),
                          reranker=Reranker.from_config(cfg))
        store.set_meta(question=question, status="running")
        _active[store.run_id] = run
    threading.Thread(target=_run_safely, args=(run,), daemon=True,
                     name=f"research-{store.run_id}").start()
    audit.append("research_start", target=question[:80], outcome="ok",
                 actor="local" if not is_auth_enabled() else "session",
                 id=store.run_id, model=name, depth=depth, cap=cap)
    return {"id": store.run_id, "cap": cap}


def _run_safely(run: ResearchRun):
    try:
        run.run()
    except Exception as e:  # never leave a run marked as running
        run.store.set_meta(status=f"failed: {e}")
        run.store.event(stage="run", msg=f"Failed: {e}", level="error")
    finally:
        with _active_lock:
            _active.pop(run.store.run_id, None)


@router.get("/api/research/runs")
async def research_runs():
    return {"runs": await asyncio.to_thread(list_runs, _root())}


@router.get("/api/research/runs/{run_id}")
async def research_run(run_id: str):
    s = await asyncio.to_thread(_store, run_id)
    if s is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    drafts, sources = s.read("drafts.json"), s.read("sources.json")
    return {"id": run_id, "meta": s.meta, "report": s.read("report.md"),
            "sources": json.loads(sources) if sources else {},
            "drafts": json.loads(drafts) if drafts else None}


@router.get("/api/research/runs/{run_id}/events")
async def research_events(run_id: str, since: int = 0):
    s = await asyncio.to_thread(_store, run_id)
    if s is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    ev = s.events(max(0, since))
    return {"events": ev, "next": max(0, since) + len(ev), "status": s.meta.get("status")}


@router.get("/api/research/runs/{run_id}/pages/{page_id}")
async def research_page(run_id: str, page_id: str):
    """The page text exactly as the run read it, so any citation can be traced."""
    s = await asyncio.to_thread(_store, run_id)
    if s is None or not _PAGE_ID.match(page_id) or page_id not in s.pages:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = s.pages[page_id]
    return PlainTextResponse(f"{p.title}\n{p.url}\nRead {p.read_at} via {p.retrieval}\n\n{s.page_text(page_id)}")


@router.post("/api/research/runs/{run_id}/cancel")
async def research_cancel(run_id: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    run = _active.get(run_id)
    if not run:
        return JSONResponse({"error": "not running"}, status_code=404)
    run.cancel.set()
    return {"cancelling": run_id}


@router.post("/api/research/runs/{run_id}/drafts")
async def research_drafts(run_id: str, request: Request):
    """Short and long social posts from the finished report. On demand only."""
    if not security.action_allowed(request, is_auth_enabled()):
        return _deny()
    s = await asyncio.to_thread(_store, run_id)
    if s is None or not s.read("report.md"):
        return JSONResponse({"error": "no report"}, status_code=404)
    body = await request.json()
    spec = await asyncio.to_thread(_spec, body.get("model") or s.meta.get("model") or "")
    if spec is None:
        return JSONResponse({"error": "unknown model"}, status_code=404)
    if spec.paid and not body.get("approved"):
        return JSONResponse({"error": "approval needed", "estimate": 0.05}, status_code=402)
    report = s.read("report.md").split("## How this was researched")[0]

    def _do():
        m = Model(spec, cap=float(body.get("approved_cost", 0.25)) if spec.paid else None)
        prompt = P.DRAFTS.format(report=report[:30000], style=prompt_rules())
        out = tidy(m.chat(prompt, system=P.SYSTEM, max_tokens=900, temperature=0.5, stage="drafts"))
        problems = style_problems(out)
        if problems:   # one more try; keep it only if it is cleaner
            fix = "\n\nYour last drafts broke these rules. Write both again without them:\n" + \
                  "\n".join(f"- {p[7:]}" for p in problems)
            out2 = tidy(m.chat(prompt + fix, system=P.SYSTEM, max_tokens=900, temperature=0.4, stage="drafts"))
            if "SHORT:" in out2 and len(style_problems(out2)) < len(problems):
                out = out2
        short = re.search(r"SHORT:\s*(.*?)\s*LONG:", out, re.S)
        long_ = re.search(r"LONG:\s*(.*)", out, re.S)
        d = {"short": short.group(1).strip() if short else "",
             "long": long_.group(1).strip() if long_ else "", "model": spec.name}
        s.write("drafts.json", json.dumps(d, ensure_ascii=False))
        return d
    try:
        return await asyncio.to_thread(_do)
    except ModelError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ── Pages ────────────────────────────────────────────────────────────
@router.get("/research")
async def research_home():
    # no-cache: the page names its script versions, so a stale copy loads old code
    return FileResponse(str(_STATIC / "research.html"), headers={"Cache-Control": "no-cache"})


def _script_safe(text: str) -> str:
    """Text placed inside a <script> block must not be able to close it."""
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.I)


@router.get("/research/runs/{run_id}/report")
async def research_report_page(run_id: str):
    """The finished report in the reading view: the report template with this run's data."""
    def _do():
        s = _store(run_id)
        if s is None:
            return None
        report = s.read("report.md")
        if not report:
            return "missing"
        m = s.meta
        sources, drafts = s.read("sources.json"), s.read("drafts.json")
        data = {"runId": run_id,
                "run": {"question": m.get("question", ""), "model": m.get("model", ""),
                        "depth": m.get("depth", ""), "createdAt": m.get("started", ""),
                        "runSeconds": m.get("seconds") or 0, "cost": m.get("cost") or 0,
                        "trust": m.get("trust"), "modelNote": m.get("model_note")},
                "sources": json.loads(sources) if sources else {},
                "next": m.get("next_questions") or [],
                "drafts": json.loads(drafts) if drafts else None}
        page = (_STATIC / "research-report.html").read_text(encoding="utf-8")
        md_open = '<script type="text/markdown" id="reportMd">'
        a = page.index(md_open) + len(md_open)
        b = page.index("</script>", a)
        page = page[:a] + "\n" + _script_safe(report) + "\n" + page[b:]
        page = page.replace('<script type="application/json" id="reportData">{}</script>',
                            '<script type="application/json" id="reportData">'
                            + _script_safe(json.dumps(data, ensure_ascii=False)) + "</script>", 1)
        return page
    page = await asyncio.to_thread(_do)
    if page is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if page == "missing":
        return RedirectResponse(url=f"/research#run/{run_id}", status_code=303)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})
