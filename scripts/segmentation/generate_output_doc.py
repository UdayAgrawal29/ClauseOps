"""
Generate a detailed output document showing full clause content for 3 selected PDFs.
"""
import sys, os, json, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from clauseops.segmentation import segment_contract

CUAD_ROOT = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf")

# Pick diverse, smaller PDFs for full output, including new ones
TEST_PDFS = [
    ("Franchise Agreement", CUAD_ROOT / "Part_I" / "Franchise" / "PfHospitalityGroupInc_20150923_10-12G_EX-10.1_9266710_EX-10.1_Franchise Agreement1.pdf"),
    ("Non-Compete Agreement", CUAD_ROOT / "Part_I" / "Non_Compete_Non_Solicit" / "Quaker Chemical Corporation - NON COMPETITION AND NON SOLICITATION AGREEMENT.PDF"),
    ("Joint Venture Agreement", CUAD_ROOT / "Part_I" / "Joint Venture" / "ACCELERATEDTECHNOLOGIESHOLDINGCORP_04_24_2003-EX-10.13-JOINT VENTURE AGREEMENT.PDF"),
    ("Endorsement Agreement", CUAD_ROOT / "Part_I" / "Endorsement" / "EcoScienceSolutionsInc_20171117_8-K_EX-10.1_10956472_EX-10.1_Endorsement Agreement.pdf"),
    ("License Agreement", CUAD_ROOT / "Part_I" / "License_Agreements" / "GopageCorp_20140221_10-K_EX-10.1_8432966_EX-10.1_Content License Agreement.pdf"),
]

output_lines = []
output_lines.append("# ClauseOps — Segmentation Output Samples\n")
output_lines.append("> **Generated:** " + time.strftime("%Y-%m-%d %H:%M") + "  \n")
output_lines.append("> **Engine:** Docling ML (DocLayNet-trained)  \n")
output_lines.append("> **Source:** CUAD v1 — Contract Understanding Atticus Dataset\n")
output_lines.append("\n---\n")

for label, pdf_path in TEST_PDFS:
    if not pdf_path.exists():
        output_lines.append(f"\n## ❌ {label}\n\nFile not found: `{pdf_path.name}`\n")
        continue
    
    print(f"Processing: {label}...")
    t0 = time.time()
    chunks = segment_contract(str(pdf_path))
    elapsed = time.time() - t0
    
    clause_count = sum(1 for c in chunks if c.chunk_type == "CLAUSE")
    table_count = sum(1 for c in chunks if c.chunk_type == "TABLE")
    def_count = sum(1 for c in chunks if c.chunk_type == "DEFINITION_GROUP")
    tokens = [c.token_count for c in chunks]
    
    output_lines.append(f"\n## 📄 {label}\n")
    output_lines.append(f"**File:** `{pdf_path.name}`  \n")
    output_lines.append(f"**Processing Time:** {elapsed:.1f}s  \n\n")
    
    output_lines.append("| Metric | Value |\n|---|---|\n")
    output_lines.append(f"| Total Segments | {len(chunks)} |\n")
    output_lines.append(f"| Clauses | {clause_count} |\n")
    output_lines.append(f"| Tables | {table_count} |\n")
    output_lines.append(f"| Definition Groups | {def_count} |\n")
    if tokens:
        output_lines.append(f"| Avg Tokens/Segment | {sum(tokens)//len(tokens)} |\n")
        output_lines.append(f"| Max Tokens | {max(tokens)} |\n")
        output_lines.append(f"| Min Tokens | {min(tokens)} |\n")
        oversized = sum(1 for c in chunks if c.is_oversized)
        output_lines.append(f"| Oversized (>480 tok) | {oversized} |\n")
    
    output_lines.append("\n### Segmented Clauses\n\n")
    
    for i, c in enumerate(chunks):
        heading = c.heading or "(no heading)"
        page_label = f"p.{c.start_page+1}" if c.start_page == c.end_page else f"p.{c.start_page+1}-{c.end_page+1}"
        
        type_emoji = {"CLAUSE": "📋", "TABLE": "📊", "DEFINITION_GROUP": "📖"}.get(c.chunk_type, "📋")
        oversized_badge = " ⚠️ **OVERSIZED**" if c.is_oversized else ""
        
        output_lines.append(f"#### {type_emoji} Segment {i+1}: {heading}\n\n")
        output_lines.append(f"| Property | Value |\n|---|---|\n")
        output_lines.append(f"| Type | `{c.chunk_type}` |\n")
        output_lines.append(f"| Page(s) | {page_label} |\n")
        output_lines.append(f"| Tokens | {c.token_count}{oversized_badge} |\n")
        if c.heading_number:
            output_lines.append(f"| Section # | {c.heading_number} |\n")
        output_lines.append(f"| Level | {c.level} |\n")
        
        if c.chunk_type == "DEFINITION_GROUP" and c.definitions:
            output_lines.append(f"\n**Definitions ({len(c.definitions)} terms):**\n\n")
            for d in c.definitions[:5]:  # Show first 5
                term_text = d.term or "(unnamed)"
                def_preview = (d.definition or "")[:200]
                output_lines.append(f"- **{term_text}**: {def_preview}...\n")
            if len(c.definitions) > 5:
                output_lines.append(f"- *...and {len(c.definitions)-5} more definitions*\n")
        elif c.chunk_type == "TABLE" and c.table_markdown:
            output_lines.append(f"\n**Table Content (Markdown):**\n\n")
            output_lines.append(f"```\n{c.table_markdown[:500]}\n```\n")
        else:
            body = (c.body_text or "").strip()
            if body:
                # Truncate long bodies for readability
                if len(body) > 500:
                    body_display = body[:500] + " [...truncated...]"
                else:
                    body_display = body
                output_lines.append(f"\n**Body Text:**\n\n")
                output_lines.append(f"> {body_display}\n")
        
        if c.is_oversized and c.sub_chunks:
            output_lines.append(f"\n> ⚠️ This clause exceeds the 480-token model limit. It has been split into **{len(c.sub_chunks)} overlapping sub-chunks** for downstream ML processing.\n")
        
        output_lines.append("\n---\n\n")
    
    output_lines.append("\n")

# Write output
output_path = Path(r"c:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\SEGMENTATION_OUTPUTS.md")
with open(output_path, "w", encoding="utf-8") as f:
    f.write("".join(output_lines))

print(f"\n✅ Output saved to: {output_path}")
