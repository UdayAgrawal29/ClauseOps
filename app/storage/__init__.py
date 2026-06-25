"""Swappable object-storage interface for the ClauseOps platform.

Exposes the abstract :class:`StorageBackend` contract, the local-filesystem
implementation used by the offline MVP, and a configuration-driven factory that
selects the backend from the centralized application settings.

Requirement 10.1: all file access goes through the ``put``/``get``/``delete``/
``url`` methods. Requirement 10.2: the MVP persists/retrieves on the local
filesystem.
"""

from __future__ import annotations

from app.config import get_settings

from .base import ObjectNotFoundError, StorageBackend
from .local import LocalFilesystemStorage


def get_storage_backend() -> StorageBackend:
    """Build the configured :class:`StorageBackend`.

    Selection is driven entirely by application settings (which are themselves
    environment-driven) so the backend can be swapped without code changes:

    - ``CLAUSEOPS_STORAGE_BACKEND`` selects the implementation. Defaults to
      ``"local"`` for the offline MVP.
    - ``CLAUSEOPS_STORAGE_ROOT`` sets the local root directory (must be outside
      the web root).

    Raises:
        ValueError: If an unknown backend name is configured.
    """
    settings = get_settings()
    backend = (settings.storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalFilesystemStorage(settings.storage_root)
    raise ValueError(f"Unknown storage backend: {backend!r}")


__all__ = [
    "StorageBackend",
    "LocalFilesystemStorage",
    "ObjectNotFoundError",
    "get_storage_backend",
]
