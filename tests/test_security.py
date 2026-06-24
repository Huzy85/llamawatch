"""Unit tests for llamawatch.security.action_allowed."""
from types import SimpleNamespace

import pytest

from llamawatch import security


def _req(host="127.0.0.1", session=False, headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        cookies={"lw_session": "t"} if session else {},
        headers=headers or {},
    )


def test_localhost_allowed_auth_off():
    assert security.action_allowed(_req("127.0.0.1"), auth_enabled=False) is True


def test_ipv6_loopback_allowed_auth_off():
    assert security.action_allowed(_req("::1"), auth_enabled=False) is True


def test_localhost_name_allowed_auth_off():
    assert security.action_allowed(_req("localhost"), auth_enabled=False) is True


def test_remote_blocked_auth_off():
    assert security.action_allowed(_req("203.0.113.50"), auth_enabled=False) is False


def test_remote_allowed_with_session_auth_on(monkeypatch):
    monkeypatch.setattr(security, "validate_session", lambda t: True)
    assert security.action_allowed(_req("203.0.113.50", session=True), auth_enabled=True) is True


def test_remote_blocked_without_session_auth_on(monkeypatch):
    monkeypatch.setattr(security, "validate_session", lambda t: False)
    assert security.action_allowed(_req("203.0.113.50"), auth_enabled=True) is False


def test_localhost_blocked_when_auth_on_but_no_session(monkeypatch):
    """With auth on, even localhost needs a valid session."""
    monkeypatch.setattr(security, "validate_session", lambda t: False)
    assert security.action_allowed(_req("127.0.0.1"), auth_enabled=True) is False


def test_no_client_blocked_auth_off():
    """Request with no client object is blocked when auth is off."""
    req = SimpleNamespace(client=None, cookies={}, headers={})
    assert security.action_allowed(req, auth_enabled=False) is False


# ── Reverse-proxy / tunnel bypass (the localhost gate must fail closed) ────────
# A proxy connects from 127.0.0.1, so client.host looks local. The presence of
# any forwarding header proves the request was relayed and must NOT be trusted
# as localhost when auth is off — otherwise nginx/Caddy/Cloudflare/Tailscale
# Funnel silently expose the terminal and shell actions.

def test_proxied_xforwardedfor_blocked_auth_off():
    req = _req("127.0.0.1", headers={"x-forwarded-for": "203.0.113.50"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_proxied_forwarded_rfc7239_blocked_auth_off():
    req = _req("127.0.0.1", headers={"forwarded": "for=203.0.113.50;proto=https"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_proxied_cloudflare_blocked_auth_off():
    req = _req("127.0.0.1", headers={"cf-connecting-ip": "203.0.113.50"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_proxied_real_ip_blocked_auth_off():
    req = _req("127.0.0.1", headers={"x-real-ip": "203.0.113.50"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_funnel_xforwardedproto_blocked_auth_off():
    """Tailscale Funnel sets X-Forwarded-Proto even when XFF is stripped."""
    req = _req("127.0.0.1", headers={"x-forwarded-proto": "https"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_proxied_ipv6_loopback_blocked_auth_off():
    req = _req("::1", headers={"x-forwarded-for": "203.0.113.50"})
    assert security.action_allowed(req, auth_enabled=False) is False


def test_genuine_localhost_no_headers_allowed_auth_off():
    """A direct loopback client with no forwarding headers is still trusted."""
    assert security.action_allowed(_req("127.0.0.1", headers={}), auth_enabled=False) is True


def test_proxied_localhost_allowed_with_session_auth_on(monkeypatch):
    """When auth is on, a valid session is the gate — proxy headers are irrelevant."""
    monkeypatch.setattr(security, "validate_session", lambda t: True)
    req = _req("127.0.0.1", session=True, headers={"x-forwarded-for": "203.0.113.50"})
    assert security.action_allowed(req, auth_enabled=True) is True


# ── is_local_request directly ─────────────────────────────────────────────────

def test_is_local_request_genuine_loopback():
    assert security.is_local_request(_req("127.0.0.1", headers={})) is True


def test_is_local_request_proxied_loopback():
    assert security.is_local_request(_req("127.0.0.1", headers={"x-forwarded-for": "1.2.3.4"})) is False


def test_is_local_request_remote():
    assert security.is_local_request(_req("203.0.113.50", headers={})) is False


def test_is_local_request_no_headers_attr():
    """A request object without a headers attribute is treated as not proxied."""
    req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert security.is_local_request(req) is True


# ── Login rate limiter ────────────────────────────────────────────────────────

def test_rate_limit_allows_up_to_max_then_blocks():
    security.reset_rate_limits()
    for i in range(5):
        assert security.rate_limit("k", max_attempts=5, window_secs=60, now=1000.0 + i) is True
    # 6th attempt within the window is blocked
    assert security.rate_limit("k", max_attempts=5, window_secs=60, now=1005.0) is False


def test_rate_limit_window_drains():
    security.reset_rate_limits()
    for _ in range(5):
        security.rate_limit("k", 5, 60, now=1000.0)
    assert security.rate_limit("k", 5, 60, now=1000.0) is False
    # once the window has fully passed, attempts are allowed again
    assert security.rate_limit("k", 5, 60, now=1061.0) is True


def test_rate_limit_blocked_attempt_not_recorded():
    """A blocked attempt must not extend the window (bucket only holds allowed)."""
    security.reset_rate_limits()
    for _ in range(5):
        security.rate_limit("k", 5, 60, now=1000.0)
    # hammer while blocked
    for _ in range(20):
        assert security.rate_limit("k", 5, 60, now=1030.0) is False
    # the 5 allowed timestamps were all at t=1000; at t=1061 they expire
    assert security.rate_limit("k", 5, 60, now=1061.0) is True


def test_rate_limit_keys_independent():
    security.reset_rate_limits()
    for _ in range(5):
        security.rate_limit("a", 5, 60, now=1000.0)
    assert security.rate_limit("a", 5, 60, now=1000.0) is False
    assert security.rate_limit("b", 5, 60, now=1000.0) is True


# ── CSRF origin check ─────────────────────────────────────────────────────────

def _csrf_req(host="", origin="", referer=""):
    h = {}
    if host:
        h["host"] = host
    if origin:
        h["origin"] = origin
    if referer:
        h["referer"] = referer
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"),
                           cookies={"lw_session": "t"}, headers=h)


def test_csrf_ok_same_origin():
    assert security.csrf_ok(_csrf_req(host="dash.example.com", origin="https://dash.example.com")) is True


def test_csrf_blocks_cross_origin():
    assert security.csrf_ok(_csrf_req(host="dash.example.com", origin="https://evil.com")) is False


def test_csrf_allows_when_host_is_loopback():
    """Proxy rewrote Host to loopback — can't compare, so allow (SameSite covers it)."""
    assert security.csrf_ok(_csrf_req(host="127.0.0.1:8451", origin="https://dash.example.com")) is True


def test_csrf_allows_when_no_origin():
    """Non-browser client (curl/API) sends no Origin — not a CSRF vector."""
    assert security.csrf_ok(_csrf_req(host="dash.example.com")) is True


def test_csrf_referer_fallback_cross_origin_blocked():
    assert security.csrf_ok(_csrf_req(host="dash.example.com", referer="https://evil.com/x")) is False


def test_csrf_ignores_port_differences():
    assert security.csrf_ok(_csrf_req(host="dash.example.com:8443", origin="https://dash.example.com")) is True


def test_csrf_no_headers_allowed():
    assert security.csrf_ok(SimpleNamespace()) is True


def test_action_allowed_blocks_cross_origin_write_auth_on(monkeypatch):
    """End-to-end: valid session but cross-origin write is denied."""
    monkeypatch.setattr(security, "validate_session", lambda t: True)
    req = _csrf_req(host="dash.example.com", origin="https://evil.com")
    assert security.action_allowed(req, auth_enabled=True) is False


def test_action_allowed_permits_same_origin_write_auth_on(monkeypatch):
    monkeypatch.setattr(security, "validate_session", lambda t: True)
    req = _csrf_req(host="dash.example.com", origin="https://dash.example.com")
    assert security.action_allowed(req, auth_enabled=True) is True
