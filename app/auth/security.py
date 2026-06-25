"""Password hashing and JWT issue/verify/refresh primitives.

This module is the framework-agnostic security core for the ClauseOps Auth
Service (design "Component 1: Auth Service"). It deliberately knows nothing
about FastAPI, the database, or cookies; the HTTP endpoints and the
current-user dependency (task 4.2) build on top of these functions.

What lives here
---------------
* Argon2 password hashing/verification via ``passlib`` -- only the hash is ever
  produced or stored; raw passwords never leave the caller. (Requirement 1.1)
* Short-lived **access** tokens signed with ``jwt_secret`` carrying the user id
  in the ``sub`` claim and a ``type`` claim, used to authorize data endpoints.
  (Requirement 1.3, 1.7)
* Rotating **refresh** tokens signed with ``jwt_refresh_secret`` carrying a
  ``jti`` so a specific refresh token can be invalidated/rotated, plus a
  rotation helper that mints a fresh refresh token from a valid one.
  (Requirement 1.3, 1.5, 1.6)
* Decode/verify helpers that reject expired, malformed, mis-signed, and
  wrong-type tokens with a single clear :class:`TokenError`.

Invalidation / logout (task 4.2)
--------------------------------
Each refresh token carries a unique ``jti``. The interface is designed so a
store of revoked/active ``jti`` values (DB column or Redis set) can be consulted
by the endpoint layer: :func:`rotate_refresh_token` returns the previous token's
``jti`` alongside the new token so the caller can revoke the old one, and
logout simply revokes the presented token's ``jti``. The core stays stateless;
persistence of the revocation list is the endpoint layer's concern.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from passlib.context import CryptContext

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Password hashing (Argon2)
# ---------------------------------------------------------------------------

# Argon2 (argon2id by default in passlib) is the design-mandated scheme. The
# CryptContext is created once at import time; it is cheap to reuse and thread
# safe. ``deprecated="auto"`` lets us migrate parameters later via
# ``needs_update`` without breaking existing hashes.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2 and return the encoded hash.

    The returned string is the full PHC-format Argon2 hash (algorithm,
    parameters, salt, and digest). Only this value should be persisted; the
    raw ``password`` is never stored.
    """

    if not isinstance(password, str) or password == "":
        raise ValueError("password must be a non-empty string")
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return ``True`` iff ``password`` matches the stored Argon2 ``password_hash``.

    Any malformed/unknown hash yields ``False`` rather than raising, so callers
    can treat verification failures uniformly as "bad credentials".
    """

    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        # Malformed/unknown hash -> not a match (avoid leaking via exceptions).
        return False


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


class TokenType(str, enum.Enum):
    """Discriminator stored in every token's ``type`` claim."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, mis-signed, or the
    wrong type. Endpoint code maps this to a 401."""


@dataclass(frozen=True)
class TokenPayload:
    """Decoded, validated token claims.

    Attributes:
        sub: The owning user's id (as a string, per JWT convention).
        token_type: ACCESS or REFRESH.
        jti: Unique token id. Present on refresh tokens (used for rotation /
            revocation); also set on access tokens.
        issued_at: ``iat`` as an aware UTC datetime.
        expires_at: ``exp`` as an aware UTC datetime.
        raw: The full decoded claims dict (for any extra claims).
    """

    sub: str
    token_type: TokenType
    jti: str
    issued_at: datetime
    expires_at: datetime
    raw: dict[str, Any]

    @property
    def user_id(self) -> int:
        """Return ``sub`` coerced back to the integer ``users.id``."""

        return int(self.sub)


@dataclass(frozen=True)
class AccessToken:
    """An issued access token plus its decoded metadata."""

    token: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class RefreshToken:
    """An issued refresh token plus its decoded metadata."""

    token: str
    jti: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(
    *,
    subject: str | int,
    token_type: TokenType,
    secret: str,
    algorithm: str,
    ttl_seconds: int,
    jti: str,
    extra_claims: Optional[dict[str, Any]] = None,
) -> tuple[str, datetime]:
    """Encode a JWT and return ``(token, expires_at)``."""

    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    claims: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type.value,
        "jti": jti,
        "iat": issued_at,
        "exp": expires_at,
    }
    if extra_claims:
        # Never allow callers to clobber the reserved claims above.
        reserved = {"sub", "type", "jti", "iat", "exp"}
        for key in extra_claims:
            if key in reserved:
                raise ValueError(f"extra_claims may not override reserved claim {key!r}")
        claims.update(extra_claims)
    token = jwt.encode(claims, secret, algorithm=algorithm)
    return token, expires_at


def issue_access_token(
    user_id: int | str,
    *,
    settings: Optional[Settings] = None,
    extra_claims: Optional[dict[str, Any]] = None,
    jti: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> AccessToken:
    """Issue a short-lived ACCESS token for ``user_id``.

    Signed with ``jwt_secret`` and expiring after ``ttl_seconds`` when given,
    else ``access_token_ttl_seconds``. The user id is placed in ``sub`` and
    ``type`` is set to ``access``. A small ``ttl_seconds`` is used to mint
    very short-lived WebSocket tickets (so a token in a URL leaks for seconds,
    not the full access-token lifetime).
    """

    settings = settings or get_settings()
    token_jti = jti or uuid.uuid4().hex
    token, expires_at = _encode(
        subject=user_id,
        token_type=TokenType.ACCESS,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=(
            ttl_seconds if ttl_seconds is not None
            else settings.access_token_ttl_seconds
        ),
        jti=token_jti,
        extra_claims=extra_claims,
    )
    return AccessToken(token=token, jti=token_jti, expires_at=expires_at)


def issue_refresh_token(
    user_id: int | str,
    *,
    settings: Optional[Settings] = None,
    jti: Optional[str] = None,
) -> RefreshToken:
    """Issue a rotating REFRESH token for ``user_id``.

    Signed with ``jwt_refresh_secret`` and expiring after
    ``refresh_token_ttl_seconds``. Carries a unique ``jti`` so the endpoint
    layer can track, rotate, and revoke individual refresh tokens (logout /
    invalidation in task 4.2). A caller may pass an explicit ``jti`` (rarely
    needed); by default a fresh random one is generated.
    """

    settings = settings or get_settings()
    token_jti = jti or uuid.uuid4().hex
    token, expires_at = _encode(
        subject=user_id,
        token_type=TokenType.REFRESH,
        secret=settings.jwt_refresh_secret,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.refresh_token_ttl_seconds,
        jti=token_jti,
    )
    return RefreshToken(token=token, jti=token_jti, expires_at=expires_at)


def _decode(
    token: str,
    *,
    secret: str,
    algorithm: str,
    expected_type: TokenType,
) -> TokenPayload:
    """Decode/verify a token, enforcing signature, expiry, and ``type``.

    Raises :class:`TokenError` for any problem (expired, bad signature,
    malformed, missing claims, or wrong type).
    """

    if not isinstance(token, str) or not token:
        raise TokenError("token must be a non-empty string")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, malformed token, missing required claims, etc.
        raise TokenError("token is invalid") from exc

    claim_type = claims.get("type")
    if claim_type != expected_type.value:
        raise TokenError(
            f"unexpected token type: expected {expected_type.value!r}, got {claim_type!r}"
        )

    jti = claims.get("jti")
    if not jti:
        raise TokenError("token is missing the 'jti' claim")

    return TokenPayload(
        sub=str(claims["sub"]),
        token_type=TokenType(claim_type),
        jti=str(jti),
        issued_at=datetime.fromtimestamp(claims["iat"], tz=timezone.utc),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
        raw=claims,
    )


def decode_access_token(
    token: str, *, settings: Optional[Settings] = None
) -> TokenPayload:
    """Verify an ACCESS token and return its payload, or raise :class:`TokenError`.

    A refresh token (or any non-access token) presented here is rejected.
    """

    settings = settings or get_settings()
    return _decode(
        token,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expected_type=TokenType.ACCESS,
    )


def decode_refresh_token(
    token: str, *, settings: Optional[Settings] = None
) -> TokenPayload:
    """Verify a REFRESH token and return its payload, or raise :class:`TokenError`.

    An access token (or any non-refresh token) presented here is rejected.
    """

    settings = settings or get_settings()
    return _decode(
        token,
        secret=settings.jwt_refresh_secret,
        algorithm=settings.jwt_algorithm,
        expected_type=TokenType.REFRESH,
    )


@dataclass(frozen=True)
class RotatedTokens:
    """Result of rotating a refresh token.

    Attributes:
        access: A freshly issued access token.
        refresh: A freshly issued refresh token (new ``jti``).
        previous_jti: The ``jti`` of the refresh token that was rotated; the
            endpoint layer should revoke this so it can never be reused.
        user_id: The owning user's id.
    """

    access: AccessToken
    refresh: RefreshToken
    previous_jti: str
    user_id: int


def rotate_refresh_token(
    refresh_token: str, *, settings: Optional[Settings] = None
) -> RotatedTokens:
    """Verify a refresh token and mint a new access + refresh token pair.

    Implements refresh rotation (Requirement 1.5): the presented refresh token
    is verified, then a brand-new refresh token (with a new ``jti``) and a new
    access token are issued for the same user. The previous token's ``jti`` is
    returned as ``previous_jti`` so the caller can invalidate it, preventing
    reuse of the rotated-out token.

    Raises :class:`TokenError` if the presented refresh token is missing,
    expired, mis-signed, malformed, or not a refresh token.
    """

    settings = settings or get_settings()
    payload = decode_refresh_token(refresh_token, settings=settings)
    user_id = payload.user_id
    new_access = issue_access_token(user_id, settings=settings)
    new_refresh = issue_refresh_token(user_id, settings=settings)
    return RotatedTokens(
        access=new_access,
        refresh=new_refresh,
        previous_jti=payload.jti,
        user_id=user_id,
    )
