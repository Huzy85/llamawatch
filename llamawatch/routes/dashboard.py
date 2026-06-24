"""Core dashboard read endpoints: widgets manifest, models, layout."""

import json

from fastapi import APIRouter, Request

from . import srv
from ..config import get_config_dir
from ..layout import migrate_layout

router = APIRouter()


@router.get("/api/widgets")
async def get_widgets():
    """Return the manifest of all discovered collectors/widgets."""
    if srv._collector_registry is None:
        return []
    return srv._collector_registry.get_manifest()


@router.get("/api/models")
async def get_models():
    """Return available models from the adapter registry."""
    if srv._adapters is None:
        return {"models": []}
    models = []
    for adapter in srv._adapters.get_all():
        name = adapter.config.get("name", "")
        health_status = "unknown"
        model_id = ""
        try:
            health_status = adapter.health()
            model_id = adapter.model_name()
        except Exception:
            pass
        models.append({
            "name": name,
            "model_id": model_id,
            "friendly_name": adapter.model_friendly_name() if model_id else name,
            "health": health_status,
            "url": adapter.url,
            # Explicit per-backend context window (tokens) for the chat meter.
            # None when unset → the chat shows a plain token count instead.
            "context_window": adapter.config.get("context_window"),
        })
    return {"models": models}


@router.get("/api/layout")
async def get_layout():
    """Return the saved widget layout as a board structure, migrating legacy formats."""
    layout_path = get_config_dir() / "layout.json"
    if layout_path.exists():
        return migrate_layout(json.loads(layout_path.read_text()))
    return migrate_layout({"widgets": []})


@router.post("/api/layout")
async def save_layout(request: Request):
    """Save the widget layout to the config directory."""
    data = await request.json()
    layout_path = get_config_dir() / "layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(json.dumps(data, indent=2))
    return {"status": "ok"}
