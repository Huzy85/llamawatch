"""Single decision point for whether a dangerous (write) action is permitted."""
from .auth import validate_session  # re-exported so tests can monkeypatch here

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Headers a reverse proxy or tunnel adds when it relays a request
# (nginx, Caddy, Traefik, Cloudflare Tunnel, Tailscale Funnel, ...). Their
# presence means the loopback source address belongs to the proxy, not to a
# genuine local client — so the localhost gate must NOT trust it. Without this,
# binding to 127.0.0.1 and exposing through a proxy would make every request
# look local and silently open the terminal/shell actions with auth off.
_PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",
    "true-client-ip",
)


def _is_proxied(request) -> bool:
    """True if the request carries any reverse-proxy/tunnel forwarding header."""
    headers = getattr(request, "headers", None)
    if not headers:
        return False
    for name in _PROXY_HEADERS:
        try:
            if headers.get(name):
                return True
        except Exception:
            pass
    return False


def is_local_request(request) -> bool:
    """True only for a genuine loopback client that did NOT arrive via a proxy.

    A reverse proxy or tunnel connects from 127.0.0.1, so ``client.host`` alone
    can't prove locality. If the request carries any forwarding header it was
    relayed — treat it as remote (fail closed). This is the gate that makes
    "no password = localhost only" actually hold behind a proxy.
    """
    host = getattr(getattr(request, "client", None), "host", None)
    if host not in _LOCAL_HOSTS:
        return False
    return not _is_proxied(request)


# ── CSRF defence-in-depth ─────────────────────────────────────────────────────
# The session cookie is SameSite=Lax, which already stops it being sent on a
# cross-site state-changing request — that's the primary CSRF defence. As a
# second layer, reject a cookie-authenticated write whose Origin/Referer clearly
# belongs to a different site. Proxy-safe: if the app sees a loopback Host (a
# reverse proxy rewrote it) we can't compare reliably, so we allow and lean on
# SameSite. Non-browser clients (no Origin/Referer) are not CSRF vectors → allowed.
import ipaddress as _ipaddress
from urllib.parse import urlparse as _urlparse


def _host_only(netloc: str) -> str:
    """Hostname from a netloc/authority: strip userinfo, port, IPv6 brackets."""
    netloc = (netloc or "").split("@")[-1]
    if netloc.startswith("["):
        return netloc[1:].split("]")[0]
    return netloc.split(":")[0]


def _is_loopback_host(host: str) -> bool:
    if host in ("", "localhost"):
        return True
    try:
        return _ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def csrf_ok(request) -> bool:
    """True unless the request is a clear cross-origin browser write."""
    headers = getattr(request, "headers", None)
    if not headers:
        return True
    source = headers.get("origin", "") or headers.get("referer", "")
    if not source:
        return True  # no browser origin → curl/API client, not a CSRF vector
    host = _host_only(headers.get("host", ""))
    if _is_loopback_host(host):
        return True  # proxy rewrote Host to loopback — can't compare; rely on SameSite
    return _host_only(_urlparse(source).netloc) == host


def action_allowed(request, auth_enabled: bool) -> bool:
    if not auth_enabled:
        return is_local_request(request)
    token = request.cookies.get("lw_session", "")
    if not validate_session(token):
        return False
    return csrf_ok(request)


# ── Login rate limiting ───────────────────────────────────────────────────────
# Argon2 already makes each password check slow, but nothing caps the *rate* of
# attempts. A simple in-memory sliding-window limiter, keyed by client IP, stops
# an unbounded guessing flood. Buckets hold recent attempt timestamps per key.
import time as _time

_RATE_BUCKETS: dict[str, list[float]] = {}


def rate_limit(key: str, max_attempts: int, window_secs: float, now: float | None = None) -> bool:
    """Record an attempt for *key*; return True if allowed, False if over the limit.

    Sliding window: at most *max_attempts* within *window_secs*. A blocked attempt
    is NOT recorded, so the window drains and the key recovers. Pass *now* in tests
    to avoid real sleeps.
    """
    t = _time.monotonic() if now is None else now
    cutoff = t - window_secs
    bucket = [ts for ts in _RATE_BUCKETS.get(key, []) if ts > cutoff]
    if len(bucket) >= max_attempts:
        _RATE_BUCKETS[key] = bucket
        return False
    bucket.append(t)
    _RATE_BUCKETS[key] = bucket
    return True


def reset_rate_limits() -> None:
    """Clear all rate-limit buckets (test helper)."""
    _RATE_BUCKETS.clear()
