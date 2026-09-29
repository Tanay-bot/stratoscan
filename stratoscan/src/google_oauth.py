"""Google OAuth — "Continue with Google", server-side code flow, stdlib only.

No OAuth library and no new dependency: ``urllib`` for the two HTTP calls,
``hmac`` for the state cookie, ``json``/``base64`` for the ID token. The same
shape as ``src/emails.py`` and ``src/redis_ratelimit.py``.

The flow, end to end (routes live in ``src/api.py``):

    GET /auth/google/start
        → 302 to accounts.google.com, carrying ``state`` and ``nonce``
        → sets ONE signed, HttpOnly ``g_oauth`` cookie holding both

    GET /auth/google/callback?code=…&state=…
        → verify the cookie's HMAC and that ``state`` round-tripped (CSRF)
        → ``exchange_code()``   (patched in tests — never called by them)
        → ``validate_id_token()`` — iss, aud, exp, nonce, email_verified
        → ``resolve_google_identity()`` — the merge rule, below
        → 302 to {APP_BASE_URL}/auth/google/complete#token=…&expires_in=…

Everything Google-specific lives in this module so ``api.py`` holds two thin
routes and nothing else.

**Why the state cookie is one cookie and not two.** The ``state`` is the CSRF
token (it proves the callback belongs to a flow this browser started) and the
``nonce`` is the replay guard for the ID token (it proves the token was minted
for THIS request). They are only meaningful together, they have the same
lifetime, and splitting them doubles the number of ways to get the pairing
wrong. Both are inside one HMAC-signed value, so neither can be edited by the
browser — signing with ``jwt_secret`` means an attacker who cannot forge a JWT
also cannot forge this.

**What is deliberately NOT trusted.** The ``id_token`` is taken from Google's
token-endpoint response, never from the browser: OpenID Connect Core §3.1.3.7
allows skipping the RS256 signature check in exactly that case, because the
token arrived over TLS directly from the issuer. Nothing else may rely on that
shortcut.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from src.auth import hash_password
from src.config import settings

logger = logging.getLogger(__name__)

# ── Google endpoints ────────────────────────────────────────────────────────
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "openid email profile"
EXCHANGE_TIMEOUT = 15.0  # seconds; the token endpoint is not a long operation

# ── State cookie ────────────────────────────────────────────────────────────
COOKIE_NAME = "g_oauth"
# Scoped to the OAuth routes: the cookie is meaningless (and useless to an
# attacker) on any other path, so it is never attached to another request.
COOKIE_PATH = "/auth/google"
COOKIE_MAX_AGE = 600  # 10 minutes — one consent screen, no more

# Google has used both spellings in ``iss`` for years; both are acceptable.
ALLOWED_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class IdTokenError(ValueError):
    """An ID-token claim failed validation.

    ``code`` is the value the callback redirects with, so it is one of the
    two error codes this module can produce:

      - ``"unverified"`` — Google says the address is not verified. Distinct
        because it is the one the user can act on (verify it in Google), and
        because it is load-bearing for the merge rule: an unverified address
        may belong to someone else.
      - ``"token"`` — anything else about the token is wrong.

    The ``str()`` of this exception (the real reason) is logged and never
    reaches the browser — the redirect carries the code only.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ═══════════════════════════════════════════════════════════════════════════
# base64url helpers
# ═══════════════════════════════════════════════════════════════════════════


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """Base64url-decode, re-adding the padding that was stripped."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ═══════════════════════════════════════════════════════════════════════════
# State / nonce — one signed cookie carrying both
# ═══════════════════════════════════════════════════════════════════════════


def new_token() -> str:
    """A fresh 256-bit URL-safe random token (used for state AND nonce)."""
    return secrets.token_urlsafe(32)


def _cookie_signature(payload: bytes) -> str:
    """HMAC-SHA256 over the cookie payload, keyed by the JWT secret."""
    digest = hmac.new(
        settings.jwt_secret.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    return _b64url_encode(digest)


def state_cookie_value(state: str, nonce: str) -> str:
    """Sign ``{state, nonce}`` into the cookie value ``<payload>.<signature>``."""
    payload = json.dumps(
        {"state": state, "nonce": nonce}, separators=(",", ":")
    ).encode("utf-8")
    return f"{_b64url_encode(payload)}.{_cookie_signature(payload)}"


def verify_state_cookie(value: Optional[str]) -> Optional[Dict[str, str]]:
    """Return the cookie's ``{state, nonce}`` when the signature checks out.

    Returns None for a missing, malformed, or forged cookie — the caller turns
    that into ``oauth_error=state``. The comparison is constant-time
    (``hmac.compare_digest``), so a wrong signature cannot be brute-forced one
    byte at a time.
    """
    if not value or "." not in value:
        return None
    payload_b64, signature = value.rsplit(".", 1)
    try:
        payload = _b64url_decode(payload_b64)
    except Exception:
        return None
    if not hmac.compare_digest(signature, _cookie_signature(payload)):
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    state, nonce = data.get("state"), data.get("nonce")
    if not state or not nonce:
        return None
    return {"state": str(state), "nonce": str(nonce)}


def state_matches(claims: Dict[str, str], state: str) -> bool:
    """Constant-time compare of the callback's ``state`` against the cookie's."""
    return hmac.compare_digest(claims.get("state", ""), state or "")


def cookie_settings(value: str) -> Dict[str, Any]:
    """Keyword arguments for ``RedirectResponse.set_cookie``.

    ``HttpOnly`` (no script can read the state), ``SameSite=Lax`` (it survives
    Google's top-level redirect back, but is not sent on cross-site POSTs),
    scoped to ``/auth/google``, and ``Secure`` only in production so a plain
    http://localhost works with the same code path.
    """
    return {
        "key": COOKIE_NAME,
        "value": value,
        "max_age": COOKIE_MAX_AGE,
        "path": COOKIE_PATH,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.is_production,
    }


# ═══════════════════════════════════════════════════════════════════════════
# URLs
# ═══════════════════════════════════════════════════════════════════════════


def build_authorization_url(state: str, nonce: str) -> str:
    """The Google consent-screen URL for this sign-in attempt."""
    query = urllib.parse.urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            # Always show the account chooser: a shared machine must never
            # silently reuse whichever Google account was last signed in.
            "prompt": "select_account",
        }
    )
    return f"{AUTH_ENDPOINT}?{query}"


def begin_authorization() -> tuple[str, str]:
    """Start a sign-in: return ``(google_url, signed_cookie_value)``."""
    state, nonce = new_token(), new_token()
    return build_authorization_url(state, nonce), state_cookie_value(state, nonce)


def login_error_url(code: str) -> str:
    """Where every failure lands: the login page with a reason code.

    Only the code travels — never exception text (golden rule #12). The
    frontend maps each code to one sentence (Auth.tsx).
    """
    base = settings.app_base_url.rstrip("/")
    return f"{base}/login?{urllib.parse.urlencode({'oauth_error': code})}"


def complete_redirect_url(token: str, expires_in: int) -> str:
    """Where success lands: the SPA, with the JWT in the FRAGMENT.

    Never the query string. A fragment is not sent to the server, so the token
    never reaches an access log, a Referer header or a proxy cache — and the
    SPA deletes it from the address bar the moment it reads it.
    """
    base = settings.app_base_url.rstrip("/")
    fragment = urllib.parse.urlencode({"token": token, "expires_in": expires_in})
    return f"{base}/auth/google/complete#{fragment}"


# ═══════════════════════════════════════════════════════════════════════════
# Token exchange (the one network call)
# ═══════════════════════════════════════════════════════════════════════════


def exchange_code(code: str) -> Dict[str, Any]:
    """Trade an authorization code for tokens at Google's token endpoint.

    Module-level on purpose: the test suite patches exactly this function, so
    no test ever opens a socket (``mock.patch("src.google_oauth.exchange_code")``).

    Raises:
        RuntimeError: on a transport failure, a non-200 response, or a body
        that is not a JSON object. The caller maps that to
        ``oauth_error=exchange``.
    """
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=EXCHANGE_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"token endpoint returned HTTP {e.code}") from e
    except Exception as e:
        raise RuntimeError(f"token endpoint unreachable: {e}") from e

    if status != 200:
        raise RuntimeError(f"token endpoint returned HTTP {status}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("token endpoint returned a non-JSON body") from e
    if not isinstance(data, dict):
        raise RuntimeError("token endpoint returned an unexpected body")
    return data


# ═══════════════════════════════════════════════════════════════════════════
# ID token claims
# ═══════════════════════════════════════════════════════════════════════════


def decode_id_token(id_token: str) -> Dict[str, Any]:
    """Decode the payload of a JWT *without* verifying its signature.

    Safe **only** for a token that came straight out of Google's token-endpoint
    response over TLS, which is the only place this is called from — OpenID
    Connect Core §3.1.3.7 allows exactly that, because the transport is the
    authentication. An ``id_token`` from a request body, query string or cookie
    must never be handed to this function.
    """
    segments = id_token.split(".")
    if len(segments) < 2:
        raise ValueError("id_token is not a JWT")
    claims = json.loads(_b64url_decode(segments[1]))
    if not isinstance(claims, dict):
        raise ValueError("id_token payload is not an object")
    return claims


def validate_id_token(id_token: str, nonce: str) -> Dict[str, Any]:
    """Validate the claims of an ID token fetched from the token endpoint.

    Every check is a rejection the caller turns into a redirect:

      - decodable, ``iss`` is Google's, ``aud`` is THIS client id
      - ``exp`` is in the future
      - ``nonce`` equals the one this browser's cookie asked for
      - ``email_verified`` is True — Google has proved the address. This is
        load-bearing for the merge rule: an unverified address may belong to
        somebody else, so acting on it would hand a stranger's account away.
      - ``sub`` and ``email`` are present

    Returns:
        The validated claims.

    Raises:
        IdTokenError: ``code="unverified"`` for an unverified address,
        ``code="token"`` for every other failure.
    """
    try:
        claims = decode_id_token(id_token)
    except Exception as e:
        raise IdTokenError("token", f"undecodable id_token: {e}") from e

    issuer = str(claims.get("iss", ""))
    if issuer not in ALLOWED_ISSUERS:
        raise IdTokenError("token", f"unexpected issuer {issuer!r}")

    if str(claims.get("aud", "")) != settings.google_client_id:
        raise IdTokenError("token", "audience is not this client id")

    try:
        expires_at = int(claims.get("exp", 0))
    except (TypeError, ValueError):
        raise IdTokenError("token", "exp is not an integer") from None
    if expires_at <= time.time():
        raise IdTokenError("token", "id_token has expired")

    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce or ""):
        raise IdTokenError("token", "nonce mismatch")

    # Strict identity check on purpose: Google sends a JSON boolean. A truthy
    # string ("true") is not proof, and treating it as one would defeat the
    # single check the merge rule depends on.
    if claims.get("email_verified") is not True:
        raise IdTokenError("unverified", "Google reports the email as unverified")

    if not claims.get("sub") or not claims.get("email"):
        raise IdTokenError("token", "id_token lacks sub/email")

    return claims


# ═══════════════════════════════════════════════════════════════════════════
# The merge rule
# ═══════════════════════════════════════════════════════════════════════════


def unusable_password_hash() -> str:
    """A real PBKDF2 hash of a random 256-bit value nobody knows.

    Deliberately a genuine hash rather than a marker string like ``"!"``:
    ``verify_password`` rejects a malformed hash in microseconds, so a marker
    would answer Google-only accounts noticeably faster than password accounts
    and leak which is which. Hashing a random secret keeps the cost of every
    login attempt identical. A Google-only user who wants a password uses
    "Forgot password".
    """
    return hash_password(secrets.token_urlsafe(32))


def resolve_google_identity(
    db, sub: str, email: str, unusable_hash: Optional[str] = None
) -> Dict[str, Any]:
    """Map a validated Google identity onto a user row — the merge rule.

    Resolved in this order, and the order is the security:

      1. **A row already has this ``sub``** → sign in. Even if the Google
         email has changed since: ``sub`` is stable, the address is not.
      2. **A row has this email and it is verified** → link ``google_sub`` to
         it and sign in. The address was proven (by the verification link or
         an earlier Google sign-in), so this is the same person; their
         password keeps working.
      3. **A row has this email and it is NOT verified** → **take the row
         over**. Nobody ever proved they could read that inbox — anyone can
         register ``alice@gmail.com`` — so linking would leave a stranger's
         password able to open the real owner's account, with the session
         they already hold still alive. See
         ``PostgresDB.take_over_for_google``.
      4. **No row** → create one: verified, with ``google_sub`` set and an
         unusable password hash.

    Case 3 is the classic account-takeover bug in this feature, and it is why
    this function needs the row's verification state rather than just its
    existence.

    Args:
        db: A ``PostgresDB`` (or the test fake) exposing the four Google
            methods.
        sub: The validated ``sub`` claim.
        email: The validated, lowercased ``email`` claim.
        unusable_hash: Optional precomputed hash (lets a caller hash once).

    Returns:
        ``{id, email, is_admin, token_version, via}`` where ``via`` is
        ``"sub"``, ``"linked"``, ``"taken_over"`` or ``"created"`` — the
        provenance is logged, never returned to the browser.
    """
    email = email.strip().lower()
    if unusable_hash is None:
        unusable_hash = unusable_password_hash()

    linked = db.get_user_by_google_sub(sub)
    if linked:
        return {
            "id": int(linked["id"]),
            "email": linked.get("email") or email,
            "is_admin": bool(linked.get("is_admin")),
            "token_version": int(linked.get("token_version", 0)),
            "via": "sub",
        }

    existing = db.get_user_by_email(email)
    if existing:
        user_id = int(existing["id"])
        if existing.get("email_verified"):
            db.link_google_identity(user_id, sub)
            return {
                "id": user_id,
                "email": existing.get("email") or email,
                "is_admin": bool(existing.get("is_admin")),
                "token_version": int(existing.get("token_version", 0)),
                "via": "linked",
            }
        new_version = db.take_over_for_google(user_id, sub, unusable_hash)
        return {
            "id": user_id,
            "email": existing.get("email") or email,
            "is_admin": bool(existing.get("is_admin")),
            "token_version": int(new_version),
            "via": "taken_over",
        }

    user_id = db.create_google_user(email, sub, unusable_hash)
    return {
        "id": int(user_id),
        "email": email,
        "is_admin": False,
        "token_version": 0,
        "via": "created",
    }
