"""
Quick test: Run Docling-based segmentation on one PDF and print results.
"""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force Docling backend
from clauseops.segmentation.docling_pipeline import segment_contract_docling

test_dir = r"c:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\TEST_PDFS"

# Pick the first PDF found
pdf_files = [f for f in os.listdir(test_dir) if f.lower().endswith(".pdf")]
if not pdf_files:
    print("No PDF files found in TEST_PDFS/")
    sys.exit(1)

for pdf_file in pdf_files:
    path = os.path.join(test_dir, pdf_file)
    short_name = pdf_file[:60] + "..." if len(pdf_file) > 60 else pdf_file

    print(f"\n{'='*80}")
    print(f"  {short_name}")
    print(f"{'='*80}")

    t0 = time.time()
    try:
        clauses = segment_contract_docling(path)
    except Exception as e:
        print(f"  ERROR: {e}")
        continue
    elapsed = time.time() - t0

    tokens = [c.token_count for c in clauses]
    print(f"  Time: {elapsed:.1f}s")
    print(f"  Chunks: {len(clauses)} total")
    print(f"    CLAUSE: {sum(1 for c in clauses if c.chunk_type == 'CLAUSE')}")
    print(f"    TABLE:  {sum(1 for c in clauses if c.chunk_type == 'TABLE')}")
    print(f"    DEF:    {sum(1 for c in clauses if c.chunk_type == 'DEFINITION_GROUP')}")
    print(f"  Tokens: avg={sum(tokens)//len(tokens) if tokens else 0}, "
          f"max={max(tokens) if tokens else 0}")
    print(f"\n  Headings:")
    for i, c in enumerate(clauses):
        h = c.heading or "(no heading)"
        h_display = h[:70] + "..." if len(h) > 70 else h
        body_preview = (c.body_text or "")[:80].replace("\n", " ")
        print(f"    {i+1:>3}. [{c.chunk_type:<18}] {h_display}")
        if body_preview:
            print(f"         Body: {body_preview}...")
