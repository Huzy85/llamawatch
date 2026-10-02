"""Authentication routes: login, logout, status."""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import security
from ..auth import is_auth_enabled, verify_password, create_session, validate_session, destroy_session

router = APIRouter()
_log = logging.getLogger("llamawatch.auth")

# Login throttle: at most this many attempts per visitor address per window,
# plus a global cap so a tunnel (where every visitor shares 127.0.0.1) cannot
# be brute-forced by rotating addresses. Tightened 2026-09-15 (was 10/60s).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECS = 300
_LOGIN_GLOBAL_MAX = 30


def _visitor_ip(request: Request) -> str:
    """Real visitor address: first X-Forwarded-For hop when relayed by a proxy/tunnel."""
    xff = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or ""
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    return getattr(getattr(request, "client", None), "host", None) or "unknown"


class LoginRequest(BaseModel):
    password: str


@router.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request, response: Response):
    client_ip = _visitor_ip(request)
    if not security.rate_limit(f"login:{client_ip}", _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECS) \
            or not security.rate_limit("login:_global", _LOGIN_GLOBAL_MAX, _LOGIN_WINDOW_SECS):
        _log.warning("login throttled ip=%s", client_ip)
        return JSONResponse(
            status_code=429,
            content={"error": "Too many login attempts. Wait five minutes and try again."},
            headers={"Retry-After": str(_LOGIN_WINDOW_SECS)},
        )
    if not verify_password(req.password):
        _log.warning("login failed ip=%s", client_ip)
        return JSONResponse(status_code=401, content={"error": "Incorrect password"})
    _log.info("login ok ip=%s", client_ip)
    token, max_age = create_session()
    response = JSONResponse(content={"status": "ok"})
    is_https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    response.set_cookie(
        "lw_session", token, max_age=max_age,
        httponly=True, samesite="lax", path="/",
        secure=is_https,
    )
    return response


@router.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get("lw_session")
    if token:
        destroy_session(token)
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie("lw_session")
    return response


@router.get("/auth/status")
async def auth_status(request: Request):
    if not is_auth_enabled():
        return {"auth_enabled": False}
    token = request.cookies.get("lw_session")
    return {"auth_enabled": True, "authenticated": validate_session(token)}
