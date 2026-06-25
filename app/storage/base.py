"""Storage backend abstraction.

Defines the provider-agnostic :class:`StorageBackend` interface that the rest of
the platform uses to read and write uploaded contract files. The MVP ships a
local-filesystem implementation; managed backends (R2/Supabase) can be slotted
in later via configuration without touching any calling code.

Design reference: design.md "Component 3: Storage Interface" and Requirement 10.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ObjectNotFoundError(KeyError):
    """Raised when a requested object key does not exist in the backend.

    Subclasses :class:`KeyError` so callers can catch either, giving a single,
    well-defined error type instead of leaking backend-specific exceptions such
    as :class:`FileNotFoundError`.
    """


class StorageBackend(ABC):
    """Abstract object-storage interface.

    All file access in the platform flows through these four methods so that the
    underlying provider can be swapped via configuration alone (Requirement
    10.1). Implementations must keep this contract identical so no calling code
    changes when the backend changes.
    """

    @abstractmethod
    def put(self, key: str, data: bytes) -> None:
        """Store ``data`` under ``key``, overwriting any existing object.

        Args:
            key: The object key. May contain ``/`` separators to namespace
                objects; implementations must keep all objects within their
                configured root.
            data: The raw bytes to persist.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key``.

        Raises:
            ObjectNotFoundError: If no object exists for ``key``.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object stored under ``key``.

        Deleting a missing key is a no-op (idempotent); implementations must not
        raise when the key is absent.
        """
        raise NotImplementedError

    @abstractmethod
    def url(self, key: str) -> str:
        """Return a streamable/serveable reference to the object for ``key``.

        For the local backend this is a ``file://`` URI; managed backends would
        return a signed URL. The key need not exist for a reference to be
        produced.
        """
        raise NotImplementedError
