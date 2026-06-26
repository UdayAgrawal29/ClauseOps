"""Refresh-token revocation / invalidation store.

The auth core (:mod:`app.auth.security`) is intentionally stateless: every
refresh token carries a unique ``jti``. This module is the endpoint-layer
concern -- where "this refresh token is no longer usable" is recorded so logout
(Requirement 1.6) and refresh rotation (Requirement 1.5) actually take effect.

Two backends are provided behind the small :class:`RefreshTokenStore`
interface, selected by ``settings.refresh_store_backend``:

* ``memory`` (default) -- a process-local revoked-``jti`` set. Zero
  infrastructure; correct for the single-process offline MVP and for tests.
* ``redis`` -- a Redis-backed set keyed off ``settings.redis_url``. Shared
  across multiple API worker processes and surviving restarts, so logout and
  rotation hold globally. Enable in production with
  ``CLAUSEOPS_REFRESH_STORE_BACKEND=redis``.

The headline operation is :meth:`RefreshTokenStore.consume` -- an **atomic**
test-and-revoke used by refresh rotation: the first use of a ``jti`` returns
``True`` (and marks it revoked); any concurrent or later reuse returns
``False``. This closes the check-then-revoke race where two requests could both
rotate the same refresh token.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.config import get_settings


class RefreshTokenStore(ABC):
    """Interface for tracking invalidated (revoked) refresh-token ``jti`` values."""

    @abstractmethod
    async def revoke(self, jti: str) -> None:
        """Mark ``jti`` as revoked so any refresh token bearing it is rejected."""

    @abstractmethod
    async def is_revoked(self, jti: str) -> bool:
        """Return ``True`` iff ``jti`` has been revoked."""

    @abstractmethod
    async def consume(self, jti: str) -> bool:
        """Atomically revoke ``jti`` and report whether this was its first use.

        Returns ``True`` when ``jti`` was previously valid (now marked revoked),
        ``False`` when it was already revoked/consumed (a reuse attempt). A
        falsy/empty ``jti`` always returns ``False``.
        """


class InMemoryRefreshTokenStore(RefreshTokenStore):
    """Process-local revocation set guarded by a lock (single-process MVP)."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    async def revoke(self, jti: str) -> None:
        if not jti:
            return
        with self._lock:
            self._revoked.add(jti)

    async def is_revoked(self, jti: str) -> bool:
        if not jti:
            return True
        with self._lock:
            return jti in self._revoked

    async def consume(self, jti: str) -> bool:
        if not jti:
            return False
        with self._lock:
            if jti in self._revoked:
                return False
            self._revoked.add(jti)
            return True

    def clear(self) -> None:
        """Drop all revocation state (used by tests)."""

        with self._lock:
            self._revoked.clear()


class RedisRefreshTokenStore(RefreshTokenStore):
    """Redis-backed revocation set: shared across workers, survives restarts.

    Each revoked ``jti`` is stored as a key ``refresh:revoked:{jti}`` with a TTL
    equal to the refresh-token lifetime, so the set self-prunes as tokens expire.
    The Redis client (``redis.asyncio``) is created lazily on first use, so
    importing this module never requires a running broker.
    """

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis_url = redis_url
        self._ttl = max(1, int(ttl_seconds))
        self._client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis  # lazy import keeps module broker-free

            self._client = aioredis.from_url(self._redis_url)
        return self._client

    @staticmethod
    def _key(jti: str) -> str:
        return f"refresh:revoked:{jti}"

    async def revoke(self, jti: str) -> None:
        if not jti:
            return
        await self._get_client().set(self._key(jti), "1", ex=self._ttl)

    async def is_revoked(self, jti: str) -> bool:
        if not jti:
            return True
        return bool(await self._get_client().exists(self._key(jti)))

    async def consume(self, jti: str) -> bool:
        if not jti:
            return False
        # SET key 1 NX EX ttl -> truthy only when the key did NOT already exist,
        # i.e. this is the first (valid) use; a reuse finds the key present and
        # returns falsy. This is the atomic test-and-revoke.
        created = await self._get_client().set(self._key(jti), "1", nx=True, ex=self._ttl)
        return bool(created)


def _build_store() -> RefreshTokenStore:
    """Construct the configured store (defaults to in-memory)."""

    settings = get_settings()
    if settings.refresh_store_backend.strip().lower() == "redis":
        return RedisRefreshTokenStore(
            settings.redis_url, settings.refresh_token_ttl_seconds
        )
    return InMemoryRefreshTokenStore()


# Module-level singleton so every request in this process shares one store.
_store: RefreshTokenStore = _build_store()


def get_refresh_token_store() -> RefreshTokenStore:
    """Return the shared refresh-token revocation store (overridable in tests)."""

    return _store


__all__ = [
    "RefreshTokenStore",
    "InMemoryRefreshTokenStore",
    "RedisRefreshTokenStore",
    "get_refresh_token_store",
]
