"""Email collector — IMAP inbox check, cached for 5 minutes."""

WIDGET_ID = "email"
WIDGET_NAME = "Email"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "📧"
WIDGET_DESCRIPTION = "IMAP inbox monitor"
WIDGET_CONFIG_SCHEMA = [
    {"key": "check_interval_min", "label": "Check interval (minutes)", "type": "number", "default": 5},
    {"key": "max_emails", "label": "Max emails to show", "type": "number", "default": 10},
]
WIDGET_CREDENTIALS_REQUIRED = True
WIDGET_CREDENTIALS_HELP = "Add to config.local.json under 'email': imap_host, imap_port, username, password, use_ssl"
WIDGET_MULTI_INSTANCE = False

import email
import email.header
import email.utils
import imaplib
import json
import os
import time

_cache: dict = {"data": None, "ts": 0}
_CACHE_TTL = 300  # 5 minutes

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

_NOT_CONFIGURED = {"status": "not_configured", "unread_count": 0, "recent": []}


def _decode_header(raw: str) -> str:
    """Decode an RFC 2047 encoded header value."""
    parts = email.header.decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _extract_name(from_header: str) -> str:
    """Extract the display name from a From header, falling back to email."""
    decoded = _decode_header(from_header)
    name, addr = email.utils.parseaddr(decoded)
    return name if name else addr


def _get_preview(mail: imaplib.IMAP4_SSL | imaplib.IMAP4, msg_id: bytes) -> str:
    """Try to get a short plain-text preview of the message body."""
    import email as email_mod
    import re
    try:
        status, data = mail.fetch(msg_id, "(BODY.PEEK[]<0.16000>)")
        if status != "OK" or not data or not data[0] or not isinstance(data[0], tuple):
            return ""
        raw = data[0][1]
        msg = email_mod.message_from_bytes(raw)
        # Walk parts looking for plain text
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    # Skip PGP encrypted blocks
                    if "-----BEGIN PGP MESSAGE-----" in text:
                        continue
                    text = re.sub(r'<[^>]+>', '', text)
                    text = " ".join(text.split())
                    if len(text) > 10:
                        return text[:120]
            elif ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode("utf-8", errors="replace")
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'&[a-z]+;', ' ', text)
                    text = " ".join(text.split())
                    if len(text) > 10:
                        return text[:120]
    except Exception:
        pass
    return ""


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect email data — registry-compatible entry point."""
    wc = widget_config or {}
    check_interval = wc.get("check_interval_min", 5)
    max_emails = wc.get("max_emails", 10)
    # Credentials come from config root: config.get("email", {})
    email_cfg = (config or {}).get("email") if config else None
    return collect_email(email_cfg=email_cfg, check_interval=check_interval, max_emails=max_emails)


def collect_email(email_cfg=None, check_interval: int = 5, max_emails: int = 10) -> dict:
    """Return unread count and recent emails from IMAP inbox.

    Args:
        email_cfg: dict with IMAP credentials (from config root "email" key). If None,
                   falls back to reading config.json from disk for backwards compatibility.
        check_interval: cache TTL in minutes (from widget_config).
        max_emails: max number of recent emails to return (from widget_config).
    """
    now = time.time()
    cache_ttl = check_interval * 60

    # Return cached data if still fresh
    if _cache["data"] is not None and (now - _cache["ts"]) < cache_ttl:
        return _cache["data"]

    # Fall back to loading config from disk if no email_cfg provided
    if email_cfg is None:
        try:
            with open(_CONFIG_PATH, "r") as f:
                file_config = json.load(f)
        except Exception:
            return _NOT_CONFIGURED
        email_cfg = file_config.get("email")

    if not email_cfg or not email_cfg.get("enabled"):
        return _NOT_CONFIGURED

    imap_host = email_cfg.get("imap_host", "")
    imap_port = email_cfg.get("imap_port", 993)
    username = email_cfg.get("username", "")
    password = email_cfg.get("password", "")
    use_ssl = email_cfg.get("use_ssl", True)

    if not imap_host or not username or not password:
        return _NOT_CONFIGURED

    mail = None
    try:
        # Connect
        if use_ssl:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            mail = imaplib.IMAP4(imap_host, imap_port)
            try:
                mail.starttls()
            except Exception:
                pass  # Server may not support STARTTLS — continue anyway

        mail.login(username, password)
        mail.select("INBOX", readonly=True)

        # Get unread count
        status, unseen_data = mail.search(None, "UNSEEN")
        unread_ids = unseen_data[0].split() if status == "OK" and unseen_data[0] else []
        unread_count = len(unread_ids)

        # Fetch most recent emails (by sequence number, newest first)
        status, all_data = mail.search(None, "ALL")
        all_ids = all_data[0].split() if status == "OK" and all_data[0] else []
        recent_ids = all_ids[-max_emails:] if len(all_ids) >= max_emails else all_ids
        recent_ids.reverse()  # newest first

        recent = []
        for msg_id in recent_ids:
            # Fetch headers only
            status, msg_data = mail.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_headers = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
            msg = email.message_from_bytes(raw_headers)

            from_name = _extract_name(msg.get("From", ""))
            subject = _decode_header(msg.get("Subject", "(no subject)"))

            # Parse date
            date_str = msg.get("Date", "")
            date_tuple = email.utils.parsedate_to_datetime(date_str) if date_str else None
            date_iso = date_tuple.isoformat() if date_tuple else ""

            # Get preview
            preview = _get_preview(mail, msg_id)

            recent.append({
                "uid": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                "from": from_name,
                "subject": subject,
                "date": date_iso,
                "preview": preview,
            })

        # Sort by date, newest first
        recent.sort(key=lambda e: e.get("date", ""), reverse=True)

        result = {
            "status": "connected",
            "unread_count": unread_count,
            "recent": recent,
        }

        _cache["data"] = result
        _cache["ts"] = now
        return result

    except Exception as e:
        # Return last cached value if available
        if _cache["data"] is not None:
            return _cache["data"]
        return {
            "status": "error",
            "unread_count": 0,
            "recent": [],
            "error": str(e),
        }
    finally:
        if mail:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass
