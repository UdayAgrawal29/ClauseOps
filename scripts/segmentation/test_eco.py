"""Quick test of EcoScience PDF with Docling."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clauseops.segmentation.docling_pipeline import segment_contract_docling

pdf = r"TEST_PDFS\EcoScienceSolutionsInc_20171117_8-K_EX-10.1_10956472_EX-10.1_Content License Agreement.pdf"
path = os.path.join(os.path.dirname(__file__), "..", pdf)

t0 = time.time()
chunks = segment_contract_docling(path)
elapsed = time.time() - t0

print(f"\nTime: {elapsed:.1f}s")
print(f"Chunks: {len(chunks)} total")
print(f"  CLAUSE: {sum(1 for c in chunks if c.chunk_type == 'CLAUSE')}")
print(f"  TABLE:  {sum(1 for c in chunks if c.chunk_type == 'TABLE')}")
print(f"  DEF:    {sum(1 for c in chunks if c.chunk_type == 'DEFINITION_GROUP')}")
tokens = [c.token_count for c in chunks]
if tokens:
    print(f"  Tokens: avg={sum(tokens)//len(tokens)}, max={max(tokens)}")

print(f"\nHeadings:")
for i, c in enumerate(chunks):
    h = c.heading or "(no heading)"
    h = h[:60] + "..." if len(h) > 60 else h
    body = (c.body_text or "")[:80].replace("\n", " ")
    print(f"  {i+1:>3}. [{c.chunk_type:<18}] {h}")
    if body:
        print(f"       Body: {body}...")
