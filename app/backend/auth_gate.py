"""
app/backend/auth_gate.py
=========================
Deny-by-default authentication gate.

Why a middleware and not a per-route dependency
-----------------------------------------------
Auth used to be opt-in: `require_user` appeared in 2 of the 20 route modules
(auth.py, chat.py), so /analysis, /research, /screener, /watchlist, /flows,
/hedge-fund, /storage and /api-keys were reachable by anyone who knew the URL.
Opt-in auth fails open — every new route file added later is public until
someone remembers to protect it. This middleware inverts that: everything is
protected unless it appears on the allowlist below.

Accepted credentials
--------------------
1. ``Authorization: Bearer <jwt>`` — a user session token from /auth/google or
   /auth/apple. Resolved to a User and attached to ``request.state.user``.
2. The shared admin secret (``DB_UPLOAD_SECRET``), as ``X-Admin-Secret`` header
   or ``?secret=`` query param — for service-to-service callers with no user
   session, e.g. the dd-dispatcher cron service. Never accepted when the env
   var is unset, so a missing secret cannot degrade into "anything passes".

Escape hatch
------------
``AUTH_ENFORCED=false`` disables the gate (logs loudly at startup). Intended as
a rollback lever if a deploy breaks; not for normal operation.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def auth_enforced() -> bool:
    return os.environ.get("AUTH_ENFORCED", "true").lower() not in ("0", "false", "no")


# ── Allowlist ─────────────────────────────────────────────────────────────────
# Keep this list short and justify every entry.

PUBLIC_PATHS: frozenset[str] = frozenset({
    "/",                    # health probe — Railway healthcheckPath
    "/ping",
    "/health",
    "/favicon.ico",
    "/docs",                # OpenAPI UI
    "/redoc",
    "/openapi.json",
    "/auth/google",         # pre-auth by definition: exchanges an id_token
    "/auth/apple",
})

# Prefix matches. /admin/* is exempt because each of those routes performs its
# own DB_UPLOAD_SECRET check and fails closed when the env var is unset — see
# admin.py, db_upload.py, power_law_migrate.py, dd_alerts.py.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/admin/",
)


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _valid_admin_secret(request: Request) -> bool:
    """True if the caller presented the shared service secret."""
    expected = os.environ.get("DB_UPLOAD_SECRET", "")
    if not expected:
        return False        # unset secret must never authenticate anyone
    presented = request.headers.get("X-Admin-Secret") or request.query_params.get("secret") or ""
    return bool(presented) and hmac.compare_digest(presented, expected)


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated requests to any non-allowlisted path."""

    async def dispatch(self, request: Request, call_next):
        # CORS preflight carries no credentials by design.
        if request.method == "OPTIONS":
            return await call_next(request)

        if not auth_enforced() or _is_public(request.url.path):
            return await call_next(request)

        if _valid_admin_secret(request):
            request.state.user = None
            request.state.is_service_call = True
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = authorization.removeprefix("Bearer ").strip()

        # Own DB session: middleware runs outside the request's dependency
        # graph, so Depends(get_db) is not available here.
        from app.backend.database.connection import SessionLocal
        from app.backend.services.auth_service import get_user_from_token

        db = SessionLocal()
        try:
            user = get_user_from_token(token, db)
            if user is None:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or expired token"},
                )
            # Routes that need the caller can read request.state.user instead of
            # decoding the token a second time.
            request.state.user = user
            request.state.user_id = user.id
            request.state.is_service_call = False
        finally:
            db.close()

        return await call_next(request)


def verify_startup_config() -> None:
    """Fail fast on an unsafe production configuration.

    A default JWT signing key means anyone can mint a token for any user id, so
    the process refuses to start rather than serving forged sessions.
    """
    from app.backend.services.auth_service import SECRET_KEY

    if not auth_enforced():
        logger.warning(
            "AUTH_ENFORCED=false — every API route is reachable without "
            "credentials. Do not run this way in production."
        )
        return

    if SECRET_KEY == "change-me-in-production-use-a-long-random-string" or len(SECRET_KEY) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY is unset, default, or shorter than 32 characters. "
            "Session tokens signed with a guessable key can be forged for any "
            "user. Set JWT_SECRET_KEY to a long random string "
            '(python -c "import secrets; print(secrets.token_urlsafe(48))"), '
            "or set AUTH_ENFORCED=false for local development only."
        )

    if not os.environ.get("DB_UPLOAD_SECRET"):
        logger.warning(
            "DB_UPLOAD_SECRET is unset — /admin/* routes and service-to-service "
            "calls (dd-dispatcher) will be rejected."
        )
