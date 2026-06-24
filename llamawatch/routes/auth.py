"""Authentication routes: login, logout, status."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import security
from ..auth import is_auth_enabled, verify_password, create_session, validate_session, destroy_session

router = APIRouter()

# Login throttle: at most this many attempts per client IP per window.
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECS = 60


class LoginRequest(BaseModel):
    password: str


@router.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request, response: Response):
    client_ip = getattr(getattr(request, "client", None), "host", None) or "unknown"
    if not security.rate_limit(f"login:{client_ip}", _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECS):
        return JSONResponse(
            status_code=429,
            content={"error": "Too many login attempts. Wait a minute and try again."},
            headers={"Retry-After": str(_LOGIN_WINDOW_SECS)},
        )
    if not verify_password(req.password):
        return JSONResponse(status_code=401, content={"error": "Incorrect password"})
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
