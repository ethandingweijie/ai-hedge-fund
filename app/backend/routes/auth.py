"""
app/backend/routes/auth.py
===========================
Authentication endpoints.

POST /auth/google  — verify Google id_token, return our JWT
POST /auth/apple   — verify Apple id_token, return our JWT
GET  /auth/me      — return current user from Bearer token
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backend.database import get_db
from app.backend.services.auth_service import (
    create_access_token,
    get_user_from_token,
    upsert_user,
    verify_apple_token,
    verify_google_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / response models ──────────────────────────────────────────────────

class TokenRequest(BaseModel):
    id_token: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str]
    avatar_url: Optional[str]
    provider: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _current_user(authorization: Optional[str] = Header(default=None),
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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/google", response_model=AuthResponse)
async def login_google(body: TokenRequest, db: Session = Depends(get_db)):
    """Verify a Google id_token and return our session JWT."""
    try:
        payload = await verify_google_token(body.id_token)
    except Exception as exc:
        logger.warning("Google token verification failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid Google token")

    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Google token missing email")

    user = upsert_user(
        db,
        email=email,
        name=payload.get("name"),
        avatar_url=payload.get("picture"),
        provider="google",
        provider_sub=payload.get("sub", ""),
    )

    token = create_access_token(user)
    return AuthResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name,
              "avatar_url": user.avatar_url, "provider": user.provider},
    )


@router.post("/apple", response_model=AuthResponse)
async def login_apple(body: TokenRequest, db: Session = Depends(get_db)):
    """Verify an Apple id_token and return our session JWT."""
    try:
        payload = await verify_apple_token(body.id_token)
    except Exception as exc:
        logger.warning("Apple token verification failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid Apple token")

    email = payload.get("email")
    sub = payload.get("sub", "")

    # Apple may omit email after first sign-in — use sub as fallback identifier
    if not email:
        email = f"{sub}@apple-private.com"

    user = upsert_user(
        db,
        email=email,
        name=None,          # Apple doesn't reliably send name in the token
        avatar_url=None,
        provider="apple",
        provider_sub=sub,
    )

    token = create_access_token(user)
    return AuthResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name,
              "avatar_url": user.avatar_url, "provider": user.provider},
    )


@router.get("/me", response_model=UserResponse)
def get_me(user=Depends(_current_user)):
    """Return the currently authenticated user."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        provider=user.provider,
    )
