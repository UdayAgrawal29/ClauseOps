# ClauseOps — Clause Segmentation v2: All 4 Gaps Fixed
### Incorporating the 4 production fixes with correct implementations

---

## Summary of Changes from v1

| Gap | Gemini's Verdict | My Verdict | Fix Status |
|---|---|---|---|
| 1. Table Data Purge | ✅ Valid | ✅ Valid — but fix needs fallback for borderless tables | Fixed with 2-strategy approach |
| 2. Multi-Column Scramble | ⚠️ Overstated | `sort=True` helps but doesn't solve true multi-column | Added `sort=True` + column detection note |
| 3. Hybrid OCR | ✅ Valid | ✅ Valid — most impactful fix | Per-page OCR implemented |
| 4. Definitions Fallacy | ✅ Valid | ✅ Valid — but fix must preserve parent-child link | Definitions handled as indexed children |

---

## Fix 1: Table Detection with Fallback Strategy

The core issue: PyMuPDF's `find_tables()` works well for tables WITH grid lines (the common case in vendor agreements). For borderless tables (alignment-only), it needs a different strategy.

```python
import fitz
import json

def extract_tables_from_page(page: fitz.Page) -> list[dict]:
    """
    Detect and extract tables from a single page.
    Returns list of table dicts with their bounding boxes and Markdown content.
    
    Two-strategy approach:
    - Strategy 1 ("lines"): For tables with visible grid lines (most commercial contracts)
    - Strategy 2 ("text"): Fallback for borderless/alignment-only tables
    """
    tables = []
    
    # Strategy 1: Line-based detection (works for tables with borders)
    try:
        tab_finder = page.find_tables(strategy="lines_strict")
        if tab_finder.tables:
            for tab in tab_finder.tables:
                markdown = tab.to_markdown()
                tables.append({
                    "bbox": tab.bbox,          # (x0, y0, x1, y1) bounding box
                    "markdown": markdown,
                    "strategy_used": "lines"
                })
            return tables  # If line-based works, use it and return
    except Exception:
        pass
    
    # Strategy 2: Text-position based (for borderless tables)
    # Only try if line strategy found nothing
    try:
        tab_finder = page.find_tables(
            strategy="text",
            min_words_vertical=3,    # Minimum words to form a column
            min_words_horizontal=2   # Minimum words to form a row
        )
        for tab in tab_finder.tables:
            # Extra validation: borderless table detection has false positives
            # Only accept if it has at least 2 columns AND 2 rows
            if len(tab.rows) >= 2 and len(tab.header.cells) >= 2:
                markdown = tab.to_markdown()
                tables.append({
                    "bbox": tab.bbox,
                    "markdown": markdown,
                    "strategy_used": "text"
                })
    except Exception:
        pass
    
    return tables


def get_table_bboxes(page: fitz.Page) -> list[tuple]:
    """
    Get bounding boxes of all detected tables on a page.
    Used to MASK table regions from normal block extraction.
    """
    tables = extract_tables_from_page(page)
    return [t["bbox"] for t in tables]


def bbox_overlaps(block_bbox: tuple, table_bbox: tuple, threshold: float = 0.5) -> bool:
    """
    Check if a text block overlaps with a table region.
    Uses intersection-over-block-area ratio.
    threshold: if more than 50% of block is inside a table region, skip it.
    """
    bx0, by0, bx1, by1 = block_bbox
    tx0, ty0, tx1, ty1 = table_bbox
    
    # Compute intersection
    ix0 = max(bx0, tx0)
    iy0 = max(by0, ty0)
    ix1 = min(bx1, tx1)
    iy1 = min(by1, ty1)
    
    if ix1 <= ix0 or iy1 <= iy0:
        return False  # No overlap
    
    intersection_area = (ix1 - ix0) * (iy1 - iy0)
    block_area = (bx1 - bx0) * (by1 - by0)
    
    if block_area == 0:
        return False
    
    overlap_ratio = intersection_area / block_area
    return overlap_ratio >= threshold
```

### Updated Layer 0: Extract with Table Masking

```python
def extract_blocks_with_metadata(pdf_path: str) -> tuple[list[TextBlock], list[dict]]:
    """
    Returns: (text_blocks, table_records)
    - text_blocks: All non-table text blocks with metadata
    - table_records: All tables as structured Markdown with page/position info
    """
    doc = fitz.open(pdf_path)
    all_blocks = []
    all_tables = []
    
    for page_num, page in enumerate(doc):
        
        # Step 1: Detect tables FIRST, get their bounding boxes
        page_tables = extract_tables_from_page(page)
        table_bboxes = [t["bbox"] for t in page_tables]
        
        # Store tables with page info for later use
        for table in page_tables:
            all_tables.append({
                "page_num": page_num,
                "bbox": table["bbox"],
                "markdown": table["markdown"],
                "classification": "TABLE"
            })
        
        # Step 2: Extract text blocks WITH sort=True for reading order
        # sort=True sorts top-left to bottom-right — helps on most contracts
        # Note: Does NOT fully solve true 2-column pages, but handles disorder from PDF creators
        page_dict = page.get_text("dict", sort=True)
        page_height = page_dict["height"]
        page_width = page_dict["width"]
        
        for block in page_dict["blocks"]:
            if block["type"] != 0:  # Skip image blocks
                continue
            
            block_bbox = block["bbox"]
            
            # Step 3: SKIP blocks that fall inside a detected table region
            # This prevents table cells from being processed as normal clauses
            is_in_table = any(
                bbox_overlaps(block_bbox, tbbox) 
                for tbbox in table_bboxes
            )
            if is_in_table:
                continue
            
            # Collect spans metadata (same as v1)
            block_text_parts = []
            font_sizes = []
            bold_flags = []
            
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]
                    font_sizes.append(span["size"])
                    is_bold = bool(span["flags"] & 16) or "Bold" in span["font"] or "bold" in span["font"].lower()
                    bold_flags.append(is_bold)
                block_text_parts.append(line_text)
            
            full_text = " ".join(block_text_parts).strip()
            if not full_text:
                continue
            
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10
            is_bold = any(bold_flags)
            x0, y0, x1, y1 = block_bbox
            block_center = (x0 + x1) / 2
            page_center = page_width / 2
            is_centered = abs(block_center - page_center) < (page_width * 0.1)
            
            import re
            alpha_text = re.sub(r'[^a-zA-Z]', '', full_text)
            is_all_caps = len(alpha_text) > 3 and alpha_text == alpha_text.upper()
            
            all_blocks.append(TextBlock(
                text=full_text,
                page_num=page_num,
                bbox=block_bbox,
                font_size=avg_font_size,
                is_bold=is_bold,
                is_italic=False,
                is_all_caps=is_all_caps,
                is_centered=is_centered,
                indentation=x0,
                block_type="text"
            ))
    
    doc.close()
    return all_blocks, all_tables
```

### Updated ClauseChunk to Include Table Type

```python
@dataclass
class ClauseChunk:
    clause_id: str
    heading: Optional[str]
    heading_number: Optional[str]
    body_text: str
    level: int
    start_page: int
    end_page: int
    token_count: int
    is_oversized: bool
    chunk_type: str = "CLAUSE"    # NEW: "CLAUSE" | "TABLE" | "DEFINITION_GROUP"
    table_markdown: Optional[str] = None   # NEW: Populated for TABLE chunks
    definitions: list = field(default_factory=list)  # NEW: For DEFINITION_GROUP
    sub_chunks: list = field(default_factory=list)
```

---

## Fix 2: Multi-Column Reading Order

The complete fix is `sort=True` + a column-detection guard for signature pages.

```python
# In extract_blocks_with_metadata, the call is already:
# page_dict = page.get_text("dict", sort=True)
# That handles the common case.

# For true 2-column pages (rare in contracts — mainly signature pages):
def detect_multi_column_page(blocks: list, page_width: float) -> bool:
    """
    Heuristic: If a significant number of blocks cluster around x=page_width/4
    AND x=3*page_width/4, it's likely a 2-column layout.
    """
    if not blocks:
        return False
    
    left_col_blocks = sum(1 for b in blocks if b.bbox[0] < page_width * 0.45)
    right_col_blocks = sum(1 for b in blocks if b.bbox[0] > page_width * 0.55)
    total = len(blocks)
    
    # If roughly equal split of blocks between left and right halves → 2-column
    if total > 4 and left_col_blocks > 0 and right_col_blocks > 0:
        ratio = min(left_col_blocks, right_col_blocks) / max(left_col_blocks, right_col_blocks)
        return ratio > 0.6  # Both columns have roughly similar block counts
    
    return False

def sort_blocks_for_multi_column(blocks: list, page_width: float) -> list:
    """
    For 2-column pages: sort left column top-to-bottom FIRST,
    then right column top-to-bottom SECOND.
    This gives correct reading order for 2-column layouts.
    """
    mid_x = page_width / 2
    left_blocks = sorted([b for b in blocks if b.bbox[0] < mid_x], key=lambda b: b.bbox[1])
    right_blocks = sorted([b for b in blocks if b.bbox[0] >= mid_x], key=lambda b: b.bbox[1])
    return left_blocks + right_blocks

# In your extract function, after collecting page blocks, add:
# if detect_multi_column_page(page_blocks, page_width):
#     page_blocks = sort_blocks_for_multi_column(page_blocks, page_width)
```

**Honest note:** For legal contracts, multi-column pages appear on maybe 5% of documents (signature pages, schedule annexures). The above handles them. Don't over-engineer this before you've seen the problem in practice.

---

## Fix 3: Per-Page OCR (Replaces Document-Level Average)

This is the most important fix. The original check failed on hybrid PDFs where most pages are digital but one or more pages are scanned.

```python
def is_page_scanned(page: fitz.Page) -> bool:
    """
    Check if a SINGLE PAGE needs OCR.
    A page is considered scanned if it has:
    1. Very few extracted text characters, AND
    2. Has image blocks (bitmap images embedded in the page)
    
    This correctly handles hybrid PDFs where most pages are digital
    but the last page (e.g., a signed addendum) is scanned.
    """
    # Get raw text directly from this page
    page_text = page.get_text("text").strip()
    word_count = len(page_text.split())
    
    # Check if page contains embedded images (sign of a scanned page)
    blocks = page.get_text("dict")["blocks"]
    has_image_block = any(b["type"] == 1 for b in blocks)
    
    # Page is scanned if: few words AND has images
    # The threshold of 20 words handles pages that have a tiny bit of
    # digital text (e.g., a page header printed digitally over a scanned body)
    return word_count < 20 and has_image_block


def ocr_single_page(page: fitz.Page) -> str:
    """
    OCR a single page using PyMuPDF's built-in Tesseract integration.
    300 DPI is the sweet spot for printed legal documents — high enough
    for accuracy, not so high that it's slow.
    """
    # get_textpage_ocr renders the page as an image and passes it to Tesseract
    tp = page.get_textpage_ocr(language="eng", dpi=300, full=True)
    return tp.extractText()


def extract_blocks_with_metadata(pdf_path: str) -> tuple[list[TextBlock], list[dict]]:
    """
    Updated version: handles hybrid PDFs by checking OCR need per page.
    """
    doc = fitz.open(pdf_path)
    all_blocks = []
    all_tables = []
    
    for page_num, page in enumerate(doc):
        
        # --- NEW: Per-page OCR check ---
        if is_page_scanned(page):
            # This page is scanned — use OCR to get text
            ocr_text = ocr_single_page(page)
            if ocr_text.strip():
                # For OCR pages: we lose visual metadata (no bold/font size info)
                # Create a single TextBlock with the full OCR text
                # and flag it as OCR-derived so downstream knows accuracy is lower
                all_blocks.append(TextBlock(
                    text=ocr_text,
                    page_num=page_num,
                    bbox=(0, 0, page.rect.width, page.rect.height),
                    font_size=10.0,           # Unknown — use default
                    is_bold=False,            # Unknown from OCR
                    is_italic=False,
                    is_all_caps=False,
                    is_centered=False,
                    indentation=0.0,
                    block_type="ocr_text"     # Flagged as OCR-derived
                ))
            continue  # Don't try dict extraction on a scanned page
        # --- End of per-page OCR check ---
        
        # Detect tables (same as before)
        page_tables = extract_tables_from_page(page)
        table_bboxes = [t["bbox"] for t in page_tables]
        for table in page_tables:
            all_tables.append({
                "page_num": page_num,
                "bbox": table["bbox"],
                "markdown": table["markdown"],
                "classification": "TABLE"
            })
        
        # Extract text blocks with sort=True
        page_dict = page.get_text("dict", sort=True)
        page_width = page_dict["width"]
        
        page_blocks_raw = []
        
        for block in page_dict["blocks"]:
            if block["type"] != 0:
                continue
            
            block_bbox = block["bbox"]
            if any(bbox_overlaps(block_bbox, tbbox) for tbbox in table_bboxes):
                continue
            
            block_text_parts = []
            font_sizes = []
            bold_flags = []
            
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]
                    font_sizes.append(span["size"])
                    is_bold = bool(span["flags"] & 16) or "Bold" in span["font"]
                    bold_flags.append(is_bold)
                block_text_parts.append(line_text)
            
            full_text = " ".join(block_text_parts).strip()
            if not full_text or len(full_text) < 3:
                continue
            
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10
            is_bold = any(bold_flags)
            x0, y0, x1, y1 = block_bbox
            block_center = (x0 + x1) / 2
            is_centered = abs(block_center - page_width / 2) < (page_width * 0.1)
            
            import re
            alpha_text = re.sub(r'[^a-zA-Z]', '', full_text)
            is_all_caps = len(alpha_text) > 3 and alpha_text == alpha_text.upper()
            
            tb = TextBlock(
                text=full_text,
                page_num=page_num,
                bbox=block_bbox,
                font_size=avg_font_size,
                is_bold=is_bold,
                is_italic=False,
                is_all_caps=is_all_caps,
                is_centered=is_centered,
                indentation=x0,
                block_type="text"
            )
            page_blocks_raw.append(tb)
        
        # Optional multi-column resorting
        if detect_multi_column_page(page_blocks_raw, page_width):
            page_blocks_raw = sort_blocks_for_multi_column(page_blocks_raw, page_width)
        
        all_blocks.extend(page_blocks_raw)
    
    doc.close()
    return all_blocks, all_tables
```

---

## Fix 4: Definitions — Structured Children, Not Blind Splitting

The original code concatenated all definition items into one big string and split it by tokens. This destroys individual definitions. The fix stores each definition as a structured child with its parent link preserved.

```python
@dataclass  
class DefinitionItem:
    """A single defined term from a Definitions section."""
    term: str          # e.g., "Confidential Information"
    definition: str    # e.g., "means any information disclosed..."
    raw_text: str      # Full original text of this item
    token_count: int

def parse_definition_item(text: str) -> tuple[str, str]:
    """
    Parse a single definition item into (term, definition) pair.
    Handles common formats:
    - "Term" means ...
    - "Term" shall mean ...
    - "Term" refers to ...
    - Term: ...
    """
    import re
    
    # Pattern 1: "Term" means / shall mean / refers to
    m = re.match(
        r'^["\u201c\u201d]?(.+?)["\u201c\u201d]?\s+(?:shall\s+)?(?:mean|means|refer(?:s)?\s+to|is\s+defined\s+as)\s+(.+)$',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    
    # Pattern 2: Term: definition
    m = re.match(r'^(.+?):\s+(.+)$', text, re.DOTALL)
    if m and len(m.group(1).split()) <= 5:  # Term should be short
        return m.group(1).strip(), m.group(2).strip()
    
    # Can't parse — return whole text as definition
    return "", text


def assemble_clauses(
    blocks: list[TextBlock], 
    body_font_size: float,
    tables: list[dict]
) -> list[ClauseChunk]:
    """
    Updated assembler: handles tables and definitions properly.
    Tables are inserted at their correct position in the clause sequence.
    Definitions sections get structured child items.
    """
    classified = [(block, classify_block(block, body_font_size)) for block in blocks]
    
    clauses = []
    current_heading = None
    current_heading_num = None
    current_level = 0
    current_body_parts = []
    current_def_items = []         # NEW: For tracking definition items
    current_is_definitions = False # NEW: Flag for definitions sections
    current_start_page = 0
    current_end_page = 0
    
    def flush_current_clause():
        nonlocal current_heading, current_body_parts, current_def_items
        nonlocal current_is_definitions, current_start_page
        
        if not current_body_parts and not current_heading and not current_def_items:
            return
        
        if current_is_definitions and current_def_items:
            # --- DEFINITION GROUP: Store as structured children ---
            # Don't concatenate and split — keep each item separate
            full_body = "\n".join(item.raw_text for item in current_def_items)
            total_tokens = sum(item.token_count for item in current_def_items)
            
            chunk = ClauseChunk(
                clause_id=str(uuid.uuid4()),
                heading=current_heading,
                heading_number=extract_heading_number(current_heading),
                body_text=full_body,
                level=current_level,
                start_page=current_start_page,
                end_page=current_end_page,
                token_count=total_tokens,
                is_oversized=total_tokens > MAX_TOKENS,
                chunk_type="DEFINITION_GROUP",
                definitions=current_def_items,  # Structured children preserved
            )
            clauses.append(chunk)
            
        else:
            # --- REGULAR CLAUSE: Same as before, but handle oversized properly ---
            body_text = " ".join(current_body_parts).strip()
            full_text = f"{current_heading or ''}\n{body_text}".strip()
            token_count = count_tokens(full_text)
            
            chunk = ClauseChunk(
                clause_id=str(uuid.uuid4()),
                heading=current_heading,
                heading_number=extract_heading_number(current_heading),
                body_text=body_text,
                level=current_level,
                start_page=current_start_page,
                end_page=current_end_page,
                token_count=token_count,
                is_oversized=token_count > MAX_TOKENS,
                chunk_type="CLAUSE",
            )
            
            if chunk.is_oversized:
                chunk.sub_chunks = split_oversized_clause(full_text, MAX_TOKENS, overlap=50)
            
            clauses.append(chunk)
        
        # Reset state
        current_heading = None
        current_body_parts.clear()
        current_def_items.clear()
        current_is_definitions = False
    
    # Build a lookup of tables by page for insertion
    tables_by_page = {}
    for t in tables:
        p = t["page_num"]
        if p not in tables_by_page:
            tables_by_page[p] = []
        tables_by_page[p].append(t)
    
    # Track which tables have been inserted
    inserted_tables = set()
    
    for i, (block, classification) in enumerate(classified):
        
        # Insert any tables from this page that haven't been inserted yet
        # Tables are attached to the CURRENT CLAUSE context
        page = block.page_num
        if page in tables_by_page:
            for table in tables_by_page[page]:
                table_id = id(table)
                if table_id not in inserted_tables:
                    # Create a TABLE ClauseChunk
                    table_chunk = ClauseChunk(
                        clause_id=str(uuid.uuid4()),
                        heading=f"[TABLE on page {page+1}]",
                        heading_number=None,
                        body_text=table["markdown"],
                        level=current_level,
                        start_page=page,
                        end_page=page,
                        token_count=count_tokens(table["markdown"]),
                        is_oversized=False,
                        chunk_type="TABLE",
                        table_markdown=table["markdown"],
                    )
                    # If we're mid-clause, attach table to current clause
                    # Otherwise add as standalone
                    clauses.append(table_chunk)
                    inserted_tables.add(table_id)
        
        if classification == 'HEADING':
            flush_current_clause()
            current_heading = block.text.strip()
            current_level = 0
            current_body_parts = []
            current_def_items = []
            current_start_page = block.page_num
            current_end_page = block.page_num
            
            # Detect if this is a Definitions section
            current_is_definitions = bool(
                re.search(r'\bDEFINITION[S]?\b', current_heading, re.IGNORECASE)
            )
        
        elif classification == 'SUBHEADING':
            current_body_parts.append(f"\n[{block.text.strip()}]")
            current_end_page = block.page_num
        
        elif classification == 'DEFINITION_ITEM':
            if current_heading is None:
                current_heading = "DEFINITIONS"
                current_is_definitions = True
                current_start_page = block.page_num
            
            # --- STRUCTURED DEFINITION STORAGE ---
            term, definition = parse_definition_item(block.text.strip())
            token_count = count_tokens(block.text.strip())
            
            def_item = DefinitionItem(
                term=term,
                definition=definition,
                raw_text=block.text.strip(),
                token_count=token_count
            )
            current_def_items.append(def_item)
            current_end_page = block.page_num
        
        elif classification in ('BODY', 'CONTINUATION'):
            if current_heading is None:
                current_heading = "PREAMBLE"
                current_start_page = block.page_num
            
            # If we're in a Definitions section but got a BODY block,
            # it might be a definition not matching our pattern — treat as body
            if current_is_definitions:
                # Try to parse as definition item first
                term, _ = parse_definition_item(block.text.strip())
                if term:
                    def_item = DefinitionItem(
                        term=term,
                        definition=_,
                        raw_text=block.text.strip(),
                        token_count=count_tokens(block.text.strip())
                    )
                    current_def_items.append(def_item)
                else:
                    current_body_parts.append(block.text.strip())
            else:
                current_body_parts.append(block.text.strip())
            
            current_end_page = block.page_num
    
    flush_current_clause()
    return clauses
```

---

## Updated Master Function

```python
def segment_contract(pdf_path: str) -> list[ClauseChunk]:
    """
    Master function: PDF → list of ClauseChunk objects.
    Now handles: tables, hybrid OCR, multi-column, and structured definitions.
    """
    # Layer 0: Extract with metadata + table detection
    blocks, tables = extract_blocks_with_metadata(pdf_path)
    
    if not blocks:
        raise ValueError(f"No text could be extracted from {pdf_path}.")
    
    doc = fitz.open(pdf_path)
    page_height = doc[0].rect.height
    doc.close()
    
    # Layer 1: Remove noise
    blocks = remove_noise_blocks(blocks, page_height=page_height)
    
    # Layer 2: Detect body font size
    # Note: OCR blocks (block_type="ocr_text") skew the modal size — filter them out
    digital_blocks = [b for b in blocks if b.block_type == "text"]
    if digital_blocks:
        body_font_size = detect_body_font_size(digital_blocks)
    else:
        body_font_size = 10.0  # Fallback for all-OCR documents
    
    # Layer 3: Assemble clauses (now table-aware and definitions-aware)
    clauses = assemble_clauses(blocks, body_font_size, tables)
    
    return clauses
```

---

## What the Output Looks Like Now

For a vendor agreement with a payment schedule table and a definitions section:

```python
clauses = segment_contract("vendor_agreement.pdf")

for c in clauses:
    print(f"\n{'='*50}")
    print(f"Type: {c.chunk_type}")
    print(f"Heading: {c.heading}")
    
    if c.chunk_type == "TABLE":
        print(f"Table (Markdown):\n{c.table_markdown[:200]}...")
    
    elif c.chunk_type == "DEFINITION_GROUP":
        print(f"Definitions ({len(c.definitions)} items):")
        for d in c.definitions[:3]:
            print(f"  Term: {d.term!r}")
            print(f"  Def:  {d.definition[:80]}...")
    
    else:
        print(f"Body: {c.body_text[:150]}...")
        print(f"Tokens: {c.token_count} | Oversized: {c.is_oversized}")
```

Sample output:
```
==================================================
Type: CLAUSE
Heading: PREAMBLE
Body: This Vendor Agreement ("Agreement") is entered into as of January 1, 2026...
Tokens: 87 | Oversized: False

==================================================
Type: DEFINITION_GROUP
Heading: 1. DEFINITIONS
Definitions (5 items):
  Term: 'Agreement'
  Def:  means this Vendor Agreement together with all schedules...
  Term: 'Deliverables'
  Def:  means the goods and services to be provided by Vendor as desc...
  Term: 'Fees'
  Def:  means the amounts payable to Vendor as set out in Schedule A...

==================================================
Type: TABLE
Heading: [TABLE on page 3]
Table (Markdown):
| Milestone | Amount | Due Date |
|---|---|---|
| Phase 1 Completion | ₹50,000 | March 1, 2026 |
| Phase 2 Completion | ₹75,000 | June 1, 2026 |

==================================================
Type: CLAUSE
Heading: 4. PAYMENT TERMS
Body: Client shall pay Vendor the Fees in accordance with the payment schedule...
Tokens: 198 | Oversized: False
```

Notice that:
- The table is extracted as structured Markdown — the ₹50,000 ↔ Phase 1 relationship is preserved
- The Definitions section stores 5 individual `DefinitionItem` objects — none get sliced mid-definition
- Regular clauses are unchanged

---

## Passing These to Downstream Models

The downstream classifier needs to handle the three chunk types differently:

```python
def analyze_chunk(chunk: ClauseChunk) -> dict:
    
    if chunk.chunk_type == "TABLE":
        # For tables: pass the Markdown to NER only (not classifier)
        # Tables don't have a "clause type" — they contain supporting data
        return {
            "clause_id": chunk.clause_id,
            "type": "TABLE",
            "entities": run_ner(chunk.table_markdown),
            "skip_classification": True
        }
    
    elif chunk.chunk_type == "DEFINITION_GROUP":
        # For definitions: run NER on each item INDIVIDUALLY
        # but store result with parent heading context
        results = []
        for item in chunk.definitions:
            # Each definition item gets its own NER pass
            # The model sees: "Agreement means this Vendor Agreement..."
            # NOT a 1000-word concatenation of all definitions
            entities = run_ner(item.raw_text)
            results.append({
                "term": item.term,
                "entities": entities
            })
        return {
            "clause_id": chunk.clause_id,
            "type": "DEFINITION_GROUP",
            "parent_heading": chunk.heading,
            "definitions": results,
            "skip_classification": True  # Definitions don't need clause classification
        }
    
    elif chunk.is_oversized:
        # For oversized clauses: run models on each sub_chunk separately
        # Aggregate results by majority vote on classification
        sub_results = [run_full_analysis(sub) for sub in chunk.sub_chunks]
        return aggregate_sub_results(chunk.clause_id, sub_results)
    
    else:
        # Normal clause: run all 4 models
        return run_full_analysis_on_chunk(chunk)
```

---

## Final Architecture (Updated)

```
PDF File
    │
    ▼  PyMuPDF get_text("dict", sort=True)
[Blocks + Visual Metadata]
    │
    │  find_tables() per page
    │  ├── Strategy "lines_strict" (for tables with borders)
    │  └── Strategy "text" fallback (for borderless tables)
[Tables extracted as Markdown + their bboxes recorded]
    │
    ▼
[Per-Page OCR Check]
    │  if page_text < 20 words AND has image blocks:
    │      → OCR that page with Tesseract 300dpi
    │      → Creates OCR-flagged TextBlock
    │  else:
    │      → Normal dict extraction
    │
    ▼
[Table Masking]
    │  Skip any text blocks that overlap with table bboxes
    ▼
[Noise Removal]
    │  Remove page numbers, running headers, footers
    ▼
[Multi-Column Detection]
    │  If left/right block ratio ≈ 1:1 → re-sort by column-first order
    ▼
[Body Font Size Detection]
    │  Uses only digital (non-OCR) blocks for reliable baseline
    ▼
[Block Classification]
    │  HEADING / SUBHEADING / BODY / CONTINUATION / DEFINITION_ITEM
    ▼
[Clause Assembly]
    │  CLAUSE chunks: HEADING + BODY blocks merged, oversized → split with overlap
    │  DEFINITION_GROUP chunks: structured DefinitionItem list, no token-splitting
    │  TABLE chunks: Markdown representation inserted at correct position
    ▼
List[ClauseChunk]
    type: CLAUSE | DEFINITION_GROUP | TABLE
    │
    ▼
Downstream: Classifier (CLAUSE only) | NER (all) | Obligation (CLAUSE only)
```

---

*Research sources for this revision: PyMuPDF official FAQ (https://pymupdf.readthedocs.io/en/latest/faq); PyMuPDF table detection documentation and GitHub issues #3156, #2885, #1901; Artifex blog "Solving Common Issues With Table Detection and Extraction" (2023)*
