"""
Segmentation Quality Audit Script

Runs the pipeline on TEST_PDFS and produces a detailed quality report
identifying specific issues:
  1. Heading-only segments (no body text)
  2. Oversized segments that may have missed sub-section splits
  3. Noise segments (TOC, exhibit labels, etc.)
  4. Missing section numbers between detected segments
  5. Segments too small to be useful for classification
"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from clauseops.segmentation import segment_contract

TEST_DIR = Path(r"c:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\TEST_PDFS")

# Grab all PDFs (skip subfolders for now)
pdf_files = list(TEST_DIR.glob("*.pdf")) + list(TEST_DIR.glob("*.PDF"))
print(f"Found {len(pdf_files)} test PDFs\n")

all_issues = {}

for pdf in pdf_files[:3]:  # test first 3 to keep it fast
    name = pdf.name[:60]
    print(f"\n{'='*80}")
    print(f"  Processing: {name}")
    print(f"{'='*80}")
    
    try:
        chunks = segment_contract(str(pdf))
    except Exception as e:
        print(f"  ERROR: {e}")
        continue
    
    issues = []
    
    # --- Issue 1: Heading-only segments (empty body) ---
    heading_only = []
    for i, c in enumerate(chunks):
        body = (c.body_text or "").strip()
        if len(body) < 10 and c.chunk_type == "CLAUSE":
            heading_only.append({
                "index": i+1,
                "heading": c.heading,
                "body": body,
                "tokens": c.token_count,
            })
    if heading_only:
        issues.append({
            "type": "HEADING_ONLY_SEGMENTS",
            "count": len(heading_only),
            "detail": heading_only,
        })
    
    # --- Issue 2: Very small segments (<20 tokens, likely noise) ---
    tiny = []
    for i, c in enumerate(chunks):
        if c.token_count < 20 and c.chunk_type == "CLAUSE":
            tiny.append({
                "index": i+1,
                "heading": c.heading,
                "body": (c.body_text or "")[:100],
                "tokens": c.token_count,
            })
    if tiny:
        issues.append({
            "type": "TINY_SEGMENTS",
            "count": len(tiny),
            "detail": tiny,
        })
    
    # --- Issue 3: Very large segments (>1500 tokens = likely missed splits) ---
    huge = []
    for i, c in enumerate(chunks):
        if c.token_count > 1500:
            huge.append({
                "index": i+1,
                "heading": c.heading,
                "tokens": c.token_count,
                "sub_chunks": len(c.sub_chunks) if c.sub_chunks else 0,
            })
    if huge:
        issues.append({
            "type": "VERY_LARGE_SEGMENTS",
            "count": len(huge),
            "detail": huge,
        })
    
    # --- Issue 4: Missing section numbers (gaps in numbering) ---
    section_nums = []
    for c in chunks:
        if c.heading_number:
            try:
                # Parse top-level number
                top = c.heading_number.split(".")[0]
                if top.isdigit():
                    section_nums.append(int(top))
            except:
                pass
    if section_nums:
        expected = set(range(min(section_nums), max(section_nums)+1))
        found = set(section_nums)
        missing = sorted(expected - found)
        if missing:
            issues.append({
                "type": "MISSING_SECTION_NUMBERS",
                "missing": missing,
                "found": sorted(found),
            })
    
    # --- Issue 5: Segments that are noise (TOC, exhibit labels) ---
    noise_keywords = ["table of contents", "exhibit", "- i -", "- ii -", "page"]
    noise_segs = []
    for i, c in enumerate(chunks):
        h = (c.heading or "").lower()
        b = (c.body_text or "").lower()
        combined = h + " " + b
        if any(kw in combined for kw in noise_keywords) and c.token_count < 30:
            noise_segs.append({
                "index": i+1,
                "heading": c.heading,
                "body": (c.body_text or "")[:80],
                "tokens": c.token_count,
            })
    if noise_segs:
        issues.append({
            "type": "NOISE_SEGMENTS",
            "count": len(noise_segs),
            "detail": noise_segs,
        })
    
    # --- Summary stats ---
    token_list = [c.token_count for c in chunks]
    stats = {
        "total_segments": len(chunks),
        "clause_count": sum(1 for c in chunks if c.chunk_type == "CLAUSE"),
        "table_count": sum(1 for c in chunks if c.chunk_type == "TABLE"),
        "def_count": sum(1 for c in chunks if c.chunk_type == "DEFINITION_GROUP"),
        "avg_tokens": sum(token_list) // len(token_list) if token_list else 0,
        "max_tokens": max(token_list) if token_list else 0,
        "min_tokens": min(token_list) if token_list else 0,
        "oversized_count": sum(1 for c in chunks if c.is_oversized),
        "heading_only_count": len(heading_only),
        "tiny_count": len(tiny),
    }
    
    print(f"\n  Stats: {stats['total_segments']} segments, avg {stats['avg_tokens']} tok")
    print(f"  Oversized: {stats['oversized_count']}, Heading-only: {stats['heading_only_count']}, Tiny: {stats['tiny_count']}")
    
    if issues:
        print(f"\n  ISSUES FOUND: {len(issues)} categories")
        for issue in issues:
            print(f"    - {issue['type']}: {issue.get('count', 'N/A')} items")
            if issue['type'] == 'HEADING_ONLY_SEGMENTS':
                for item in issue['detail'][:5]:
                    print(f"        Seg {item['index']}: \"{item['heading']}\" ({item['tokens']} tokens, body=\"{item['body']}\")")
            elif issue['type'] == 'TINY_SEGMENTS':
                for item in issue['detail'][:5]:
                    print(f"        Seg {item['index']}: \"{item['heading']}\" ({item['tokens']} tokens)")
            elif issue['type'] == 'VERY_LARGE_SEGMENTS':
                for item in issue['detail'][:5]:
                    print(f"        Seg {item['index']}: \"{item['heading']}\" ({item['tokens']} tokens, {item['sub_chunks']} sub-chunks)")
            elif issue['type'] == 'NOISE_SEGMENTS':
                for item in issue['detail'][:5]:
                    print(f"        Seg {item['index']}: \"{item['heading']}\" body=\"{item['body']}\"")
            elif issue['type'] == 'MISSING_SECTION_NUMBERS':
                print(f"        Missing: {issue['missing']}")
                print(f"        Found:   {issue['found']}")
    else:
        print("  NO ISSUES FOUND")
    
    all_issues[pdf.name] = {"stats": stats, "issues": issues}
    
    # Also print all segments for manual review
    print(f"\n  ALL SEGMENTS:")
    for i, c in enumerate(chunks):
        h = (c.heading or "(no heading)")[:50]
        b = (c.body_text or "")[:80].replace("\n", " ")
        flag = ""
        if c.is_oversized:
            flag = " [OVERSIZED]"
        if c.token_count < 10:
            flag += " [TINY]"
        if len((c.body_text or "").strip()) < 5:
            flag += " [NO BODY]"
        print(f"    {i+1:>3}. [{c.chunk_type:<18}] {h:<50} {c.token_count:>5} tok{flag}")
        if b and c.token_count < 30:
            print(f"         body: \"{b}\"")

print("\n\nDONE")
