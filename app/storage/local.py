"""Local-filesystem storage backend (offline MVP).

Persists objects as files under a configurable root directory that lives
*outside* the web root, satisfying Requirements 3.4 and 10.2. Object keys are
mapped to paths beneath the root; path-traversal attempts that would escape the
root are rejected.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .base import ObjectNotFoundError, StorageBackend


class LocalFilesystemStorage(StorageBackend):
    """Store objects as files under a configured root directory.

    Args:
        root: The base directory for all stored objects. It is created if it
            does not already exist. This should point outside the web root so
            uploaded files are never directly served.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The absolute root directory under which objects are stored."""
        return self._root

    def _resolve(self, key: str) -> Path:
        """Map an object key to an absolute path within the root.

        Normalizes the key as a POSIX-style relative path and guarantees the
        result stays inside ``root``; otherwise raises :class:`ValueError` to
        block path traversal (e.g. ``../etc/passwd``).
        """
        if not key or not str(key).strip():
            raise ValueError("Storage key must be a non-empty string")

        # Treat the key as a relative POSIX path regardless of host OS so keys
        # behave identically across platforms.
        normalized = PurePosixPath(str(key).replace("\\", "/"))
        if normalized.is_absolute():
            normalized = normalized.relative_to(normalized.anchor)

        target = (self._root / Path(*normalized.parts)).resolve()

        # Ensure the resolved path is the root itself or a descendant of it.
        if target != self._root and self._root not in target.parents:
            raise ValueError(f"Storage key escapes the configured root: {key!r}")
        return target

    def put(self, key: str, data: bytes) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, key: str) -> bytes:
        target = self._resolve(key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        except IsADirectoryError as exc:
            raise ObjectNotFoundError(key) from exc

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        try:
            target.unlink()
        except FileNotFoundError:
            # Idempotent: deleting a missing key is a no-op.
            return
        except IsADirectoryError:
            return

    def url(self, key: str) -> str:
        target = self._resolve(key)
        return target.as_uri()
