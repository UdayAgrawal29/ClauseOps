"""
Generate Classification Output Markdown (Round 2)
Processes 5 NEW sample PDFs, runs segmentation and classification, and writes
a detailed markdown report to CLASSIFICATION_OUTPUTS_2.md.
"""
import os
import sys
from pathlib import Path
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available

OUTPUT_FILE = Path(r"C:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\clauseops\clause_classification\DOCX\CLASSIFICATION_OUTPUTS_2.md")
PDF_BASE = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf")

# 5 NEW test PDFs across different categories (selected smaller files to prevent OOM)
TEST_PDFS = [
    ("License-ChinaRealEstate", PDF_BASE / "Part_I" / "License_Agreements" / "ChinaRealEstateInformationCorp_20090929_F-1_EX-10.32_4771615_EX-10.32_Content License Agreement.pdf"),
    ("License-Euromedia", PDF_BASE / "Part_I" / "License_Agreements" / "EuromediaHoldingsCorp_20070215_10SB12G_EX-10.B(01)_525118_EX-10.B(01)_Content License Agreement.pdf"),
    ("License-Fulucai", PDF_BASE / "Part_I" / "License_Agreements" / "FulucaiProductionsLtd_20131223_10-Q_EX-10.9_8368347_EX-10.9_Content License Agreement.pdf"),
    ("License-Gopage", PDF_BASE / "Part_I" / "License_Agreements" / "GopageCorp_20140221_10-K_EX-10.1_8432966_EX-10.1_Content License Agreement.pdf"),
    ("License-Phoenix", PDF_BASE / "Part_I" / "License_Agreements" / "PhoenixNewMediaLtd_20110421_F-1_EX-10.17_6958322_EX-10.17_Content License Agreement.pdf"),
]

def generate_report():
    if not is_model_available():
        print("Model is not available. Please train it first.")
        return

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Track global stats across all documents
    total_classified = 0
    total_filtered = 0
    total_high_conf = 0
    total_review = 0
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# ClauseOps Phase 2: Classification Output Report (Round 2)\n\n")
        f.write(f"Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("This document tests **5 additional** unseen PDFs using the updated pipeline.\n\n")
        f.write("---\n\n")
        
        for contract_type, pdf_path in TEST_PDFS:
            if not pdf_path.exists():
                print(f"Skipping {contract_type}: File not found at {pdf_path}")
                continue
                
            print(f"Processing {contract_type}...")
            f.write(f"## Document: {contract_type} Agreement\n")
            f.write(f"**Source:** `{pdf_path.name}`\n\n")
            
            # Segment
            t0 = time.time()
            clauses = segment_contract(str(pdf_path))
            seg_time = time.time() - t0
            
            # Classify
            t1 = time.time()
            results = classify_clauses(clauses)
            class_time = time.time() - t1
            
            # Count filtered vs classified
            doc_filtered = sum(1 for r in results if r.get('source') == 'filtered')
            doc_classified = sum(1 for r in results if r.get('source') not in ('filtered', 'pre_labeled'))
            doc_high = sum(1 for r in results if r.get('source') not in ('filtered', 'pre_labeled') and not r.get('needs_review', False))
            doc_review = sum(1 for r in results if r.get('source') not in ('filtered', 'pre_labeled') and r.get('needs_review', False))
            
            total_classified += doc_classified
            total_filtered += doc_filtered
            total_high_conf += doc_high
            total_review += doc_review
            
            f.write(f"**Stats:** {len(clauses)} segments | Seg: {seg_time:.1f}s | Class: {class_time:.1f}s | Filtered: {doc_filtered} | Classified: {doc_classified} (High: {doc_high}, Review: {doc_review})\n\n")
            
            # Write segments
            for i, (chunk, result) in enumerate(zip(clauses, results)):
                if chunk.chunk_type != "CLAUSE":
                    continue
                    
                heading_display = chunk.heading if chunk.heading else "(No heading)"
                conf = result.get('confidence', 0.0)
                pred = result.get('clause_type', 'UNKNOWN')
                needs_review = result.get('needs_review', False)
                source = result.get('source', '')
                
                # Format confidence indicator
                if source == 'filtered':
                    status_icon = "FILTERED"
                elif conf >= 0.75:
                    status_icon = "HIGH"
                elif conf >= 0.45:
                    status_icon = "MEDIUM"
                else:
                    status_icon = "LOW"
                    
                f.write(f"#### Segment {i+1}: {heading_display}\n\n")
                
                f.write("| Property | Value |\n")
                f.write("|---|---|\n")
                f.write(f"| **Predicted Class** | `{pred}` |\n")
                f.write(f"| **Confidence** | {status_icon} **{conf*100:.1f}%** |\n")
                
                if needs_review and 'alternatives' in result:
                    alts = ", ".join([f"{a[0]} ({a[1]*100:.1f}%)" for a in result['alternatives']])
                    f.write(f"| **Alternatives** | {alts} |\n")
                
                f.write(f"| Tokens | {chunk.token_count} |\n")
                f.write(f"| Source | {source} |\n")
                if chunk.is_oversized:
                    f.write(f"| Oversized | Yes |\n")
                f.write("\n")
                
                body_text = chunk.body_text.replace('\n', ' ') if chunk.body_text else "(No body text)"
                # Truncate very long bodies for readability
                if len(body_text) > 500:
                    body_text = body_text[:500] + "... [truncated]"
                f.write("**Body Text:**\n\n")
                f.write(f"> {body_text}\n\n")
                f.write("---\n\n")
        
        # Write summary
        f.write("## SUMMARY\n\n")
        f.write(f"| Metric | Count |\n")
        f.write(f"|---|---|\n")
        f.write(f"| Total Classified | {total_classified} |\n")
        f.write(f"| Total Filtered (Preamble/Signature) | {total_filtered} |\n")
        f.write(f"| High Confidence (>=75%) | {total_high_conf} |\n")
        f.write(f"| Needs Review (<75%) | {total_review} |\n")
        if total_classified > 0:
            f.write(f"| High Confidence Rate | {total_high_conf/total_classified*100:.1f}% |\n")
                
    print(f"\nReport written to: {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_report()
