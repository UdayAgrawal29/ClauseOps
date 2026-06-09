"""
Quick Entity Extraction Test

Usage:
    python scripts/test_entity_extraction.py path/to/contract.pdf --output output.md
"""

import argparse
from pathlib import Path
import time

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available


def build_report(pdf_path: Path, output_path: Path):
    clauses = segment_contract(str(pdf_path))

    classification_results = None
    if is_model_available():
        classification_results = classify_clauses(clauses)

    entity_results = None
    if is_ner_available():
        entity_results = extract_entities_from_contract(clauses)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# ClauseOps Phase 3: Entity Extraction Output\n\n")
        f.write(f"Source: `{pdf_path.name}`\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        for i, clause in enumerate(clauses):
            f.write(f"## Segment {i + 1}\n\n")
            f.write(f"**Heading:** {clause.heading or '(No heading)'}\n\n")
            f.write(f"**Chunk Type:** {clause.chunk_type}\n\n")

            if classification_results and i < len(classification_results):
                c = classification_results[i]
                f.write(f"**Class:** {c.get('clause_type')} (conf {c.get('confidence', 0):.2f})\n\n")

            if entity_results and i < len(entity_results):
                ent = entity_results[i]
                summary = ent.get("entity_summary", {})
                if summary:
                    f.write("**Entity Summary:**\n\n")
                    for label, values in summary.items():
                        f.write(f"- {label}: {', '.join(values)}\n")
                    f.write("\n")
                else:
                    f.write("**Entity Summary:** (none)\n\n")

            body_text = clause.body_text.replace("\n", " ") if clause.body_text else "(No body text)"
            if len(body_text) > 600:
                body_text = body_text[:600] + "... [truncated]"
            f.write("**Body Text:**\n\n")
            f.write(f"> {body_text}\n\n")
            f.write("---\n\n")

    print(f"Report written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run entity extraction on a PDF contract")
    parser.add_argument("pdf", type=Path, help="Path to PDF contract")
    parser.add_argument("--output", type=Path, default=Path("entity_extraction_output.md"))
    args = parser.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")

    build_report(args.pdf, args.output)


if __name__ == "__main__":
    main()
