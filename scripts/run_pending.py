"""Process all PENDING contracts in-process (no Celery worker needed).

A convenience for local development on Windows, where running a Celery worker is
fiddly. This finds every contract still in PENDING status and runs the real
``clauseops`` pipeline on each synchronously (the same code the Celery task runs),
persisting clauses/tasks/reminders and flipping the contract to COMPLETE/FAILED.

Usage (after uploading a PDF in the UI):
    venv\\Scripts\\python.exe scripts\\run_pending.py

Re-run it whenever you upload new contracts. It is safe to run repeatedly — it
only touches contracts still in PENDING.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def _pending_ids() -> list[int]:
    from sqlalchemy import select
    from app.data.database import get_sessionmaker
    from app.data.enums import ContractStatus
    from app.data.models import Contract

    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(
                select(Contract.id).where(Contract.status == ContractStatus.PENDING)
            )
        ).scalars().all()
        return list(rows)


def main() -> int:
    from app.processing.ml import process_contract

    ids = asyncio.run(_pending_ids())
    if not ids:
        print("No PENDING contracts to process.")
        return 0

    print(f"Processing {len(ids)} pending contract(s): {ids}")
    for cid in ids:
        print(f"  -> contract {cid} ... ", end="", flush=True)
        try:
            summary = process_contract.run(cid)
            print(
                f"{summary['status']} "
                f"(clauses={summary['clause_count']}, tasks={summary['task_count']})"
            )
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"FAILED: {type(exc).__name__}: {exc}")
    print("Done. Refresh the app to see the results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
