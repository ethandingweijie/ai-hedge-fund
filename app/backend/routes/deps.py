"""
app/backend/routes/deps.py
===========================
Shared FastAPI dependencies. `require_user` is the same Bearer-JWT-required
check `auth.py` defines locally as `_current_user` — extracted here so a
third route file (chat.py) doesn't duplicate it again. `auth.py`'s own
`_current_user` and `watchlist.py`'s optional-auth `_optional_user_id` are
left as-is (not a goal of this change to refactor working code).
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.backend.database import get_db
from app.backend.services.auth_service import get_user_from_token


def require_user(authorization: Optional[str] = Header(default=None),
                  db: Session = Depends(get_db)):
    """Dependency: extract and validate Bearer JWT, return User or raise 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(token, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired token")
    return user


def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
    db: Session = Depends(get_db),
):
    """Dependency: require admin access.

    Admin access is granted via either:
    1. X-Admin-Secret header matching DB_UPLOAD_SECRET (service-to-service)
    2. A valid JWT from a user with role='admin' (Phase 3 — not yet implemented)

    For Phase 0, only the admin secret is checked. This protects API key
    management endpoints from regular authenticated users.
    """
    admin_secret = os.environ.get("DB_UPLOAD_SECRET", "")

    # Check admin secret header
    if admin_secret and x_admin_secret:
        if hmac.compare_digest(admin_secret, x_admin_secret):
            return None  # Admin access granted via secret

    # If no admin secret matched, check for a valid JWT user
    # (Phase 3 will add role check here; for now, regular users are denied)
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        user = get_user_from_token(token, db)
        if user is not None:
            # TODO Phase 3: Check user.role == 'admin'
            # For now, deny all regular users from admin endpoints
            pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required. Provide X-Admin-Secret header.",
    )
