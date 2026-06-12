"""
Generate a full pipeline report for test PDFs.

Runs segmentation -> classification (if available) -> NER (if available)
and writes a Markdown report.

Usage:
    python scripts/test_full_pipeline_report.py --input-dir TEST_PDFS \
        --output clauseops/NER/DOCX/PIPELINE_OUTPUTS.md
"""

import argparse
from pathlib import Path
import time

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available


def _collect_pdfs(input_dir: Path) -> list[Path]:
    pdfs = sorted([p for p in input_dir.glob("*.pdf")])
    # Include uppercase extensions
    pdfs.extend(sorted([p for p in input_dir.glob("*.PDF")]))
    # De-dup
    seen = set()
    unique = []
    for p in pdfs:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        unique.append(p)
    return unique


def _truncate(text: str, limit: int = 500) -> str:
    text = text.replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "... [truncated]"
    return text


def generate_report(input_dir: Path, output_path: Path) -> None:
    pdfs = _collect_pdfs(input_dir)
    if not pdfs:
        raise SystemExit(f"No PDFs found in: {input_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    model_available = is_model_available()
    ner_available = is_ner_available()

    total_docs = 0
    total_clauses = 0

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# ClauseOps Full Pipeline Report\n\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"Input directory: `{input_dir}`\n\n")
        f.write(f"Classification model available: {model_available}\n\n")
        f.write(f"NER model available: {ner_available}\n\n")
        f.write("---\n\n")

        for pdf_path in pdfs:
            total_docs += 1
            f.write(f"## Document: {pdf_path.name}\n\n")

            # Segmentation
            t0 = time.time()
            clauses = segment_contract(str(pdf_path))
            seg_time = time.time() - t0

            # Classification
            classification_results = None
            class_time = 0.0
            if model_available:
                t1 = time.time()
                classification_results = classify_clauses(clauses)
                class_time = time.time() - t1

            # NER
            entity_results = None
            ner_time = 0.0
            if ner_available:
                t2 = time.time()
                entity_results = extract_entities_from_contract(clauses)
                ner_time = time.time() - t2

            clause_count = sum(1 for c in clauses if c.chunk_type == "CLAUSE")
            total_clauses += clause_count

            f.write(f"**Stats:** {len(clauses)} segments | Clauses: {clause_count} | ")
            f.write(f"Seg: {seg_time:.1f}s | Class: {class_time:.1f}s | NER: {ner_time:.1f}s\n\n")

            for i, clause in enumerate(clauses):
                f.write(f"### Segment {i + 1}\n\n")
                f.write(f"**Chunk Type:** {clause.chunk_type}\n\n")
                f.write(f"**Heading:** {clause.heading or '(No heading)'}\n\n")
                f.write(f"**Pages:** {clause.start_page + 1}-{clause.end_page + 1}\n\n")
                f.write(f"**Tokens:** {clause.token_count}\n\n")

                if classification_results and i < len(classification_results):
                    c = classification_results[i]
                    f.write(
                        f"**Class:** {c.get('clause_type')} "
                        f"(conf {c.get('confidence', 0):.2f})\n\n"
                    )

                if entity_results and i < len(entity_results):
                    summary = entity_results[i].get("entity_summary", {})
                    if summary:
                        f.write("**Entity Summary:**\n\n")
                        for label, values in summary.items():
                            f.write(f"- {label}: {', '.join(values)}\n")
                        f.write("\n")
                        
                    relations = entity_results[i].get("relations", [])
                    if relations:
                        f.write("**Extracted Relations:**\n\n")
                        for rel in relations:
                            f.write(f"- {rel['subject']} -> {rel['verb']} -> {rel['object']} ({rel['object_label']})\n")
                        f.write("\n")

                if clause.chunk_type == "TABLE" and clause.table_markdown:
                    f.write("**Table:**\n\n")
                    f.write(f"```\n{_truncate(clause.table_markdown, 1200)}\n```\n\n")
                elif clause.chunk_type == "DEFINITION_GROUP":
                    f.write(f"**Definitions:** {len(clause.definitions)} items\n\n")
                else:
                    f.write("**Body:**\n\n")
                    f.write(f"> {_truncate(clause.body_text or '')}\n\n")

                f.write("---\n\n")

        f.write("# Summary\n\n")
        f.write(f"Documents: {total_docs}\n\n")
        f.write(f"Total clauses: {total_clauses}\n\n")

    print(f"Report written to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a full pipeline report for PDFs")
    parser.add_argument("--input-dir", type=Path, required=True, help="Folder with PDF files")
    parser.add_argument("--output", type=Path, required=True, help="Output report markdown path")
    args = parser.parse_args()

    if not args.input_dir.exists():
        raise SystemExit(f"Input folder not found: {args.input_dir}")

    generate_report(args.input_dir, args.output)


if __name__ == "__main__":
    main()
