"""
Generate Classification Output Markdown
Processes sample PDFs, runs segmentation and classification, and writes
a detailed markdown report to CLASSIFICATION_OUTPUTS.md.
"""
import os
import sys
from pathlib import Path
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available

OUTPUT_FILE = Path(r"C:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\clauseops\clause_classification\DOCX\CLASSIFICATION_OUTPUTS.md")
PDF_BASE = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf")

# Broad test across 7 contract types — small files to avoid CPU OOM
TEST_PDFS = [
    # 1. Franchise Agreement
    ("Franchise", PDF_BASE / "Part_I" / "Franchise" / "PfHospitalityGroupInc_20150923_10-12G_EX-10.1_9266710_EX-10.1_Franchise Agreement3.pdf"),
    # 2. Affiliate Agreement
    ("Affiliate", PDF_BASE / "Part_I" / "Affiliate_Agreements" / "LinkPlusCorp_20050802_8-K_EX-10_3240252_EX-10_Affiliate Agreement.pdf"),
    # 3. Co-Branding Agreement
    ("Co-Branding", PDF_BASE / "Part_I" / "Co_Branding" / "PcquoteComInc_19990721_S-1A_EX-10.11_6377149_EX-10.11_Co-Branding Agreement3.pdf"),
    # 4. Marketing Agreement
    ("Marketing", PDF_BASE / "Part_I" / "Marketing" / "EmmisCommunicationsCorp_20191125_8-K_EX-10.6_11906433_EX-10.6_Marketing Agreement.pdf"),
    # 5. Development Agreement
    ("Development", PDF_BASE / "Part_I" / "Development" / "EmeraldHealthBioceuticalsInc_20200218_1-A_EX1A-6 MAT CTRCT_11987205_EX1A-6 MAT CTRCT_Development Agreement.pdf"),
    # 6. Content License Agreement
    ("License", PDF_BASE / "Part_I" / "License_Agreements" / "DataCallTechnologies_20060918_SB-2A_EX-10.9_944510_EX-10.9_Content License Agreement.pdf"),
    # 7. Service Agreement
    ("Service", PDF_BASE / "Part_I" / "Service" / "IntegrityFunds_20200121_485BPOS_EX-99.E UNDR CONTR_11948727_EX-99.E UNDR CONTR_Service Agreement.pdf"),
    # 8. Supply Agreement
    ("Supply", PDF_BASE / "Part_I" / "Supply" / "LohaCompanyltd_20191209_F-1_EX-10.16_11917878_EX-10.16_Supply Agreement.pdf"),
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
        f.write("# ClauseOps Phase 2: Classification Output Report\n\n")
        f.write(f"Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("This document demonstrates the full pipeline: **Raw PDF -> ML Segmentation -> Pre-filters -> Contracts-BERT Classification**.\n\n")
        f.write("Pre-classification filters applied:\n")
        f.write("- **Preamble/Recitals filter**: Segments with PREAMBLE/RECITALS/WHEREAS headings/body -> tagged `PREAMBLE`, skipped\n")
        f.write("- **Signature block filter**: Segments with <20 tokens and no legal verbs -> tagged `SIGNATURE_BLOCK`, skipped\n\n")
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
