"""
app/backend/services/auth_service.py
=====================================
Google + Apple ID-token verification and JWT session issuance.

Flow:
  1. Frontend gets an id_token from Google/Apple SDK.
  2. Frontend POSTs it to /auth/google or /auth/apple.
  3. We verify the token with the provider's public keys.
  4. We upsert a User row, then issue our own short-lived JWT.
  5. Frontend stores the JWT and sends it as Bearer on every request.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.backend.database.models import User

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM = "HS256"
TOKEN_EXPIRE_SECONDS = 60 * 60 * 24 * 7   # 7 days


# ── Google verification ────────────────────────────────────────────────────────

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_google_certs_cache: dict[str, Any] = {}
_google_certs_fetched_at: float = 0


async def _get_google_certs() -> dict:
    global _google_certs_cache, _google_certs_fetched_at
    if time.time() - _google_certs_fetched_at < 3600:
        return _google_certs_cache
    async with httpx.AsyncClient() as client:
        r = await client.get(GOOGLE_CERTS_URL, timeout=10)
        r.raise_for_status()
        _google_certs_cache = r.json()
        _google_certs_fetched_at = time.time()
    return _google_certs_cache


async def verify_google_token(id_token: str) -> dict:
    """Verify a Google id_token and return the payload dict."""
    from jose import jwk, jwt as jose_jwt
    from jose.utils import base64url_decode
    import json, base64

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    certs = await _get_google_certs()

    # Decode header to find kid
    header_segment = id_token.split(".")[0]
    padding = 4 - len(header_segment) % 4
    header_bytes = base64.urlsafe_b64decode(header_segment + "=" * padding)
    header = json.loads(header_bytes)
    kid = header.get("kid")

    # Find matching key
    matching_key = None
    for key_data in certs.get("keys", []):
        if key_data.get("kid") == kid:
            matching_key = key_data
            break

    if matching_key is None:
        raise ValueError("No matching Google public key found for kid")

    public_key = jwk.construct(matching_key)

    options = {"verify_aud": bool(client_id)}
    audience = client_id if client_id else None

    payload = jose_jwt.decode(
        id_token,
        public_key,
        algorithms=["RS256"],
        audience=audience,
        options={"verify_aud": bool(audience)},
    )

    if payload.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise ValueError("Invalid Google token issuer")

    return payload


# ── Apple verification ─────────────────────────────────────────────────────────

APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
_apple_certs_cache: dict[str, Any] = {}
_apple_certs_fetched_at: float = 0


async def _get_apple_keys() -> dict:
    global _apple_certs_cache, _apple_certs_fetched_at
    if time.time() - _apple_certs_fetched_at < 3600:
        return _apple_certs_cache
    async with httpx.AsyncClient() as client:
        r = await client.get(APPLE_KEYS_URL, timeout=10)
        r.raise_for_status()
        _apple_certs_cache = r.json()
        _apple_certs_fetched_at = time.time()
    return _apple_certs_cache


async def verify_apple_token(id_token: str) -> dict:
    """Verify an Apple id_token and return the payload dict."""
    from jose import jwk
    from jose import jwt as jose_jwt
    import json, base64

    client_id = os.environ.get("APPLE_CLIENT_ID", "")
    keys_data = await _get_apple_keys()

    header_segment = id_token.split(".")[0]
    padding = 4 - len(header_segment) % 4
    header_bytes = base64.urlsafe_b64decode(header_segment + "=" * padding)
    header = json.loads(header_bytes)
    kid = header.get("kid")

    matching_key = None
    for key_data in keys_data.get("keys", []):
        if key_data.get("kid") == kid:
            matching_key = key_data
            break

    if matching_key is None:
        raise ValueError("No matching Apple public key found for kid")

    public_key = jwk.construct(matching_key)

    payload = jose_jwt.decode(
        id_token,
        public_key,
        algorithms=["RS256"],
        audience=client_id if client_id else None,
        options={"verify_aud": bool(client_id)},
    )

    if payload.get("iss") != "https://appleid.apple.com":
        raise ValueError("Invalid Apple token issuer")

    return payload


# ── User upsert ────────────────────────────────────────────────────────────────

def upsert_user(db: Session, *, email: str, name: str | None, avatar_url: str | None,
                provider: str, provider_sub: str) -> User:
    """Find or create a User row. Updates name/avatar if changed."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email,
            name=name,
            avatar_url=avatar_url,
            provider=provider,
            provider_sub=provider_sub,
        )
        db.add(user)
    else:
        if name and user.name != name:
            user.name = name
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    return user


# ── JWT issuance / verification ────────────────────────────────────────────────

def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name,
        "avatar": user.avatar_url,
        "exp": int(time.time()) + TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate our own JWT. Raises JWTError on failure."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def get_user_from_token(token: str, db: Session) -> User | None:
    """Return the User for a Bearer token, or None if invalid/expired."""
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
        return db.query(User).filter(User.id == user_id).first()
    except (JWTError, KeyError, ValueError):
        return None
