"""Unit tests for the local-filesystem storage backend.

Covers the swappable ``StorageBackend`` contract as implemented by
``LocalFilesystemStorage`` (app/storage):

- put/get/delete/url round-trips, including nested/subdir keys
- missing-key handling (get raises ``ObjectNotFoundError``; delete is idempotent)
- files persist under the configured root, and the root is outside the web root
- path-traversal keys are rejected with ``ValueError``

Requirements: 10.1 (all file access flows through put/get/delete/url),
10.2 (MVP persists/retrieves on the local filesystem under a root outside the
web root).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import (
    LocalFilesystemStorage,
    ObjectNotFoundError,
    StorageBackend,
)


def test_is_storage_backend(tmp_path: Path) -> None:
    """The local backend implements the abstract StorageBackend contract."""
    storage = LocalFilesystemStorage(tmp_path)
    assert isinstance(storage, StorageBackend)


def test_init_creates_root(tmp_path: Path) -> None:
    """Constructing the backend creates the root directory if absent."""
    root = tmp_path / "does" / "not" / "exist" / "yet"
    assert not root.exists()

    storage = LocalFilesystemStorage(root)

    assert root.exists()
    assert storage.root == root.resolve()


# --- put/get round-trips -------------------------------------------------


def test_put_get_round_trip(tmp_path: Path) -> None:
    """Bytes written under a key are returned verbatim by get."""
    storage = LocalFilesystemStorage(tmp_path)
    payload = b"%PDF-1.7 fake contract bytes\x00\x01\x02"

    storage.put("contract.pdf", payload)

    assert storage.get("contract.pdf") == payload


def test_put_get_round_trip_nested_key(tmp_path: Path) -> None:
    """Keys with subdirectories round-trip and create intermediate dirs."""
    storage = LocalFilesystemStorage(tmp_path)
    payload = b"nested object bytes"

    storage.put("users/42/contracts/abc123.pdf", payload)

    assert storage.get("users/42/contracts/abc123.pdf") == payload


def test_put_overwrites_existing_object(tmp_path: Path) -> None:
    """Writing the same key twice overwrites the previous contents."""
    storage = LocalFilesystemStorage(tmp_path)
    storage.put("key", b"first")
    storage.put("key", b"second")

    assert storage.get("key") == b"second"


def test_put_empty_bytes(tmp_path: Path) -> None:
    """Storing empty bytes round-trips to empty bytes."""
    storage = LocalFilesystemStorage(tmp_path)
    storage.put("empty", b"")

    assert storage.get("empty") == b""


# --- delete --------------------------------------------------------------


def test_delete_removes_object(tmp_path: Path) -> None:
    """After delete, the object is gone and get raises ObjectNotFoundError."""
    storage = LocalFilesystemStorage(tmp_path)
    storage.put("temp.pdf", b"data")

    storage.delete("temp.pdf")

    with pytest.raises(ObjectNotFoundError):
        storage.get("temp.pdf")


def test_delete_missing_key_is_noop(tmp_path: Path) -> None:
    """Deleting a missing key does not raise (idempotent)."""
    storage = LocalFilesystemStorage(tmp_path)

    # Neither the first nor a repeated delete should raise.
    storage.delete("never-existed")
    storage.put("present", b"x")
    storage.delete("present")
    storage.delete("present")


# --- missing-key handling ------------------------------------------------


def test_get_missing_key_raises(tmp_path: Path) -> None:
    """get on an unknown key raises ObjectNotFoundError."""
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(ObjectNotFoundError):
        storage.get("missing.pdf")


def test_object_not_found_is_key_error(tmp_path: Path) -> None:
    """ObjectNotFoundError subclasses KeyError for caller convenience."""
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(KeyError):
        storage.get("missing.pdf")


# --- url -----------------------------------------------------------------


def test_url_returns_file_uri(tmp_path: Path) -> None:
    """url returns a file:// URI for the object."""
    storage = LocalFilesystemStorage(tmp_path)
    storage.put("doc.pdf", b"data")

    uri = storage.url("doc.pdf")

    assert uri.startswith("file://")
    assert uri.endswith("doc.pdf")


def test_url_does_not_require_existing_object(tmp_path: Path) -> None:
    """A reference can be produced even when the key does not exist yet."""
    storage = LocalFilesystemStorage(tmp_path)

    uri = storage.url("not-yet-written.pdf")

    assert uri.startswith("file://")


def test_url_round_trips_to_stored_file(tmp_path: Path) -> None:
    """The file:// URI points at the file that holds the stored bytes."""
    import urllib.parse
    import urllib.request

    storage = LocalFilesystemStorage(tmp_path)
    payload = b"locate me"
    storage.put("sub/dir/located.bin", payload)

    uri = storage.url("sub/dir/located.bin")
    # Convert the file:// URI back to a local filesystem path portably.
    parsed = urllib.parse.urlparse(uri)
    local_path = Path(urllib.request.url2pathname(parsed.path))

    assert local_path.read_bytes() == payload


# --- persistence under the configured root -------------------------------


def test_files_persist_under_configured_root(tmp_path: Path) -> None:
    """Stored objects live as real files beneath the configured root."""
    storage = LocalFilesystemStorage(tmp_path)
    storage.put("a/b/c.pdf", b"persisted")

    on_disk = tmp_path / "a" / "b" / "c.pdf"
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"persisted"
    # The file is a descendant of the configured root.
    assert storage.root in on_disk.resolve().parents


def test_root_is_outside_web_root(tmp_path: Path) -> None:
    """The storage root is a standalone directory, not under a served web root.

    Using a tmp_path-based root guarantees the location is isolated from any
    application/static web root, satisfying the "outside the web root"
    requirement (10.2 / 3.4).
    """
    web_root = tmp_path / "app" / "static"
    web_root.mkdir(parents=True)
    storage_root = tmp_path / "var" / "storage"

    storage = LocalFilesystemStorage(storage_root)
    storage.put("contract.pdf", b"data")

    stored_file = (storage_root / "contract.pdf").resolve()
    # The stored file must not reside within the web root.
    assert web_root.resolve() not in stored_file.parents
    assert stored_file.exists()


# --- path traversal ------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "../escape.pdf",
        "../../etc/passwd",
        "sub/../../escape.pdf",
        "a/b/../../../escape",
    ],
)
def test_path_traversal_rejected(tmp_path: Path, key: str) -> None:
    """Keys that escape the configured root raise ValueError."""
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.put(key, b"malicious")


@pytest.mark.parametrize("key", ["", "   ", "\t"])
def test_empty_key_rejected(tmp_path: Path, key: str) -> None:
    """Empty/whitespace keys raise ValueError."""
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.put(key, b"data")


def test_traversal_does_not_write_outside_root(tmp_path: Path) -> None:
    """A rejected traversal key leaves nothing written outside the root."""
    root = tmp_path / "root"
    storage = LocalFilesystemStorage(root)
    sibling = tmp_path / "escape.pdf"

    with pytest.raises(ValueError):
        storage.put("../escape.pdf", b"malicious")

    assert not sibling.exists()
