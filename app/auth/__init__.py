"""Auth core: password hashing and JWT token logic.

This package holds the framework-agnostic security primitives for the
ClauseOps Auth Service (design "Component 1: Auth Service"):

- Argon2 password hashing / verification (:mod:`app.auth.security`).
- Issue / verify short-lived access tokens and rotating refresh tokens.

The HTTP endpoints and the current-user dependency are built on top of these
primitives by the web layer (task 4.2); keeping the core here lets the worker,
the web layer, and tests share one implementation.
"""

from app.auth.security import (
    AccessToken,
    RefreshToken,
    RotatedTokens,
    TokenError,
    TokenPayload,
    TokenType,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    rotate_refresh_token,
    verify_password,
)

__all__ = [
    "AccessToken",
    "RefreshToken",
    "RotatedTokens",
    "TokenError",
    "TokenPayload",
    "TokenType",
    "decode_access_token",
    "decode_refresh_token",
    "hash_password",
    "issue_access_token",
    "issue_refresh_token",
    "rotate_refresh_token",
    "verify_password",
]
