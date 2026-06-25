"""Unit tests for the auth security core (Argon2 hashing + JWT tokens).

Covers Requirements 1.1, 1.3, 1.5: only-hash storage, access issue/verify,
refresh rotation, and rejection of expired/invalid/wrong-type tokens.
"""

from __future__ import annotations

import time

import jwt
import pytest

from app.auth import security
from app.auth.security import (
    TokenError,
    TokenType,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.config import Settings

USER_ID = 42


@pytest.fixture()
def settings() -> Settings:
    """Isolated settings with short TTLs for expiry testing."""

    return Settings(
        jwt_secret="test-access-secret",
        jwt_refresh_secret="test-refresh-secret",
        jwt_algorithm="HS256",
        access_token_ttl_seconds=900,
        refresh_token_ttl_seconds=60 * 60 * 24 * 14,
    )


# --- Password hashing (Requirement 1.1) ---------------------------------


def test_hash_password_is_argon2_and_not_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2")
    assert "correct horse battery staple" not in hashed


def test_hash_password_is_salted_unique():
    assert hash_password("samepw") != hash_password("samepw")


def test_verify_password_roundtrip():
    hashed = hash_password("s3cret!")
    assert verify_password("s3cret!", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_password_handles_malformed_hash():
    assert verify_password("anything", "not-a-real-hash") is False


def test_hash_password_rejects_empty():
    with pytest.raises(ValueError):
        hash_password("")


# --- Access token issue -> verify (Requirement 1.3) ---------------------


def test_access_token_roundtrip(settings: Settings):
    issued = issue_access_token(USER_ID, settings=settings)
    payload = decode_access_token(issued.token, settings=settings)
    assert payload.user_id == USER_ID
    assert payload.sub == str(USER_ID)
    assert payload.token_type is TokenType.ACCESS
    assert payload.jti == issued.jti


def test_access_token_rejects_tampered_signature(settings: Settings):
    issued = issue_access_token(USER_ID, settings=settings)
    tampered = issued.token[:-3] + ("abc" if issued.token[-3:] != "abc" else "xyz")
    with pytest.raises(TokenError):
        decode_access_token(tampered, settings=settings)


def test_access_token_rejects_wrong_secret(settings: Settings):
    issued = issue_access_token(USER_ID, settings=settings)
    other = Settings(jwt_secret="different", jwt_refresh_secret="x", jwt_algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(issued.token, settings=other)


def test_access_token_rejects_garbage(settings: Settings):
    with pytest.raises(TokenError):
        decode_access_token("not.a.jwt", settings=settings)
    with pytest.raises(TokenError):
        decode_access_token("", settings=settings)


def test_expired_access_token_is_rejected():
    short = Settings(
        jwt_secret="a",
        jwt_refresh_secret="b",
        jwt_algorithm="HS256",
        access_token_ttl_seconds=1,
    )
    issued = issue_access_token(USER_ID, settings=short)
    # Force expiry deterministically by decoding with a leeway-free clock skip.
    time.sleep(1.2)
    with pytest.raises(TokenError):
        decode_access_token(issued.token, settings=short)


# --- Wrong token type is rejected ---------------------------------------


def test_refresh_token_rejected_by_access_decoder(settings: Settings):
    refresh = issue_refresh_token(USER_ID, settings=settings)
    with pytest.raises(TokenError):
        decode_access_token(refresh.token, settings=settings)


def test_access_token_rejected_by_refresh_decoder(settings: Settings):
    access = issue_access_token(USER_ID, settings=settings)
    with pytest.raises(TokenError):
        decode_refresh_token(access.token, settings=settings)


# --- Refresh token issue -> verify --------------------------------------


def test_refresh_token_roundtrip(settings: Settings):
    issued = issue_refresh_token(USER_ID, settings=settings)
    payload = decode_refresh_token(issued.token, settings=settings)
    assert payload.user_id == USER_ID
    assert payload.token_type is TokenType.REFRESH
    assert payload.jti == issued.jti


def test_refresh_tokens_have_unique_jti(settings: Settings):
    a = issue_refresh_token(USER_ID, settings=settings)
    b = issue_refresh_token(USER_ID, settings=settings)
    assert a.jti != b.jti


# --- Refresh rotation (Requirement 1.5) ---------------------------------


def test_rotate_refresh_token_issues_new_pair(settings: Settings):
    original = issue_refresh_token(USER_ID, settings=settings)
    rotated = rotate_refresh_token(original.token, settings=settings)

    # A usable new access token for the same user.
    access_payload = decode_access_token(rotated.access.token, settings=settings)
    assert access_payload.user_id == USER_ID

    # A new refresh token with a different jti.
    refresh_payload = decode_refresh_token(rotated.refresh.token, settings=settings)
    assert refresh_payload.user_id == USER_ID
    assert rotated.refresh.jti != original.jti

    # The caller is told which jti to revoke (the rotated-out token).
    assert rotated.previous_jti == original.jti
    assert rotated.user_id == USER_ID


def test_rotate_rejects_access_token(settings: Settings):
    access = issue_access_token(USER_ID, settings=settings)
    with pytest.raises(TokenError):
        rotate_refresh_token(access.token, settings=settings)


def test_rotate_rejects_expired_refresh():
    short = Settings(
        jwt_secret="a",
        jwt_refresh_secret="b",
        jwt_algorithm="HS256",
        refresh_token_ttl_seconds=1,
    )
    issued = issue_refresh_token(USER_ID, settings=short)
    time.sleep(1.2)
    with pytest.raises(TokenError):
        rotate_refresh_token(issued.token, settings=short)


def test_extra_claims_cannot_override_reserved(settings: Settings):
    with pytest.raises(ValueError):
        issue_access_token(USER_ID, settings=settings, extra_claims={"sub": "999"})


def test_token_missing_jti_is_rejected(settings: Settings):
    # Hand-craft a token without a jti to confirm the decoder enforces it.
    import datetime as _dt

    claims = {
        "sub": str(USER_ID),
        "type": TokenType.ACCESS.value,
        "iat": _dt.datetime.now(_dt.timezone.utc),
        "exp": _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=5),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)
