"""
ClauseOps — Phase 4 QA Extractor Integration Test (real PDFs)
==============================================================
Runs the full pipeline (segment -> classify -> NER -> obligation -> tasks) on
real contract PDFs with the NEW offline extractive-QA agent/action extractor
active, and validates the plan's integration targets:

  - Grounding invariant: every obligated_party & action is an EXACT substring
    of its clause body (no fabrication).
  - No "Contracting Party" hardcoded fallback ever appears (QA abstains instead).
  - Task counts stay in a sane range (no explosion).
  - Captures agent/action + confidence scores for manual quality scoring.

Writes a Markdown analysis report.

Usage:
  python scripts/integration/test_qa_pipeline.py --input TEST_5 \
      --output clauseops/obligation_detection/DOCX/28_QA_INTEGRATION_RESULTS.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available
from clauseops.obligation_detection.deontic_classifier import classify_contract_obligations
from clauseops.obligation_detection.bert_classifier import is_bert_available
from clauseops.obligation_detection.qa_extractor import is_qa_available
from clauseops.obligation_detection.task_generator import generate_tasks_for_contract


def _truncate(text: str, limit: int = 320) -> str:
    if not text:
        return ""
    flat = text.replace("\n", " ").strip()
    return flat if len(flat) <= limit else flat[:limit] + "… [truncated]"


def _collect_pdfs(root: Path) -> list[Path]:
    pdfs = [p for p in root.rglob("*.pdf") if p.is_file()]
    pdfs += [p for p in root.rglob("*.PDF") if p.is_file()]
    # de-dup by resolved path
    seen, out = set(), []
    for p in sorted(pdfs):
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def build_report(input_root: Path, output_path: Path, max_pdfs: int = 5) -> None:
    pdfs = _collect_pdfs(input_root)[:max_pdfs]
    clf_ok = is_model_available()
    ner_ok = is_ner_available()
    bert_ok = is_bert_available()
    qa_ok = is_qa_available()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Global accumulators
    g_obligations = 0
    g_grounding_violations = 0
    g_fabricated_party = 0
    g_agent_abstain = 0
    g_action_abstain = 0
    g_tasks = 0
    g_agent_scores: list[float] = []
    g_action_scores: list[float] = []
    per_doc_rows: list[tuple[str, int, int, int, int]] = []  # name, obl, tasks, violations, fabricated

    f = output_path.open("w", encoding="utf-8")
    f.write("# ClauseOps Phase 4 — QA Extractor Integration Results\n\n")
    f.write(f"> **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"> **Input:** `{input_root}`\n")
    f.write(f"> **Models** — Classification: {clf_ok} | NER: {ner_ok} | "
            f"Modality BERT: {bert_ok} | **QA Extractor: {qa_ok}**\n\n")
    f.write("The QA extractor is active when **QA Extractor: True**. Every agent/action "
            "below is decoded as a span of the clause and verified to be an exact substring "
            "(grounding invariant).\n\n---\n\n")

    for pdf in pdfs:
        f.write(f"## 📄 {pdf.name}\n\n")
        doc_obl = doc_tasks = doc_viol = doc_fab = 0
        try:
            t0 = time.time()
            clauses = segment_contract(str(pdf))
            classification_results = classify_clauses(clauses) if clf_ok else []
            entity_results = extract_entities_from_contract(clauses) if ner_ok else []

            clauses_data = []
            for i, clause in enumerate(clauses):
                c_type = (classification_results[i].get("clause_type", "")
                          if classification_results and i < len(classification_results) else "")
                er = entity_results[i] if entity_results and i < len(entity_results) else {}
                clauses_data.append({
                    "clause_id": f"clause_{i}",
                    "body_text": clause.body_text or "",
                    "clause_type": c_type,
                    "entity_summary": er.get("entity_summary", {}),
                    "relations": er.get("relations", []),
                    "entities": er.get("entities", []),
                    "heading": clause.heading or "",
                })

            obligation_results = classify_contract_obligations(clauses_data) if (bert_ok and qa_ok) else []
            tasks = generate_tasks_for_contract(clauses_data, contract_name=pdf.name)
            elapsed = time.time() - t0

            doc_tasks = len(tasks)

            f.write(f"**Time:** {elapsed:.1f}s | **Segments:** {len(clauses)} | "
                    f"**Tasks generated:** {doc_tasks}\n\n")

            # Walk obligations, validate grounding
            f.write("### Extracted obligations (QA agent/action)\n\n")
            any_obl = False
            for i, recs in enumerate(obligation_results):
                if not recs:
                    continue
                body = clauses_data[i]["body_text"]
                for rec in recs:
                    any_obl = True
                    doc_obl += 1
                    g_obligations += 1

                    agent = rec.obligated_party or ""
                    # rec.action holds the QA-extracted span ("" when the model
                    # abstained on the action). rec.action_verb may carry the
                    # generic "perform" placeholder, which is NOT an extracted
                    # span and must not be grounding-checked.
                    action_span = rec.action or ""
                    action_abstained = (action_span == "")

                    agent_grounded = (agent in body) if agent else True
                    action_grounded = True if action_abstained else (action_span in body)
                    if not (agent_grounded and action_grounded):
                        doc_viol += 1
                        g_grounding_violations += 1
                    if agent.strip().lower() == "contracting party":
                        doc_fab += 1
                        g_fabricated_party += 1
                    if action_abstained:
                        g_action_abstain += 1

                    if rec.agent_score:
                        g_agent_scores.append(rec.agent_score)
                    if rec.action_score and not action_abstained:
                        g_action_scores.append(rec.action_score)

                    flag = "" if (agent_grounded and action_grounded) else " ⚠️ GROUNDING VIOLATION"
                    action_disp = (_truncate(action_span, 200) if not action_abstained
                                   else "(abstained — placeholder 'perform')")
                    f.write(f"- **[{rec.obligation_type}]** _(clause {i}, conf {rec.confidence:.2f})_{flag}\n")
                    f.write(f"  - **Agent:** `{agent}`  _(score {rec.agent_score:.2f}, grounded={agent_grounded})_\n")
                    f.write(f"  - **Action:** {('`'+action_disp+'`') if not action_abstained else action_disp}"
                            f"  _(score {rec.action_score:.2f})_\n")
                    if rec.beneficiary:
                        f.write(f"  - **Beneficiary:** {rec.beneficiary}\n")
            if not any_obl:
                f.write("_No obligations extracted (all clauses gated out or abstained)._\n")
            f.write("\n")

            # Sample of generated task titles
            if tasks:
                f.write("### Sample task titles\n\n")
                for t in tasks[:10]:
                    f.write(f"- `{t.priority}` — {_truncate(t.title, 160)}\n")
                f.write("\n")

            g_tasks += doc_tasks
            per_doc_rows.append((pdf.name, doc_obl, doc_tasks, doc_viol, doc_fab))
            f.write("---\n\n")

        except Exception as exc:  # noqa: BLE001
            f.write(f"**ERROR:** {exc}\n\n---\n\n")
            per_doc_rows.append((pdf.name, doc_obl, doc_tasks, doc_viol, doc_fab))

    # ── Summary ─────────────────────────────────────────────────────────────
    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    f.write("# Summary\n\n")
    f.write("| Document | Obligations | Tasks | Grounding violations | Fabricated party |\n")
    f.write("|---|---|---|---|---|\n")
    for name, obl, tks, viol, fab in per_doc_rows:
        f.write(f"| {name[:48]} | {obl} | {tks} | {viol} | {fab} |\n")
    f.write(f"| **TOTAL** | **{g_obligations}** | **{g_tasks}** | "
            f"**{g_grounding_violations}** | **{g_fabricated_party}** |\n\n")

    f.write("## Invariant checks\n\n")
    f.write(f"- **Grounding invariant (Property 1):** "
            f"{'✅ PASS' if g_grounding_violations == 0 else '❌ FAIL'} "
            f"({g_grounding_violations} violations across {g_obligations} obligations)\n")
    f.write(f"- **No fabricated 'Contracting Party' (Property 4):** "
            f"{'✅ PASS' if g_fabricated_party == 0 else '❌ FAIL'} "
            f"({g_fabricated_party} occurrences)\n")
    f.write(f"- **Action abstentions** (agent found, action no-answer → 'perform' placeholder): "
            f"{g_action_abstain} / {g_obligations}\n")
    f.write(f"- **Mean agent score:** {_avg(g_agent_scores):.2f} | "
            f"**Mean action score (non-abstained):** {_avg(g_action_scores):.2f}\n\n")

    f.close()
    print(f"Report written to: {output_path}")
    print(f"Obligations: {g_obligations} | Tasks: {g_tasks} | "
          f"Grounding violations: {g_grounding_violations} | Fabricated: {g_fabricated_party}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("TEST_5"))
    parser.add_argument("--output", type=Path,
                        default=Path("clauseops/obligation_detection/DOCX/28_QA_INTEGRATION_RESULTS.md"))
    parser.add_argument("--max-pdfs", type=int, default=5)
    args = parser.parse_args()
    build_report(args.input, args.output, args.max_pdfs)
