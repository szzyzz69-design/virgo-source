import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse

from app.config import Settings
from app.errors import ApiError
from app.security import secure_equals
from app.services.supervisor_service import (
    SupervisorNotFound,
    SupervisorScopeError,
    SupervisorService,
)


COOKIE = "virgo_supervisor_session"
logger = logging.getLogger(__name__)
_failures: dict[str, deque[float]] = defaultdict(deque)


def _configured(settings: Settings) -> None:
    if (
        not settings.supervisor_username
        or not settings.supervisor_password
        or len(settings.supervisor_session_secret) < 32
    ):
        raise ApiError(
            503,
            "SUPERVISOR_NOT_CONFIGURED",
            "Supervisor credentials are not configured",
        )


def _sign(settings: Settings, expires: int) -> str:
    payload = f"{settings.supervisor_username}:{expires}"
    signature = hmac.new(
        settings.supervisor_session_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{expires}.{signature}"


def _authenticated(settings: Settings, session: str | None) -> str:
    _configured(settings)
    try:
        expires_text, signature = (session or "").split(".", 1)
        expires = int(expires_text)
    except ValueError:
        raise ApiError(401, "UNAUTHORIZED", "Supervisor login required")
    expected = _sign(settings, expires).split(".", 1)[1]
    if expires < int(time.time()) or not hmac.compare_digest(signature, expected):
        raise ApiError(401, "UNAUTHORIZED", "Supervisor login required")
    return session or ""


def _csrf_token(settings: Settings, session: str) -> str:
    return hmac.new(
        settings.supervisor_session_secret.encode(),
        f"csrf:{session}".encode(),
        hashlib.sha256,
    ).hexdigest()


def create_supervisor_router(
    settings: Settings,
    service: SupervisorService,
) -> APIRouter:
    router = APIRouter(prefix="/supervisor/api", tags=["supervisor"])

    def auth(
        session: str | None = Cookie(default=None, alias=COOKIE),
    ) -> str:
        return _authenticated(settings, session)

    def write_auth(
        request: Request,
        session: str | None = Cookie(default=None, alias=COOKIE),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> str:
        authenticated_session = _authenticated(settings, session)
        expected = _csrf_token(settings, authenticated_session)
        if not csrf or not hmac.compare_digest(csrf, expected):
            raise ApiError(403, "CSRF_FAILED", "CSRF validation failed")
        origin = request.headers.get("origin")
        if origin:
            forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
            forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", "")).split(",", 1)[0].strip()
            if origin.rstrip("/") != f"{forwarded_proto}://{forwarded_host}".rstrip("/"):
                raise ApiError(403, "CSRF_FAILED", "Request origin is not allowed")
        return authenticated_session

    def map_not_found(error: Exception) -> ApiError:
        return ApiError(404, "NOT_FOUND", "Conversation not found")

    @router.post("/login")
    async def login(request: Request, response: Response):
        _configured(settings)
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        failures = _failures[client]
        while failures and failures[0] < now - 300:
            failures.popleft()
        if len(failures) >= 8:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "Too many login attempts; try again later",
            )
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if not secure_equals(username, settings.supervisor_username) or not secure_equals(
            password,
            settings.supervisor_password,
        ):
            failures.append(now)
            time.sleep(min(0.15 * len(failures), 1.0))
            raise ApiError(
                401,
                "INVALID_CREDENTIALS",
                "Invalid username or password",
            )
        failures.clear()
        expires = int(time.time()) + 12 * 3600
        session = _sign(settings, expires)
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        response.set_cookie(
            COOKIE,
            session,
            max_age=12 * 3600,
            httponly=True,
            secure=request.url.scheme == "https" or forwarded == "https",
            samesite="strict",
            path="/supervisor",
        )
        return {
            "username": settings.supervisor_username,
            "csrfToken": _csrf_token(settings, session),
        }

    @router.post("/logout")
    def logout(
        response: Response,
        _: str = Depends(write_auth),
    ):
        response.delete_cookie(COOKIE, path="/supervisor")
        return {"ok": True}

    @router.get("/me")
    def me(session: str = Depends(auth)):
        return {
            "username": settings.supervisor_username,
            "csrfToken": _csrf_token(settings, session),
        }

    @router.get("/agents")
    def agents(_: str = Depends(auth)):
        return service.list_agents(settings.supervisor_timezone)

    @router.get("/conversations")
    def conversations(
        account_id: str,
        sim_card_id: str | None = None,
        status: str = Query("all", pattern="^(all|waiting|replied|failed)$"),
        search: str = Query("", max_length=100),
        limit: int = 5,
        cursor: str | None = None,
        _: str = Depends(auth),
    ):
        try:
            return service.list_conversations(
                account_id,
                sim_card_id,
                status,
                search.strip(),
                limit,
                cursor,
            )
        except ValueError as error:
            raise ApiError(400, "INVALID_CURSOR", str(error)) from error

    @router.get("/conversations/{conversation_id}")
    def conversation(
        conversation_id: str,
        account_id: str,
        _: str = Depends(auth),
    ):
        try:
            return service.get_conversation(conversation_id, account_id)
        except SupervisorScopeError as error:
            raise ApiError(403, "FORBIDDEN", str(error)) from error
        except SupervisorNotFound as error:
            raise map_not_found(error) from error

    @router.get("/conversations/{conversation_id}/messages")
    def messages(
        conversation_id: str,
        account_id: str,
        limit: int = 50,
        before: int | None = None,
        _: str = Depends(auth),
    ):
        try:
            return service.list_messages(conversation_id, account_id, limit, before)
        except SupervisorScopeError as error:
            raise ApiError(403, "FORBIDDEN", str(error)) from error
        except SupervisorNotFound as error:
            raise map_not_found(error) from error

    return router


def mount_supervisor_ui(app) -> None:
    root = Path(__file__).resolve().parents[2] / "supervisor" / "dist"
    index = root / "index.html"

    @app.get("/supervisor", include_in_schema=False)
    @app.get("/supervisor/", include_in_schema=False)
    @app.get("/supervisor/conversations/{conversation_id}", include_in_schema=False)
    def supervisor_page(conversation_id: str | None = None):
        if not index.exists():
            raise ApiError(
                503,
                "SUPERVISOR_UI_MISSING",
                "Supervisor UI was not built",
            )
        return FileResponse(index)

    @app.get("/supervisor/assets/{asset_path:path}", include_in_schema=False)
    def supervisor_asset(asset_path: str):
        target = (root / "assets" / asset_path).resolve()
        assets = (root / "assets").resolve()
        if assets not in target.parents or not target.is_file():
            raise ApiError(404, "NOT_FOUND", "Asset not found")
        return FileResponse(target)
