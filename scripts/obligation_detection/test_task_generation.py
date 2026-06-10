"""
ClauseOps — Phase 4 End-to-End Test Script

Runs the FULL pipeline (Segmentation → Classification → NER → Obligation
Detection → Date Normalization → Task Generation) on PDFs and outputs
a comprehensive TASK_OUTPUTS.md report.

Usage:
    python scripts/test_task_generation.py
    python scripts/test_task_generation.py --input-dir path/to/pdfs
    python scripts/test_task_generation.py --input-dir path/to/pdfs --output path/to/report.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available
from clauseops.obligation_detection import (
    generate_tasks_for_contract,
    extract_anchor_date,
    TaskRecord,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
1
# ─── Default paths ───────────────────────────────────────────────────────────
DEFAULT_INPUT = Path(__file__).parent.parent / "clauseops" / "entity_extraction" / "TEST_PDFS_MIXED"
DEFAULT_OUTPUT = Path(__file__).parent.parent / "clauseops" / "obligation_detection" / "DOCX" / "TASK_OUTPUTS_V2.md"


def process_pdf(pdf_path: Path) -> tuple[str, list[TaskRecord], dict]:
    """
    Run full pipeline on a single PDF and return tasks.

    Returns: (filename, tasks, stats_dict)
    """
    filename = pdf_path.name
    logger.info("Processing: %s", filename)

    stats = {
        "seg_time": 0.0,
        "cls_time": 0.0,
        "ner_time": 0.0,
        "task_time": 0.0,
        "segments": 0,
        "tasks": 0,
        "anchor_date": None,
    }

    # Step 1: Segment
    t0 = time.time()
    try:
        clauses = segment_contract(str(pdf_path))
    except Exception as e:
        logger.error("Segmentation failed for %s: %s", filename, e)
        return filename, [], stats
    stats["seg_time"] = time.time() - t0
    stats["segments"] = len(clauses)

    if not clauses:
        logger.warning("No segments found for %s", filename)
        return filename, [], stats

    # Step 2: Classify
    t0 = time.time()
    classification_results = []
    if is_model_available():
        try:
            classification_results = classify_clauses(clauses)
        except Exception as e:
            logger.error("Classification failed: %s", e)
    stats["cls_time"] = time.time() - t0

    # Step 3: NER
    t0 = time.time()
    entity_results = []
    if is_ner_available():
        try:
            entity_results = extract_entities_from_contract(clauses)
        except Exception as e:
            logger.error("NER failed: %s", e)
    stats["ner_time"] = time.time() - t0

    # Step 4: Build unified clause data for Phase 4
    clauses_data = []
    for i, clause in enumerate(clauses):
        cd = {
            "clause_id": clause.clause_id,
            "body_text": clause.body_text,
            "heading": clause.heading or "",
            "chunk_type": clause.chunk_type,
        }

        # Classification
        if classification_results and i < len(classification_results):
            cr = classification_results[i]
            cd["clause_type"] = cr.get("label", "")
            cd["classification_confidence"] = cr.get("confidence", 0.0)
        else:
            cd["clause_type"] = ""

        # NER
        if entity_results and i < len(entity_results):
            er = entity_results[i]
            cd["entities"] = er.get("entities", [])
            cd["entity_summary"] = er.get("entity_summary", {})
            cd["relations"] = er.get("relations", [])
        else:
            cd["entities"] = []
            cd["entity_summary"] = {}
            cd["relations"] = []

        clauses_data.append(cd)

    # Step 5: Task Generation
    t0 = time.time()
    try:
        tasks = generate_tasks_for_contract(clauses_data, contract_name=filename)
        anchor = extract_anchor_date(clauses_data)
        stats["anchor_date"] = anchor.isoformat() if anchor else "NOT FOUND"
    except Exception as e:
        logger.error("Task generation failed: %s", e)
        tasks = []
    stats["task_time"] = time.time() - t0
    stats["tasks"] = len(tasks)

    return filename, tasks, stats


def write_report(
    results: list[tuple[str, list[TaskRecord], dict]],
    output_path: Path,
):
    """Write a comprehensive markdown report."""
    lines = [
        "# ClauseOps Phase 4 — Task Generation Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Contract | Segments | Tasks | Anchor Date | Time |")
    lines.append("|---|---|---|---|---|")

    total_tasks = 0
    for filename, tasks, stats in results:
        total_time = stats["seg_time"] + stats["cls_time"] + stats["ner_time"] + stats["task_time"]
        lines.append(
            f"| {filename[:50]} | {stats['segments']} | {len(tasks)} | "
            f"{stats.get('anchor_date', 'N/A')} | {total_time:.1f}s |"
        )
        total_tasks += len(tasks)

    lines.append(f"| **TOTAL** | | **{total_tasks}** | | |")
    lines.append("")

    # Priority distribution
    priorities = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    date_types = {"ABSOLUTE": 0, "RELATIVE": 0, "RECURRING": 0, "CONDITIONAL": 0, "NONE": 0}
    obligation_types = {}
    review_count = 0

    for _, tasks, _ in results:
        for t in tasks:
            priorities[t.priority] = priorities.get(t.priority, 0) + 1
            date_types[t.date_type] = date_types.get(t.date_type, 0) + 1
            obligation_types[t.obligation_type] = obligation_types.get(t.obligation_type, 0) + 1
            if t.requires_review:
                review_count += 1

    lines.append("## Distribution")
    lines.append("")
    lines.append("### By Priority")
    lines.append("")
    for p, c in priorities.items():
        bar = "█" * min(c, 30)
        lines.append(f"- **{p}**: {c} {bar}")
    lines.append("")

    lines.append("### By Date Type")
    lines.append("")
    for dt, c in date_types.items():
        lines.append(f"- **{dt}**: {c}")
    lines.append("")

    lines.append("### By Obligation Type")
    lines.append("")
    for ot, c in obligation_types.items():
        lines.append(f"- **{ot}**: {c}")
    lines.append("")

    lines.append(f"**Requires Human Review**: {review_count} tasks")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-contract detailed tasks
    for filename, tasks, stats in results:
        lines.append(f"## Document: {filename}")
        lines.append("")
        lines.append(f"**Anchor Date:** {stats.get('anchor_date', 'NOT FOUND')}")
        lines.append(f"**Segments:** {stats['segments']} | **Tasks Generated:** {len(tasks)}")
        lines.append(f"**Pipeline Time:** Seg {stats['seg_time']:.1f}s | "
                      f"Cls {stats['cls_time']:.1f}s | "
                      f"NER {stats['ner_time']:.1f}s | "
                      f"Task {stats['task_time']:.1f}s")
        lines.append("")

        if not tasks:
            lines.append("*No tasks generated.*")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        for j, task in enumerate(tasks, 1):
            priority_emoji = {
                "CRITICAL": "🔴",
                "HIGH": "🟠",
                "MEDIUM": "🟡",
                "LOW": "🟢",
            }.get(task.priority, "⚪")

            lines.append(f"### Task {j} {priority_emoji} [{task.priority}]")
            lines.append("")
            lines.append(f"**Title:** {task.title}")
            lines.append("")
            lines.append(f"| Field | Value |")
            lines.append(f"|---|---|")
            lines.append(f"| Clause | `{task.clause_id}` ({task.clause_type}) |")
            lines.append(f"| Obligation | {task.obligation_type} |")
            lines.append(f"| Obligated Party | {task.obligated_party} |")
            if task.beneficiary:
                lines.append(f"| Beneficiary | {task.beneficiary} |")
            lines.append(f"| Due Date | {task.due_date.isoformat() if task.due_date else 'N/A'} |")
            lines.append(f"| Date Type | {task.date_type} |")
            lines.append(f"| Requires Review | {'⚠️ YES' if task.requires_review else 'No'} |")
            if task.reminder_dates:
                reminders_str = ", ".join(d.isoformat() for d in task.reminder_dates)
                lines.append(f"| Reminders | {reminders_str} |")
            lines.append("")

            # Description
            lines.append(f"<details>")
            lines.append(f"<summary>Full Description</summary>")
            lines.append("")
            lines.append(f"```")
            lines.append(task.description)
            lines.append(f"```")
            lines.append("")
            lines.append(f"</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Write file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report written to: %s", output_path)


def main():
    parser = argparse.ArgumentParser(description="ClauseOps Phase 4 Task Generation Test")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT,
                        help="Directory containing PDF contracts")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Output markdown report path")
    parser.add_argument("--max-files", type=int, default=10,
                        help="Maximum number of PDFs to process")
    args = parser.parse_args()

    if not args.input_dir.exists():
        logger.error("Input directory not found: %s", args.input_dir)
        sys.exit(1)

    pdf_files = sorted(args.input_dir.glob("*.pdf"))[:args.max_files]
    if not pdf_files:
        pdf_files = sorted(args.input_dir.glob("*.PDF"))[:args.max_files]

    if not pdf_files:
        logger.error("No PDF files found in: %s", args.input_dir)
        sys.exit(1)

    logger.info("Found %d PDFs in %s", len(pdf_files), args.input_dir)
    logger.info("Classification model: %s", "Available" if is_model_available() else "NOT AVAILABLE")
    logger.info("NER model: %s", "Available" if is_ner_available() else "NOT AVAILABLE")

    results = []
    for pdf in pdf_files:
        try:
            result = process_pdf(pdf)
            results.append(result)
        except Exception as e:
            logger.error("FATAL error processing %s: %s", pdf.name, e)

    write_report(results, args.output)

    # Print summary to console
    total_tasks = sum(len(tasks) for _, tasks, _ in results)
    print(f"\n{'='*60}")
    print(f"Phase 4 Task Generation Complete")
    print(f"{'='*60}")
    print(f"  Contracts processed: {len(results)}")
    print(f"  Total tasks generated: {total_tasks}")
    print(f"  Report: {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
