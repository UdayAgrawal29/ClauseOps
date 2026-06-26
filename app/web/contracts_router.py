"""Contract Upload & Ingestion HTTP endpoints (design "Component 2").

This module implements the upload half of the contract lifecycle (task 7.1):

* ``POST /contracts`` -- validate an uploaded PDF (size cap, MIME type, and
  ``%PDF`` magic bytes), store the bytes via the swappable
  :class:`~app.storage.base.StorageBackend` under a randomized, non-guessable,
  per-user object key, create an owned :class:`~app.data.models.Contract` in
  status ``PENDING``, enqueue the ML processing chain through the
  :mod:`app.processing.enqueue` seam, and return ``202 { contract_id, job_id }``.

Invalid uploads (too large, wrong MIME, missing ``%PDF`` magic bytes) are
rejected with a descriptive 4xx error and create **no** Contract record and
store **nothing** (Requirements 3.2, 3.3).

``DELETE /contracts/{id}`` (task 7.2) removes an owned contract: ownership is
enforced first, then the Contract row is deleted so the database ON DELETE
CASCADE rules tear down its clauses, tasks, reminders, and notifications, and
finally the stored object is removed via the ``StorageBackend`` (Requirements
3.5, 2.2).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.data.enums import ContractStatus
from app.data.models import Contract
from app.data.ownership import contracts_for_user, require_owned
from app.processing.enqueue import EnqueueProcessing, get_enqueue_processing
from app.storage import StorageBackend, get_storage_backend
from app.web.demo_data import DEMOS, get_demo, read_demo_bytes
from app.web.dependencies import CurrentUser, DbSession, get_settings_dep
from app.web.schemas import (
    ContractDetail,
    ContractSummary,
    ContractUploadResponse,
    DemoContractSummary,
)

# The only MIME type accepted for contract uploads.
_PDF_MIME_TYPE = "application/pdf"
# Leading bytes every well-formed PDF begins with.
_PDF_MAGIC = b"%PDF"

# Pagination guards for the list endpoint.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100

# Chunk size for the capped streaming upload read (1 MB).
_UPLOAD_CHUNK_BYTES = 1024 * 1024

SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
StorageDep = Annotated[StorageBackend, Depends(get_storage_backend)]
EnqueueDep = Annotated[EnqueueProcessing, Depends(get_enqueue_processing)]

router = APIRouter(tags=["contracts"])


def _generate_file_key(user_id: int) -> str:
    """Build a randomized, non-guessable object key namespaced by user.

    The ``uuid4`` component makes the key unpredictable, and namespacing under
    the owner's id keeps each user's objects partitioned. The key is stored
    outside the web root by the storage backend (Requirement 3.4).
    """

    return f"contracts/{user_id}/{uuid.uuid4().hex}.pdf"


async def _store_register_enqueue(
    *,
    session: DbSession,
    storage: StorageBackend,
    enqueue: EnqueueProcessing,
    user_id: int,
    content: bytes,
    filename: str,
    contract_type: str | None,
) -> ContractUploadResponse:
    """Store validated PDF bytes, register a PENDING contract, and enqueue it.

    Shared by the user-upload path and the bundled-demo path so both create an
    owned :class:`~app.data.models.Contract` and dispatch the processing chain
    through exactly the same seam. On a failed registration the just-stored
    object is removed so no orphaned file is left behind.
    """

    file_key = _generate_file_key(user_id)
    # Storage writes are blocking filesystem I/O; run them off the event loop.
    await run_in_threadpool(storage.put, file_key, content)

    contract = Contract(
        user_id=user_id,
        filename=filename,
        file_key=file_key,
        file_size_kb=max(1, round(len(content) / 1024)),
        contract_type=contract_type,
    )
    session.add(contract)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await run_in_threadpool(storage.delete, file_key)
        raise
    await session.refresh(contract)

    job_id = enqueue(contract.id)
    return ContractUploadResponse(contract_id=contract.id, job_id=job_id)


@router.post(
    "/contracts",
    response_model=ContractUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_contract(
    current_user: CurrentUser,
    session: DbSession,
    settings: SettingsDep,
    storage: StorageDep,
    enqueue: EnqueueDep,
    file: Annotated[UploadFile, File(description="The contract PDF to upload.")],
    contract_type: Annotated[str | None, Form()] = None,
) -> ContractUploadResponse:
    """Validate, store, register, and enqueue an uploaded contract PDF.

    Validation runs *before* anything is persisted so a rejected upload leaves
    no Contract record and stores no bytes (Requirements 3.2, 3.3):

    1. MIME check -- the declared content type must be ``application/pdf``.
    2. Size cap -- the file must be at most ``settings.max_upload_bytes`` (20MB).
    3. Magic-byte check -- the content must begin with ``%PDF``.

    On success the bytes are stored under a randomized key, a ``PENDING``
    Contract owned by the current user is created, the processing chain is
    enqueued, and ``202 { contract_id, job_id }`` is returned (Requirement 3.1).
    """

    # 1. MIME check. Reject anything not declaring application/pdf.
    if (file.content_type or "").split(";")[0].strip().lower() != _PDF_MIME_TYPE:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media type: only application/pdf uploads are accepted",
        )

    # Read the body in capped chunks so an oversized upload is rejected WITHOUT
    # ever buffering the whole (potentially multi-GB) body in memory. A spoofed
    # or absent Content-Length cannot smuggle a large file through: we stop as
    # soon as the accumulated size exceeds the cap.
    max_bytes = settings.max_upload_bytes
    # Fast pre-check when the client provides a size (multipart sets file.size).
    if getattr(file, "size", None) is not None and file.size > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the maximum upload size of {max_mb:.0f} MB",
        )

    chunks: list[bytes] = []
    size_bytes = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            max_mb = max_bytes / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the maximum upload size of {max_mb:.0f} MB",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    # 2. Empty files are invalid.
    if size_bytes == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # 3. Magic-byte check (Requirement 3.3). Defends against a PDF MIME type on
    # non-PDF content.
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content is not a valid PDF (missing %PDF magic bytes)",
        )

    # --- Validation passed: store, register, enqueue. ---
    return await _store_register_enqueue(
        session=session,
        storage=storage,
        enqueue=enqueue,
        user_id=current_user.id,
        content=content,
        filename=file.filename or "contract.pdf",
        contract_type=contract_type,
    )


@router.get("/demo-contracts", response_model=list[DemoContractSummary])
async def list_demo_contracts(current_user: CurrentUser) -> list[DemoContractSummary]:
    """List the bundled sample contracts offered on the upload page.

    These ship inside the application package so a hosted instance can let any
    signed-in visitor process a representative contract end-to-end without
    uploading their own file. Sizes are read from the bundled files; an absent
    file is skipped defensively so a packaging slip never breaks the catalog.
    """

    out: list[DemoContractSummary] = []
    for demo in DEMOS:
        try:
            size_kb = max(1, round(demo.path.stat().st_size / 1024))
        except OSError:
            continue  # bundled file missing — omit rather than error
        out.append(
            DemoContractSummary(
                slug=demo.slug,
                title=demo.title,
                contract_type=demo.contract_type,
                description=demo.description,
                size_kb=size_kb,
            )
        )
    return out


@router.post(
    "/demo-contracts/{slug}",
    response_model=ContractUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def process_demo_contract(
    slug: str,
    current_user: CurrentUser,
    session: DbSession,
    storage: StorageDep,
    enqueue: EnqueueDep,
) -> ContractUploadResponse:
    """Process a bundled demo contract as the current user.

    Copies the trusted bundled PDF into the caller's storage under a fresh key,
    creates a normal owned ``PENDING`` contract, and enqueues the same
    processing chain as a real upload. The result is indistinguishable from an
    uploaded contract once processed (it appears in the user's Analysis list,
    tasks, dashboard, etc.).
    """

    demo = get_demo(slug)
    if demo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown demo contract",
        )

    try:
        content = await run_in_threadpool(read_demo_bytes, demo)
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo contract is unavailable",
        )

    return await _store_register_enqueue(
        session=session,
        storage=storage,
        enqueue=enqueue,
        user_id=current_user.id,
        content=content,
        filename=f"{demo.title}.pdf",
        contract_type=demo.contract_type,
    )


@router.get("/contracts", response_model=list[ContractSummary])
async def list_contracts(
    current_user: CurrentUser,
    session: DbSession,
    status_filter: Annotated[
        ContractStatus | None,
        Query(alias="status", description="Optional contract-status filter."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Contract]:
    """List the authenticated user's contracts, newest-first (Requirements 5.4, 2.1).

    The query is scoped to the caller via
    :func:`~app.data.ownership.contracts_for_user`, so only the owner's
    contracts are ever returned. Results are paginated with ``limit``/``offset``
    (capped at :data:`_MAX_LIMIT`) and may be narrowed to a single
    :class:`~app.data.enums.ContractStatus` via the optional ``status`` filter.
    Summaries omit the heavy clauses/tasks collections; the full analysis is
    served by ``GET /contracts/{id}``.
    """

    stmt = contracts_for_user(current_user.id)
    if status_filter is not None:
        stmt = stmt.where(Contract.status == status_filter)
    # Newest-first; id as a stable tiebreaker for rows sharing a timestamp.
    stmt = stmt.order_by(Contract.created_at.desc(), Contract.id.desc())
    stmt = stmt.limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/contracts/{contract_id}", response_model=ContractDetail)
async def get_contract(
    contract_id: int,
    current_user: CurrentUser,
    session: DbSession,
) -> Contract:
    """Return the full analysis for an owned contract (Requirements 5.4, 2.1).

    Ownership is enforced first via :func:`~app.data.ownership.require_owned`: a
    missing or non-owned contract raises
    :class:`~app.data.ownership.OwnershipError`, which the app maps to a uniform
    404 (no data leaked). On success the contract is returned together with its
    clauses and tasks, eager-loaded with ``selectinload`` so the async response
    serialization never triggers a lazy load. Each task carries its grounding
    span offsets (``agent_*``/``action_*``) for the source-span viewer.
    """

    # Ownership guard: missing or non-owned -> OwnershipError -> 404.
    await require_owned(session, Contract, contract_id, current_user.id)

    # Re-load the owned contract with clauses and tasks eager-loaded so the
    # async serialization does not lazy-load relationships outside the session.
    stmt = (
        contracts_for_user(current_user.id)
        .where(Contract.id == contract_id)
        .options(
            selectinload(Contract.clauses),
            selectinload(Contract.tasks),
        )
    )
    contract = (await session.execute(stmt)).scalar_one()

    # Present clauses in document order and tasks oldest-first for stability.
    contract.clauses.sort(key=lambda c: (c.clause_index, c.id))
    contract.tasks.sort(key=lambda t: t.id)
    return contract


@router.delete(
    "/contracts/{contract_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_contract(
    contract_id: int,
    current_user: CurrentUser,
    session: DbSession,
    storage: StorageDep,
) -> Response:
    """Delete an owned contract, its derived data, and its stored file.

    Ownership is enforced first via :func:`~app.data.ownership.require_owned`: a
    contract that does not exist or belongs to another user raises
    :class:`~app.data.ownership.OwnershipError`, which the app maps to a 404 with
    **no deletion performed** (Requirements 2.2, 3.5). The same response for
    "missing" and "not owned" avoids leaking other tenants' ids.

    On success the Contract row is deleted and the database ON DELETE CASCADE
    rules tear down its clauses, tasks, and (transitively) the tasks' reminders
    and notifications in one transaction. The matching ORM relationships use
    ``passive_deletes=True`` so the cascade is driven by the database rather than
    by per-row ORM loads. Only after the delete commits is the stored object
    removed via :meth:`StorageBackend.delete`, which is idempotent, so a missing
    object never turns a successful deletion into an error.
    """

    # Ownership guard: missing or non-owned -> OwnershipError -> 404, no delete.
    contract = await require_owned(session, Contract, contract_id, current_user.id)

    # Capture the file key before the row is gone so we can clean up storage.
    file_key = contract.file_key

    # Issue the delete; DB ON DELETE CASCADE removes clauses, tasks, and the
    # tasks' reminders/notifications (relationships use passive_deletes=True).
    await session.delete(contract)
    await session.commit()

    # Remove the stored object only after the DB change is durable. delete() is
    # idempotent, so a no-op (already-absent) object is fine. Run the blocking
    # filesystem delete off the event loop.
    await run_in_threadpool(storage.delete, file_key)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
