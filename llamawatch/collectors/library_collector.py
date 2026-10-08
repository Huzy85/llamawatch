"""Library / Knowledge Base collector — connects to ChromaDB or compatible vector store."""

WIDGET_ID = "library"
WIDGET_NAME = "Library"
WIDGET_ICON = "📚"
WIDGET_DESCRIPTION = "Knowledge base browser and search"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA = [
    {"key": "chromadb_url", "label": "ChromaDB URL", "type": "text",
     "placeholder": "http://localhost:8200", "description": "ChromaDB API endpoint"},
    {"key": "hub_url", "label": "Knowledge Hub URL (optional)", "type": "text",
     "placeholder": "http://localhost:8300", "description": "RAG knowledge hub for document search"},
    {"key": "hub_api_key_path", "label": "Hub API key file (optional)", "type": "text",
     "placeholder": "~/.config/llamawatch/hub.key",
     "description": "Path to API key file for the knowledge hub"},
]
WIDGET_CONFIG_REQUIRED = True
WIDGET_MULTI_INSTANCE = False

import json
import os
import urllib.request
import urllib.parse


def _read_key(path):
    """Read an API key from a file path, expanding ~."""
    if not path:
        return None
    try:
        expanded = os.path.expanduser(path)
        with open(expanded) as f:
            return f.read().strip()
    except Exception:
        return None


def _http_get(url, headers=None, timeout=10):
    """Simple GET returning parsed JSON or None."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _http_post(url, payload, headers=None, timeout=10):
    """Simple POST returning parsed JSON or None."""
    try:
        data = json.dumps(payload).encode()
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return library overview — collection list with counts."""
    wc = widget_config or {}
    chromadb_url = wc.get("chromadb_url", "").rstrip("/")
    hub_url = wc.get("hub_url", "").rstrip("/")

    if not chromadb_url and not hub_url:
        return {"error": "No knowledge base configured", "configured": False}

    result = {
        "configured": True,
        "shelves": [],
        "hub_available": False,
        "chromadb_available": False,
    }

    # ChromaDB shelves (collections)
    if chromadb_url:
        chroma_base = f"{chromadb_url}/api/v2/tenants/default_tenant/databases/default_database"
        collections = _http_get(f"{chroma_base}/collections")
        if collections is not None:
            result["chromadb_available"] = True
            for col in collections:
                name = col.get("name", "unknown")
                col_id = col.get("id", "")
                # Get count
                count_data = _http_get(f"{chroma_base}/collections/{col_id}/count")
                count = int(count_data) if count_data is not None else 0
                result["shelves"].append({
                    "name": name,
                    "id": col_id,
                    "count": count,
                    "friendly_name": _friendly_name(name),
                })
            result["shelves"].sort(key=lambda s: s["count"], reverse=True)

    # Knowledge Hub availability check
    if hub_url:
        health = _http_get(f"{hub_url}/health")
        if health is not None:
            result["hub_available"] = True

    return result


def search_hub(hub_url, query, api_key=None):
    """Search the knowledge hub RAG. Returns list of results."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    url = f"{hub_url}/docs?q={urllib.parse.quote(query)}"
    data = _http_get(url, headers=headers)
    if data is None:
        return []
    return data.get("results", data if isinstance(data, list) else [])


def get_shelf_documents(chromadb_url, collection_id, limit=100):
    """Get documents from a ChromaDB collection."""
    chroma_base = f"{chromadb_url}/api/v2/tenants/default_tenant/databases/default_database"
    url = f"{chroma_base}/collections/{collection_id}/get"
    data = _http_post(url, {"limit": limit, "include": ["documents", "metadatas"]})
    if data is None:
        return []
    documents = []
    ids = data.get("ids", [])
    docs = data.get("documents", [])
    metas = data.get("metadatas", [])
    for i, doc_id in enumerate(ids):
        documents.append({
            "id": doc_id,
            "content": docs[i][:500] if i < len(docs) and docs[i] else "",
            "source": (metas[i] or {}).get("source", "") if i < len(metas) else "",
        })
    return documents


def _friendly_name(raw):
    """Convert collection name to a friendly display name."""
    known = {
        "library_docs": "Technical Documentation",
        "library_private": "Personal Knowledge",
        "library_private_careers": "Career Evidence",
    }
    if raw in known:
        return known[raw]
    if raw.startswith("web-search-"):
        return "Web Search: " + raw[11:19] + "..."
    return raw.replace("_", " ").replace("-", " ").title()
