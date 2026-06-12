"""
Run the full ClauseOps pipeline on a folder of PDFs and write a Markdown report.

Usage:
  python scripts/test_full_pipeline.py --input TEST_PDFS --output clauseops/NER/DOCX/PIPELINE_OUTPUTS.md
"""

import argparse
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available


def _truncate(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    flat = text.replace("\n", " ").strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "... [truncated]"


def _collect_pdfs(root: Path) -> list[Path]:
    return sorted([p for p in root.rglob("*.pdf") if p.is_file()])


def _write_header(f, input_root: Path, pdfs: list[Path], clf_ok: bool, ner_ok: bool):
    f.write("# ClauseOps Pipeline Report\n\n")
    f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    f.write(f"Input folder: `{input_root}`\n\n")
    f.write(f"PDF count: {len(pdfs)}\n\n")
    f.write(f"Classification model available: {clf_ok}\n\n")
    f.write(f"NER model available: {ner_ok}\n\n")
    if not ner_ok:
        f.write("NOTE: NER was skipped because `en_core_web_trf` is not installed.\n\n")
    if not clf_ok:
        f.write("NOTE: Classification was skipped because the model is not available.\n\n")
    f.write("---\n\n")


def build_report(input_root: Path, output_path: Path):
    pdfs = _collect_pdfs(input_root)
    clf_ok = is_model_available()
    ner_ok = is_ner_available()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_chunks = 0
    total_clauses = 0
    total_tables = 0
    total_defs = 0
    failed_docs = 0

    with output_path.open("w", encoding="utf-8") as f:
        _write_header(f, input_root, pdfs, clf_ok, ner_ok)

        for pdf in pdfs:
            f.write(f"## Document: {pdf.name}\n\n")
            t0 = time.time()
            try:
                clauses = segment_contract(str(pdf))
                seg_time = time.time() - t0

                classification_results = None
                if clf_ok:
                    t1 = time.time()
                    classification_results = classify_clauses(clauses)
                    class_time = time.time() - t1
                else:
                    class_time = 0.0

                entity_results = None
                if ner_ok:
                    t2 = time.time()
                    entity_results = extract_entities_from_contract(clauses)
                    ner_time = time.time() - t2
                else:
                    ner_time = 0.0

                clause_count = sum(1 for c in clauses if c.chunk_type == "CLAUSE")
                table_count = sum(1 for c in clauses if c.chunk_type == "TABLE")
                def_count = sum(1 for c in clauses if c.chunk_type == "DEFINITION_GROUP")

                total_chunks += len(clauses)
                total_clauses += clause_count
                total_tables += table_count
                total_defs += def_count

                f.write(f"Segments: {len(clauses)} | Clauses: {clause_count} | Tables: {table_count} | Definitions: {def_count}\n\n")
                f.write(f"Timing: segmentation {seg_time:.1f}s")
                if clf_ok:
                    f.write(f", classification {class_time:.1f}s")
                if ner_ok:
                    f.write(f", ner {ner_time:.1f}s")
                f.write("\n\n")

                for i, clause in enumerate(clauses):
                    f.write(f"### Segment {i + 1}\n\n")
                    f.write(f"Heading: {clause.heading or '(No heading)'}\n\n")
                    f.write(f"Chunk Type: {clause.chunk_type}\n\n")
                    f.write(f"Tokens: {clause.token_count} | Oversized: {clause.is_oversized}\n\n")

                    if classification_results and i < len(classification_results):
                        c = classification_results[i]
                        f.write(
                            f"Classification: {c.get('clause_type')} (conf {c.get('confidence', 0):.2f}, source {c.get('source')})\n\n"
                        )
                        if c.get("needs_review") and c.get("alternatives"):
                            alts = ", ".join([f"{a[0]} ({a[1]:.2f})" for a in c["alternatives"]])
                            f.write(f"Alternatives: {alts}\n\n")

                    if entity_results and i < len(entity_results):
                        ent = entity_results[i]
                        summary = ent.get("entity_summary", {})
                        if summary:
                            f.write("Entity Summary:\n\n")
                            for label, values in summary.items():
                                f.write(f"- {label}: {', '.join(values)}\n")
                            f.write("\n")

                    if clause.chunk_type == "TABLE" and clause.table_markdown:
                        f.write("Table Preview:\n\n")
                        f.write("```\n")
                        f.write(_truncate(clause.table_markdown, 800))
                        f.write("\n```\n\n")
                    elif clause.chunk_type == "DEFINITION_GROUP" and clause.definitions:
                        f.write("Definitions (first 5):\n\n")
                        for item in clause.definitions[:5]:
                            f.write(f"- {item.term}: {_truncate(item.definition, 200)}\n")
                        f.write("\n")
                    else:
                        f.write("Body Preview:\n\n")
                        f.write(f"> {_truncate(clause.body_text)}\n\n")

                    f.write("---\n\n")

            except Exception as exc:
                failed_docs += 1
                f.write(f"ERROR: {exc}\n\n")
                f.write("---\n\n")

        f.write("## Summary\n\n")
        f.write(f"Total documents: {len(pdfs)}\n\n")
        f.write(f"Failed documents: {failed_docs}\n\n")
        f.write(f"Total segments: {total_chunks}\n\n")
        f.write(f"Total clauses: {total_clauses}\n\n")
        f.write(f"Total tables: {total_tables}\n\n")
        f.write(f"Total definition groups: {total_defs}\n\n")

    print(f"Report written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run full pipeline and generate report")
    parser.add_argument("--input", type=Path, default=Path("TEST_PDFS"))
    parser.add_argument("--output", type=Path, default=Path("clauseops/NER/DOCX/PIPELINE_OUTPUTS.md"))
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input folder not found: {args.input}")

    build_report(args.input, args.output)


if __name__ == "__main__":
    main()
