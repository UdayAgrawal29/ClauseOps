"""
Test Docling segmentation on diverse CUAD contracts.
Picks one PDF from each contract category to ensure broad coverage.
Outputs results to a JSON file for analysis.
"""
import sys, os, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

CUAD_ROOT = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf")

# Pick one PDF from different contract types for diversity
TEST_PDFS = {
    "License Agreement": CUAD_ROOT / "Part_I" / "License_Agreements" / "ArtaraTherapeuticsInc_20200110_8-K_EX-10.5_11943350_EX-10.5_License Agreement.pdf",
    "Service Agreement": CUAD_ROOT / "Part_I" / "Service" / next((CUAD_ROOT / "Part_I" / "Service").iterdir()).name if (CUAD_ROOT / "Part_I" / "Service").exists() else "",
    "Joint Venture": CUAD_ROOT / "Part_I" / "Joint Venture" / next((CUAD_ROOT / "Part_I" / "Joint Venture").iterdir()).name if (CUAD_ROOT / "Part_I" / "Joint Venture").exists() else "",
    "Non-Compete": CUAD_ROOT / "Part_I" / "Non_Compete_Non_Solicit" / next((CUAD_ROOT / "Part_I" / "Non_Compete_Non_Solicit").iterdir()).name if (CUAD_ROOT / "Part_I" / "Non_Compete_Non_Solicit").exists() else "",
    "Franchise": CUAD_ROOT / "Part_I" / "Franchise" / next((CUAD_ROOT / "Part_I" / "Franchise").iterdir()).name if (CUAD_ROOT / "Part_I" / "Franchise").exists() else "",
}

# Filter to existing PDFs
test_pdfs = {k: v for k, v in TEST_PDFS.items() if v and Path(v).exists()}

print(f"\n{'='*80}")
print(f"  CUAD SEGMENTATION TEST — {len(test_pdfs)} diverse contracts")
print(f"{'='*80}\n")

from clauseops.segmentation import segment_contract

all_results = {}

for label, pdf_path in test_pdfs.items():
    print(f"\n{'='*80}")
    print(f"  [{label}] {Path(pdf_path).name[:70]}...")
    print(f"{'='*80}")
    
    try:
        t0 = time.time()
        chunks = segment_contract(str(pdf_path))
        elapsed = time.time() - t0
        
        clause_count = sum(1 for c in chunks if c.chunk_type == "CLAUSE")
        table_count = sum(1 for c in chunks if c.chunk_type == "TABLE")
        def_count = sum(1 for c in chunks if c.chunk_type == "DEFINITION_GROUP")
        tokens = [c.token_count for c in chunks]
        
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Chunks: {len(chunks)} total")
        print(f"    CLAUSE: {clause_count}")
        print(f"    TABLE:  {table_count}")
        print(f"    DEF:    {def_count}")
        if tokens:
            print(f"  Tokens: avg={sum(tokens)//len(tokens)}, max={max(tokens)}, min={min(tokens)}")
        
        # Show all headings with body preview
        print(f"\n  Headings:")
        headings_data = []
        for i, c in enumerate(chunks):
            h = c.heading or "(no heading)"
            h_display = h[:65] + "..." if len(h) > 65 else h
            body = (c.body_text or "")[:100].replace("\n", " ")
            print(f"    {i+1:>3}. [{c.chunk_type:<18}] {h_display}")
            if body:
                print(f"         Body: {body}...")
            headings_data.append({
                "index": i+1,
                "type": c.chunk_type,
                "heading": h,
                "body_preview": body[:200],
                "token_count": c.token_count,
                "start_page": c.start_page,
                "end_page": c.end_page,
            })
        
        all_results[label] = {
            "filename": Path(pdf_path).name,
            "time_seconds": round(elapsed, 1),
            "total_chunks": len(chunks),
            "clause_count": clause_count,
            "table_count": table_count,
            "def_count": def_count,
            "avg_tokens": sum(tokens) // len(tokens) if tokens else 0,
            "max_tokens": max(tokens) if tokens else 0,
            "min_tokens": min(tokens) if tokens else 0,
            "headings": headings_data,
        }
        
    except Exception as e:
        print(f"  ERROR: {e}")
        all_results[label] = {"filename": Path(pdf_path).name, "error": str(e)}

# Save results
output_path = Path(__file__).parent / "cuad_test_results.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

print(f"\n\n{'='*80}")
print(f"  SUMMARY")
print(f"{'='*80}")
for label, r in all_results.items():
    if "error" in r:
        print(f"  ❌ {label}: {r['error'][:60]}")
    else:
        print(f"  ✅ {label}: {r['total_chunks']} chunks, avg {r['avg_tokens']} tok, {r['time_seconds']}s")
print(f"\n  Results saved to: {output_path}")
