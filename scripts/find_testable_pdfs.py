"""Ground-check CUAD PDFs and copy the most testable ones into TEST_READY/.

Runs the real clauseops pipeline (loaded once) over a curated sample of
contracts and ranks them by how many *dated* tasks they yield (tasks with a
resolved ``due_date``), since those best exercise the deadline-first UI. The
top contracts are copied into ``TEST_READY/`` at the repo root.

Usage:
    venv\\Scripts\\python.exe scripts\\find_testable_pdfs.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

CUAD_ROOT = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf")
REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "TEST_READY"

# Ensure the repo root is importable so `import app...` works when this script
# is run directly (e.g. `python scripts/find_testable_pdfs.py`).
sys.path.insert(0, str(REPO_ROOT))

# How many candidates to actually run through the (slow) pipeline, and how many
# winners to copy out.
MAX_CANDIDATES = 14
COPY_TOP = 8

# Filename keywords that tend to indicate time-bound commercial contracts with
# explicit/relative dates (payment terms, renewals, delivery windows, etc.).
PREFERRED_KEYWORDS = [
    "maintenance", "service", "supply", "hosting", "reseller", "distributor",
    "distribution", "manufacturing", "lease", "promotion", "sponsorship",
    "development", "license", "agency", "consulting", "transportation",
]


def pick_candidates() -> list[Path]:
    """Choose small, date-likely candidate contracts from the CUAD corpus.

    Large CUAD contracts (100+ pages) make Docling run out of memory and take
    many minutes, so we cap file size as a proxy for page count and prefer the
    smallest matching files.
    """
    MIN_BYTES = 15 * 1024       # skip tiny/odd files
    MAX_BYTES = 180 * 1024      # ~ up to ~15 pages; avoids OOM / very slow runs

    all_pdfs = list(CUAD_ROOT.rglob("*.pdf"))
    scored: list[tuple[int, int, Path]] = []
    for p in all_pdfs:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if not (MIN_BYTES <= size <= MAX_BYTES):
            continue
        name = p.name.lower()
        score = sum(1 for kw in PREFERRED_KEYWORDS if kw in name)
        if score == 0:
            continue
        scored.append((score, size, p))

    # Highest keyword score first, then smallest file first (fast + low memory).
    scored.sort(key=lambda t: (-t[0], t[1]))

    chosen: list[Path] = []
    seen_types: set[str] = set()
    for _score, _size, p in scored:
        type_key = p.name.lower().split("_")[-1][:18]
        if type_key in seen_types:
            continue
        seen_types.add(type_key)
        chosen.append(p)
        if len(chosen) >= MAX_CANDIDATES:
            break
    return chosen


def main() -> int:
    if not CUAD_ROOT.exists():
        print(f"CUAD root not found: {CUAD_ROOT}")
        return 1

    from app.processing.ml import run_pipeline

    candidates = pick_candidates()
    print(f"Selected {len(candidates)} candidates to evaluate.\n")

    results = []
    for i, pdf in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {pdf.name[:70]} ... ", end="", flush=True)
        t0 = time.time()
        try:
            res = run_pipeline(str(pdf), pdf.name, contract_id=0)
            tasks = res.tasks
            dated = sum(1 for t in tasks if getattr(t, "due_date", None) is not None)
            review = sum(1 for t in tasks if getattr(t, "requires_review", False))
            results.append(
                {
                    "pdf": pdf,
                    "clauses": len(res.clauses),
                    "tasks": len(tasks),
                    "dated": dated,
                    "review": review,
                }
            )
            print(f"clauses={len(res.clauses)} tasks={len(tasks)} dated={dated} "
                  f"review={review} ({time.time()-t0:.0f}s)")
        except Exception as exc:  # noqa: BLE001 - we just want to skip bad PDFs
            print(f"FAILED: {type(exc).__name__}: {exc}")

    # Rank: most dated tasks first, then most tasks overall.
    results.sort(key=lambda r: (-r["dated"], -r["tasks"]))

    print("\n==== RANKED RESULTS ====")
    for r in results:
        print(f"  dated={r['dated']:>2}  tasks={r['tasks']:>3}  review={r['review']:>2}  "
              f"clauses={r['clauses']:>3}  {r['pdf'].name[:70]}")

    OUT_DIR.mkdir(exist_ok=True)
    # Prefer contracts with at least one dated task; fall back to highest task
    # count so we always copy something useful.
    winners = [r for r in results if r["dated"] > 0][:COPY_TOP]
    if len(winners) < COPY_TOP:
        extra = [r for r in results if r["dated"] == 0][: COPY_TOP - len(winners)]
        winners += extra

    print(f"\nCopying {len(winners)} contracts into {OUT_DIR} ...")
    for r in winners:
        dest = OUT_DIR / r["pdf"].name
        shutil.copy2(r["pdf"], dest)
        print(f"  + {r['pdf'].name[:70]}  (dated={r['dated']}, tasks={r['tasks']})")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
