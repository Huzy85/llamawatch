"""Press Room collector — reads an intelligence/news articles DB.

Reads the SQLite articles DB configured as press_room_db (read-only, short timeout).
Returns {} gracefully if the DB is missing or errors.
"""

import os
import sqlite3
import time
from pathlib import Path

WIDGET_ID = "press-room"
WIDGET_NAME = "Press Room"
WIDGET_ICON = "📰"
WIDGET_DESCRIPTION = "Recent intelligence/news articles from the configured database"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False
WIDGET_CONFIG_REQUIRED = False

_ARTICLE_LIMIT = 15


def _db_path(config=None):
    """Articles DB path from config (press_room_db), or None when unset."""
    p = (config or {}).get("press_room_db") if config else None
    if not p:
        try:
            from ..config import load_config
            p = load_config().get("press_room_db")
        except Exception:
            p = None
    return Path(os.path.expanduser(p)) if p else None


def _fmt_when(iso_str: str) -> str:
    """Convert ISO8601 string to a short human age like '2h ago'."""
    if not iso_str:
        return ""
    try:
        # Strip timezone indicator for simple parsing
        clean = iso_str.split("+")[0].replace("Z", "").replace("T", " ")
        ts = time.mktime(time.strptime(clean[:19], "%Y-%m-%d %H:%M:%S"))
        delta = int(time.time()) - int(ts)
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"
    except Exception:
        return iso_str[:10] if iso_str else ""


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return recent Press Room articles, or {} on any failure."""
    db_path = _db_path(config)
    if not db_path or not db_path.exists():
        return {}

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        rows = cur.execute(
            """
            SELECT id, title, topic_key, tier, created_at, last_written_at, is_read
            FROM articles
            ORDER BY COALESCE(last_written_at, created_at) DESC
            LIMIT ?
            """,
            (_ARTICLE_LIMIT,),
        ).fetchall()

        total = cur.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        con.close()

        articles = []
        for row in rows:
            title = row["title"] or "(untitled)"
            # Derive a clean topic label from topic_key (strip "topic:" prefix)
            topic = (row["topic_key"] or "").replace("topic:", "").replace("_", " ")
            tier = row["tier"] or 0
            status = "tier-" + str(tier) if tier else "article"
            effective_ts = row["last_written_at"] or row["created_at"]
            articles.append({
                "id": row["id"],
                "title": title,
                "topic": topic,
                "status": status,
                "when": _fmt_when(effective_ts),
                "is_read": bool(row["is_read"]),
            })

        return {
            "articles": articles,
            "total": total,
        }

    except Exception:
        return {}
