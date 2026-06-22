"""
Dump raw PDF text (independent of our segmenter, via PyMuPDF) AND the complete
pipeline output (clauses → classification → entities → obligations → deadlines →
tasks) for a few PDFs, so we can hand-build ground truth and compare.

Usage:
  python scripts/integration/dump_for_gt.py
Outputs to scripts/integration/_gt/:
  <name>.rawtext.txt        — independent PyMuPDF extraction (ground-truth source)
  <name>.pipeline.md        — full pipeline output incl. EVERY generated task
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available
from clauseops.obligation_detection.deontic_classifier import classify_contract_obligations
from clauseops.obligation_detection.bert_classifier import is_bert_available
from clauseops.obligation_detection.qa_extractor import is_qa_available
from clauseops.obligation_detection.task_generator import generate_tasks_for_contract
from clauseops.obligation_detection.date_normalizer import (
    normalize_contract_dates, extract_anchor_date,
)

OUT = Path(__file__).parent / "_gt"
OUT.mkdir(exist_ok=True)

PDFS = [
    ROOT / "TEST_5" / "BORROWMONEYCOM,INC_06_11_2020-EX-10.1-JOINT VENTURE AGREEMENT.PDF",
    ROOT / "TEST_PDFS" / "EuromediaHoldingsCorp_20070215_10SB12G_EX-10.B(01)_525118_EX-10.B(01)_Content License Agreement.pdf",
    ROOT / "TEST_PDFS" / "IdeanomicsInc_20160330_10-K_EX-10.26_9512211_EX-10.26_Content License Agreement.pdf",
]


def dump_raw_text(pdf: Path) -> None:
    doc = fitz.open(str(pdf))
    parts = []
    for pno in range(doc.page_count):
        parts.append(f"\n===== PAGE {pno + 1} =====\n")
        parts.append(doc.load_page(pno).get_text("text"))
    doc.close()
    (OUT / f"{pdf.stem[:40]}.rawtext.txt").write_text("".join(parts), encoding="utf-8")


def dump_pipeline(pdf: Path) -> None:
    clauses = segment_contract(str(pdf))
    cls = classify_clauses(clauses) if is_model_available() else []
    ents = extract_entities_from_contract(clauses) if is_ner_available() else []

    clauses_data = []
    for i, c in enumerate(clauses):
        ctype = cls[i].get("clause_type", "") if cls and i < len(cls) else ""
        er = ents[i] if ents and i < len(ents) else {}
        clauses_data.append({
            "clause_id": f"clause_{i}",
            "body_text": c.body_text or "",
            "clause_type": ctype,
            "entity_summary": er.get("entity_summary", {}),
            "relations": er.get("relations", []),
            "entities": er.get("entities", []),
            "heading": c.heading or "",
        })

    anchor = extract_anchor_date(clauses_data)
    obligations = classify_contract_obligations(clauses_data) if (is_bert_available() and is_qa_available()) else []
    deadlines = normalize_contract_dates(clauses_data, anchor)
    tasks = generate_tasks_for_contract(clauses_data, contract_name=pdf.name, anchor_date=anchor)

    lines = []
    lines.append(f"# Pipeline dump — {pdf.name}\n")
    lines.append(f"- Anchor date detected: {anchor}\n")
    lines.append(f"- Segments: {len(clauses)} | Obligations: {sum(len(o) for o in obligations)} | "
                 f"Deadlines: {sum(len(d) for d in deadlines)} | **Tasks: {len(tasks)}**\n\n")

    lines.append("## COMPLETE TASK LIST (final pipeline output)\n\n")
    for n, t in enumerate(tasks, 1):
        lines.append(f"### Task {n}: {t.title}\n")
        lines.append(f"- Priority: {t.priority} | Type: {t.obligation_type} | "
                     f"Clause: {t.clause_id} ({t.clause_type})\n")
        lines.append(f"- Obligated party: {t.obligated_party} | Beneficiary: {t.beneficiary}\n")
        lines.append(f"- Due date: {t.due_date} | Date type: {t.date_type} | "
                     f"Requires review: {t.requires_review}\n")
        if t.reminder_dates:
            lines.append(f"- Reminders: {', '.join(str(d) for d in t.reminder_dates)}\n")
        lines.append(f"- Description:\n```\n{t.description}\n```\n\n")

    lines.append("## Per-clause detail (classification + obligations + deadlines)\n\n")
    for i, c in enumerate(clauses):
        if c.chunk_type != "CLAUSE":
            continue
        ob = obligations[i] if i < len(obligations) else []
        dl = deadlines[i] if i < len(deadlines) else []
        ctype = clauses_data[i]["clause_type"]
        lines.append(f"### clause_{i} — {ctype} — {c.heading or '(no heading)'}\n")
        lines.append(f"**Body:** {(c.body_text or '')[:700]}\n\n")
        if ob:
            for o in ob:
                lines.append(f"- OBL [{o.obligation_type}] party=`{o.obligated_party}` "
                             f"action=`{(o.action or o.action_verb)[:160]}` "
                             f"(conf {o.confidence:.2f}, agent_s {o.agent_score:.1f}, act_s {o.action_score:.1f})\n")
        if dl:
            for d in dl:
                lines.append(f"- DATE [{d.date_type}] raw=`{d.raw_text}` -> {d.normalized_date} "
                             f"(review={d.requires_review}, label={d.deadline_label})\n")
        lines.append("\n")

    (OUT / f"{pdf.stem[:40]}.pipeline.md").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    for pdf in PDFS:
        if not pdf.exists():
            print(f"MISSING: {pdf}")
            continue
        print(f"Dumping raw text: {pdf.name}")
        dump_raw_text(pdf)
        print(f"Running pipeline: {pdf.name}")
        dump_pipeline(pdf)
    print(f"Done. Outputs in {OUT}")
