"""Zero-dependency authentication for Stratoscan.

Pure-stdlib security (no JWT/bcrypt packages needed):

  - **Passwords**: PBKDF2-HMAC-SHA256 with a per-user random salt and
    600,000 iterations (OWASP-recommended). Stored as
    ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
  - **Tokens**: HMAC-SHA256-signed JWT (HS256 format) — header.payload
    base64url-encoded, signature = HMAC-SHA256(secret, "header.payload").
    Verified with ``hmac.compare_digest`` (constant-time).

Provides the FastAPI dependency used to protect endpoints:

  - ``get_current_user``  — resolves the ``Authorization: Bearer`` token.

Everything is free (no paywall): the backtesting engine used to sit
behind a premium plan; that gate was removed. Auth now only exists for
per-user rate limiting and account features.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from src.config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_HASH_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-HMAC-SHA256
_TOKEN_TTL = "exp"            # key name in the JWT payload

# ═══════════════════════════════════════════════════════════════════════════════
# Password hashing (PBKDF2)
# ═══════════════════════════════════════════════════════════════════════════════


def hash_password(password: str) -> str:
    """Hash a plaintext password with PBKDF2-HMAC-SHA256.

    Format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
    The random per-user salt is embedded so verification can re-derive.

    Args:
        password: The plaintext password.

    Returns:
        The encoded hash string (safe to store in the database).
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"{_HASH_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash.

    Constant-time comparison via ``hmac.compare_digest``. Malformed
    stored hashes simply fail verification (never raise).

    Args:
        password: The plaintext candidate.
        stored_hash: The stored ``pbkdf2_sha256$...`` string.

    Returns:
        True if the password matches, False otherwise.
    """
    try:
        algo, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if algo != _HASH_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        # Wrong number of segments / non-hex salt → not a valid hash
        return False


# VULN-001 fix: timing-equalization dummy hash for the login endpoint.
# Login previously did ``if not user or not verify_password(...)`` — when the
# email didn't exist, the expensive PBKDF2 (600k iterations, ~400ms) never
# ran, so unknown-email responses answered in ~3ms vs ~400ms for a known
# email. That timing difference leaks account existence. Login now verifies
# against this fixed throwaway hash for unknown emails so BOTH paths burn the
# same PBKDF2 cost. The hash never matches a real user (random password).
_dummy_hash: Optional[str] = None


def dummy_password_hash() -> str:
    """Return a valid PBKDF2 hash of a throwaway password (never matches)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("stratoscan-timing-equalizer-dummy")
    return _dummy_hash


# ═══════════════════════════════════════════════════════════════════════════════
# JWT tokens (HMAC-SHA256)
# ═══════════════════════════════════════════════════════════════════════════════


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode without padding (JWT standard)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Base64url-decode, re-adding any stripped padding."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(user_id: int, email: str) -> str:
    """Create a signed JWT (HS256) for a user.

    Args:
        user_id: The user's database id (the ``sub`` claim).
        email: The user's email (for convenience/debugging).

    Returns:
        A three-part ``header.payload.signature`` token string.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        _TOKEN_TTL: now + settings.jwt_expire_hours * 3600,
    }
    header_b64 = _b64url_encode(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(
        settings.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a token's signature + expiry and return its payload.

    Args:
        token: The JWT string.

    Returns:
        The decoded payload dict, or None if invalid/expired/forged.
    """
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = hmac.new(
            settings.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64url_decode(signature_b64), expected):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if not isinstance(payload, dict):
            return None
        if int(payload.get(_TOKEN_TTL, 0)) < time.time():
            return None
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI dependencies
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_bearer_token(request: Request) -> Optional[str]:
    """Pull the token out of the ``Authorization: Bearer`` header."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    return auth_header[7:].strip()


def _fetch_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Load a user from the database by id (lazy import keeps tests light)."""
    from src.database import PostgresDB

    try:
        return PostgresDB().get_user_by_id(user_id)
    except Exception as e:
        logger.error("Failed to load user %s: %s", user_id, e)
        return None


def get_current_user(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: resolve the authenticated user from the request.

    Raises 401 (via HTTPException) when the token is missing, malformed,
    expired, or the account no longer exists. Used by endpoints that need
    a per-user identity (e.g. /backtest for rate limiting).
    """
    if not settings.has_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured. Set JWT_SECRET in .env.",
        )

    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Include an 'Authorization: Bearer <token>' header.",
        )

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token. Log in again to get a fresh one.",
        )

    try:
        user_id = int(payload.get("sub", -1))
    except (TypeError, ValueError):
        user_id = -1

    user = _fetch_user(user_id)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Account no longer exists. Log in again.",
        )
    return user


def require_admin(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: an authenticated user WITH the admin role.

    Composes :func:`get_current_user` and additionally checks the user's
    ``is_admin`` flag (from the users table). Used by the /admin/*
    management endpoints — the admin dashboard is a power tool, so
    privilege escalation must be explicit (ADMIN_EMAILS bootstrap or
    PATCH /admin/users/{id} by an existing admin), never implicit.

    Raises 401 when unauthenticated (via get_current_user) and 403 when
    the authenticated user is not an admin.
    """
    user = get_current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required. This action is restricted to administrators.",
        )
    return user



