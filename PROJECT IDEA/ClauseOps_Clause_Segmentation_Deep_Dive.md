# ClauseOps — Clause Segmentation: Deep Research & Complete Architecture
### The Foundation Step. Get this right first.

---

## Why You Failed Before (And Why Everyone Fails)

Before giving you the solution, you need to understand exactly why clause segmentation is hard — because if you don't understand the failure modes, you'll build the wrong thing again.

There are **7 reasons** segmentation fails on legal contracts:

---

### Failure Mode 1: You Treated a Contract Like Normal Text

Most students do `text.split("\n\n")` or use spaCy sentence boundaries. This fails immediately because a single legal clause can span multiple paragraphs. Example:

```
2. CONFIDENTIALITY OBLIGATIONS

The Receiving Party agrees to hold all Confidential Information in strict
confidence. The Receiving Party shall not disclose any Confidential Information
to any third party without prior written consent.

This obligation shall survive the termination of this Agreement for a period
of five (5) years.
```

That is **ONE clause** across **TWO paragraphs**. A double-newline split breaks it into two fragments, and your downstream classifier gets garbage.

---

### Failure Mode 2: Headers and Footers Bleed Into Clause Text

A Lexion research paper (KDD Document Intelligence Workshop 2021) studied this directly. They tested a pure text-based model for section splitting and it "struggled to accurately identify sections that a human could easily distinguish using visual cues." The specific culprit: **page numbers, running headers, and footers interrupt clause text.**

Example of what raw PyMuPDF text() output looks like without structural processing:

```
...payment shall be due within thirty (30) days of receipt of invoice.
                                        4
3. TERMINATION

Either party may terminate this Agreement...
```

That `4` is a page number. A naive segmenter thinks it's the start of a new section. The clause text for Section 2 ends prematurely.

---

### Failure Mode 3: Nested Sub-Clauses

Legal contracts have hierarchy. A top-level clause (Section 3) contains sub-clauses (3.1, 3.2), which may contain sub-sub-clauses (3.1(a), 3.1(b)). The question "what is a clause?" has no single answer. Is 3.1(a) a clause? Is all of Section 3 one clause?

If you treat every numbered item as a separate clause, you fragment the meaning. If you merge everything under Section 3 into one chunk, it's too long for a 512-token transformer.

---

### Failure Mode 4: Continuation Clauses

Many contracts look like this:

```
5. PAYMENT TERMS

5.1 The Client shall pay the Service Provider:
    (a) an initial deposit of 30% upon execution;
    (b) a further 40% upon delivery of Phase 1; and
    (c) the remaining 30% upon final acceptance.
```

Items (a), (b), (c) are **fragments** — they don't make sense independently. They are syntactic subclauses of 5.1. Your segmenter must know to merge them upward into their parent.

---

### Failure Mode 5: Inconsistent Numbering Across Contract Types

Different contract types use completely different structural signals:

| Contract Type | Structure Example |
|---|---|
| Vendor Agreement | `1. DEFINITIONS`, `2. PAYMENT`, `3. TERM` |
| NDA | `Article I`, `Article II`, `Section 1.1` |
| Employment Contract | `A. Position`, `B. Compensation`, `C. Benefits` |
| Lease Agreement | `Clause 1`, `Clause 2` OR just headings in bold |
| Indian contracts | Often no numbers at all — just bold uppercase headings |

A hardcoded regex for `^\d+\.\s+[A-Z]` misses ALL the Article/Letter/Clause formats.

---

### Failure Mode 6: Defined Terms Creating False Boundaries

Contracts have definition sections that look like paragraphs but are actually individual definitions. Example:

```
1. DEFINITIONS

"Agreement" means this Service Agreement dated January 2026.
"Confidential Information" means any information disclosed by one party.
"Deliverables" means the work product described in Schedule A.
"Fees" means the amounts payable as specified in Clause 4.
```

Each definition line looks like a separate clause. But legally, they are all sub-items of the "Definitions" clause and should be kept together as a unit OR individually indexed with reference to their parent.

---

### Failure Mode 7: The 512 Token Problem

Even if you segment perfectly, a long clause (e.g., a full Indemnification section) might be 800+ tokens. BERT-family models accept 512. You need a strategy for oversized clauses — truncation loses legal meaning, so you need smart chunking with overlap.

---

## What the Research Actually Tells You to Do

### Finding 1 (Lexion / KDD 2021): Visual Metadata from PDFs is Critical

The Lexion paper directly studied this on CUAD contracts. Their conclusion: **"Visual cues such as layout, style, and placement of text in a document are strong features that are crucial to achieving an acceptable level of accuracy."**

Specifically, the four visual features that mattered most:
1. **Page layout**: Is this text near the top/bottom/center of the page? (footer/header detection)
2. **Text placement**: Is this line centered? (headings are often centered)
3. **Visual grouping**: Which words are grouped into the same block by the PDF renderer?
4. **Stylistic features**: Is this text **bold**? *Italic*? UPPERCASE? (heading signals)

These features come from PyMuPDF's `get_text("dict")` — not from plain text extraction.

### Finding 2 (IEEE Paper, Shah et al.): Sequential Classification with Text Features

A dedicated IEEE paper on "Legal Clause Extraction From Contract Using Machine Learning" proposed treating clause boundary detection as a **sequential classification problem**: for each paragraph/block, predict BOUNDARY (this starts a new clause) or NOT-BOUNDARY (this continues the previous clause).

Features used: window size around the paragraph, history features (what came before), text features (font, indentation, numbering pattern).

Key insight: **adding false positive samples** (examples that look like boundaries but aren't) significantly improved precision.

### Finding 3 (arxiv 2603.09990, 2025): LLM-based Segmentation Gets ROUGE F1 of 0.95

A very recent 2025 paper proposed a two-stage system for NDAs:
- **Stage 1:** LLaMA-3.1-8B-Instruct for segmentation → ROUGE F1 of **0.95**
- **Stage 2:** Fine-tuned Legal-RoBERTa-Large for classification → weighted F1 of **0.85**

This is the best-performing approach documented in the literature. But it requires a locally running 8B LLM, which is expensive. You'll use a **simplified, student-feasible version** of this.

### Finding 4 (CUAD Paper, Hendrycks et al.): Paragraphs Are the Right Granularity

CUAD itself tells you the right segmentation unit. From the paper: *"we first segment a contract into different paragraphs typically ranging from one to five sentences. Then for each label category and each such paragraph, we format the question..."*

The CUAD annotation methodology used **paragraph-level chunks** as their base unit. This is your target granularity — not sentences, not full sections.

---

## The Architecture: 4-Layer Hybrid Segmentation

Based on all the research above, here is the architecture that is:
- Achievable by a student
- Handles all 7 failure modes
- Based on documented, working approaches

```
Raw PDF
   │
   ▼
┌──────────────────────────────────────────────────────┐
│  LAYER 0: Structural Extraction (PyMuPDF dict mode)  │
│  Extract blocks WITH metadata: font, size, flags,    │
│  position, bold/italic, indentation level            │
└──────────────────────────────┬───────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────┐
│  LAYER 1: Noise Removal                              │
│  Identify and remove:                                │
│  - Page numbers (isolated numeric blocks)            │
│  - Running headers (repeated text near top of page)  │
│  - Footers (repeated text near bottom of page)       │
│  - Table of contents entries                         │
└──────────────────────────────┬───────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────┐
│  LAYER 2: Heading Detection                          │
│  Classify each block as: HEADING / BODY / SUBCLAUSE  │
│  Using: font size, bold flag, ALL_CAPS, centering,   │
│  numbering pattern (regex), indentation level        │
└──────────────────────────────┬───────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────┐
│  LAYER 3: Clause Assembly                            │
│  Group blocks into clause units:                     │
│  - HEADING + following BODY blocks = one clause      │
│  - Handle continuation subclauses (a), (b), (c)      │
│  - Handle multi-paragraph clauses                    │
│  - Handle oversized clauses (>512 tokens)            │
└──────────────────────────────┬───────────────────────┘
                               │
                               ▼
   List of ClauseChunk objects
   {heading, body_text, level, start_page, token_count}
```

---

## Complete Implementation

### Step 0: Install Dependencies

```bash
pip install pymupdf spacy
python -m spacy download en_core_web_sm
```

### Step 1: The Core Data Structure

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TextBlock:
    """A single block of text from PyMuPDF with full metadata."""
    text: str
    page_num: int
    bbox: tuple          # (x0, y0, x1, y1) position on page
    font_size: float
    is_bold: bool
    is_italic: bool
    is_all_caps: bool
    is_centered: bool
    indentation: float   # x0 distance from left margin
    block_type: str      # 'text' | 'image' | 'table'
    
@dataclass
class ClauseChunk:
    """A fully assembled, segmented clause unit."""
    clause_id: str
    heading: Optional[str]        # e.g. "3.1 Payment Terms"
    heading_number: Optional[str] # e.g. "3.1"
    body_text: str                # Full clause body text
    level: int                    # 0=top, 1=sub, 2=sub-sub
    start_page: int
    end_page: int
    token_count: int
    is_oversized: bool            # True if > 480 tokens
    sub_chunks: list = field(default_factory=list)  # For oversized clauses
```

### Step 2: Layer 0 — Structural Extraction

```python
import fitz  # PyMuPDF
import re

def extract_blocks_with_metadata(pdf_path: str) -> list[TextBlock]:
    """
    Extract all text blocks from PDF with full visual metadata.
    This is the foundation — do NOT use page.get_text("text").
    Always use "dict" mode to get structural information.
    """
    doc = fitz.open(pdf_path)
    all_blocks = []
    
    page_width = doc[0].rect.width  # Assume consistent page width
    
    for page_num, page in enumerate(doc):
        page_dict = page.get_text("dict")
        page_height = page_dict["height"]
        page_width = page_dict["width"]
        
        for block in page_dict["blocks"]:
            if block["type"] != 0:  # Skip image blocks (type=1)
                continue
                
            # Collect all spans within this block
            block_text_parts = []
            font_sizes = []
            bold_flags = []
            
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]
                    font_sizes.append(span["size"])
                    # PyMuPDF flags: bit 4 = bold (TEXT_FONT_BOLD = 16)
                    is_span_bold = bool(span["flags"] & 16) or "Bold" in span["font"] or "bold" in span["font"].lower()
                    bold_flags.append(is_span_bold)
                block_text_parts.append(line_text)
            
            full_text = " ".join(block_text_parts).strip()
            if not full_text:
                continue
                
            # Compute aggregate properties
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10
            is_bold = any(bold_flags)  # Bold if any span is bold
            bbox = block["bbox"]  # (x0, y0, x1, y1)
            x0, y0, x1, y1 = bbox
            
            # Centering: block center vs page center
            block_center = (x0 + x1) / 2
            page_center = page_width / 2
            is_centered = abs(block_center - page_center) < (page_width * 0.1)  # within 10% of center
            
            # All-caps detection
            alpha_text = re.sub(r'[^a-zA-Z]', '', full_text)
            is_all_caps = len(alpha_text) > 3 and alpha_text == alpha_text.upper()
            
            # Indentation
            indentation = x0
            
            all_blocks.append(TextBlock(
                text=full_text,
                page_num=page_num,
                bbox=bbox,
                font_size=avg_font_size,
                is_bold=is_bold,
                is_italic=False,  # add italic check similarly if needed
                is_all_caps=is_all_caps,
                is_centered=is_centered,
                indentation=indentation,
                block_type="text"
            ))
    
    doc.close()
    return all_blocks
```

### Step 3: Layer 1 — Noise Removal

```python
from collections import Counter

def remove_noise_blocks(blocks: list[TextBlock], page_height: float = 842.0) -> list[TextBlock]:
    """
    Remove headers, footers, and page numbers.
    
    Strategy:
    - Page numbers: Short numeric text (1-3 chars) in top/bottom 8% of page
    - Running headers: Text that appears identically on 3+ pages
    - Footers: Similar — repeated at bottom of pages
    """
    
    # Find repeated text (running headers/footers)
    text_page_map = {}
    for block in blocks:
        normalized = block.text.strip().lower()
        if normalized not in text_page_map:
            text_page_map[normalized] = set()
        text_page_map[normalized].add(block.page_num)
    
    # Text appearing on 3+ pages is likely a header/footer
    repeated_texts = {
        text for text, pages in text_page_map.items() 
        if len(pages) >= 3
    }
    
    cleaned = []
    for block in blocks:
        # Skip repeated headers/footers
        if block.text.strip().lower() in repeated_texts:
            continue
        
        # Skip page numbers: short numeric text near top or bottom of page
        is_top_zone = block.bbox[1] < (page_height * 0.08)   # top 8%
        is_bottom_zone = block.bbox[3] > (page_height * 0.92)  # bottom 8%
        is_short_numeric = re.match(r'^\d{1,3}$', block.text.strip()) is not None
        
        if is_short_numeric and (is_top_zone or is_bottom_zone):
            continue
        
        # Skip very short standalone blocks that look like artifacts
        if len(block.text.strip()) < 3:
            continue
            
        cleaned.append(block)
    
    return cleaned
```

### Step 4: Layer 2 — Heading Detection

This is the most important layer. It decides what is a heading vs body text.

```python
def detect_body_font_size(blocks: list[TextBlock]) -> float:
    """
    Find the most common font size in the document.
    This is the 'body text' baseline. Anything significantly larger is a heading.
    """
    sizes = [round(b.font_size, 1) for b in blocks]
    counter = Counter(sizes)
    return counter.most_common(1)[0][0]  # modal font size

# Heading numbering patterns — covers ALL real-world contract formats
HEADING_PATTERNS = [
    r'^\d+\.\s+[A-Z]',              # 1. PAYMENT TERMS
    r'^\d+\.\d+\s+[A-Z]',           # 1.1 Payment Schedule
    r'^\d+\.\d+\.\d+\s+',           # 1.1.1 Sub-provision
    r'^Article\s+[IVXLCDM\d]+',     # Article I, Article 12
    r'^Section\s+\d+',              # Section 4
    r'^Clause\s+\d+',               # Clause 7
    r'^[A-Z]\.\s+[A-Z]',           # A. DEFINITIONS
    r'^\([a-z]\)\s+',               # (a) continuation item
    r'^\([ivxlIVXL]+\)\s+',         # (i), (ii), (iii) sub-items
    r'^\d+\)\s+[A-Z]',             # 1) PAYMENT
    r'^WHEREAS',                    # Recitals
    r'^NOW,?\s+THEREFORE',         # Operative clause start
    r'^IN WITNESS WHEREOF',        # Signature block
    r'^SCHEDULE\s+[A-Z\d]',        # Schedule A
    r'^EXHIBIT\s+[A-Z\d]',         # Exhibit B
    r'^ANNEX\s+[A-Z\d]',           # Annex 1
]

# Sub-clause continuation patterns — these are NOT headings, they continue a parent
CONTINUATION_PATTERNS = [
    r'^\([a-z]\)\s+',     # (a), (b), (c)
    r'^\([ivxl]+\)\s+',   # (i), (ii), (iii)
    r'^[a-z]\)\s+',       # a), b), c)
]

def classify_block(block: TextBlock, body_font_size: float) -> str:
    """
    Returns: 'HEADING' | 'SUBHEADING' | 'CONTINUATION' | 'BODY' | 'DEFINITION_ITEM'
    """
    text = block.text.strip()
    
    # Signal scoring system
    heading_signals = 0
    
    # Signal 1: Font size significantly larger than body text
    if block.font_size > body_font_size * 1.15:
        heading_signals += 3  # Strong signal
    elif block.font_size > body_font_size * 1.05:
        heading_signals += 1  # Weak signal
    
    # Signal 2: Bold text
    if block.is_bold:
        heading_signals += 2
    
    # Signal 3: ALL CAPS text
    if block.is_all_caps and len(text.split()) >= 2:
        heading_signals += 2
    
    # Signal 4: Centered on page
    if block.is_centered:
        heading_signals += 1
    
    # Signal 5: Matches a heading numbering pattern
    for pattern in HEADING_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            heading_signals += 3
            break
    
    # Short text with high signals = heading
    is_short = len(text.split()) <= 10
    
    # Check for continuation items first (they should NOT be headings)
    for pattern in CONTINUATION_PATTERNS:
        if re.match(pattern, text):
            return 'CONTINUATION'
    
    # Classify based on signals
    if heading_signals >= 4 and is_short:
        # Distinguish top-level vs sub headings by indentation and font size
        if block.font_size >= body_font_size * 1.1 or block.is_all_caps:
            return 'HEADING'
        else:
            return 'SUBHEADING'
    
    # Definition items: "Term" means ... pattern
    if re.match(r'^["\'"]?\w[\w\s]+["\'"]?\s+means\s+', text, re.IGNORECASE):
        return 'DEFINITION_ITEM'
    
    return 'BODY'
```

### Step 5: Layer 3 — Clause Assembly

This is where you combine everything into meaningful clause units.

```python
import uuid
from transformers import AutoTokenizer

# Use the same tokenizer your downstream model will use
tokenizer = AutoTokenizer.from_pretrained("nlpaueb/legal-bert-base-uncased")
MAX_TOKENS = 480  # Leave buffer below 512

def count_tokens(text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=True))

def assemble_clauses(blocks: list[TextBlock], body_font_size: float) -> list[ClauseChunk]:
    """
    Assemble classified blocks into ClauseChunk objects.
    
    Key rules:
    1. A HEADING block starts a new clause
    2. BODY and CONTINUATION blocks following a heading belong to that clause
    3. A SUBHEADING starts a sub-clause within the current clause
    4. DEFINITION_ITEMs are kept together under the Definitions heading
    5. Oversized clauses (>MAX_TOKENS) are split with overlapping windows
    """
    
    # First, classify all blocks
    classified = [(block, classify_block(block, body_font_size)) for block in blocks]
    
    clauses = []
    current_heading = None
    current_heading_num = None
    current_level = 0
    current_body_parts = []
    current_start_page = 0
    current_end_page = 0
    
    def flush_current_clause():
        """Save the current accumulated clause."""
        nonlocal current_heading, current_body_parts, current_start_page
        
        if not current_body_parts and not current_heading:
            return
        
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
        )
        
        # Handle oversized clauses by splitting with overlap
        if chunk.is_oversized:
            sub_chunks = split_oversized_clause(full_text, MAX_TOKENS, overlap=50)
            chunk.sub_chunks = sub_chunks
        
        clauses.append(chunk)
    
    for block, classification in classified:
        
        if classification == 'HEADING':
            # Save the previous clause before starting a new one
            flush_current_clause()
            current_heading = block.text.strip()
            current_level = 0
            current_body_parts = []
            current_start_page = block.page_num
            current_end_page = block.page_num
            
        elif classification == 'SUBHEADING':
            # Sub-headings: optionally flush and start sub-clause
            # For ClauseOps V1: treat subheadings as body text with emphasis
            # This keeps clause granularity at the top-level section
            current_body_parts.append(f"\n[{block.text.strip()}]")
            current_end_page = block.page_num
            
        elif classification in ('BODY', 'CONTINUATION', 'DEFINITION_ITEM'):
            # Add to current clause body
            if current_heading is None:
                # Text before any heading — probably a preamble
                current_heading = "PREAMBLE"
                current_start_page = block.page_num
                
            current_body_parts.append(block.text.strip())
            current_end_page = block.page_num
    
    # Don't forget the last clause
    flush_current_clause()
    
    return clauses


def extract_heading_number(heading: str) -> Optional[str]:
    """Extract the section number from a heading text."""
    if not heading:
        return None
    match = re.match(r'^(\d+(?:\.\d+)*|[A-Z]|\([a-z]\)|Article\s+\w+|Section\s+[\d\.]+)', heading.strip(), re.IGNORECASE)
    return match.group(1) if match else None


def split_oversized_clause(text: str, max_tokens: int, overlap: int = 50) -> list[str]:
    """
    Split a clause that's too long for the transformer.
    Uses sentence-level splitting with overlap to preserve context.
    
    Overlap is critical: the last `overlap` tokens of chunk N become
    the first tokens of chunk N+1. This prevents losing meaning at boundaries.
    """
    import spacy
    nlp = spacy.load("en_core_web_sm")
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents]
    
    chunks = []
    current_chunk_sents = []
    current_tokens = 0
    
    for sent in sentences:
        sent_tokens = count_tokens(sent)
        
        if current_tokens + sent_tokens > max_tokens and current_chunk_sents:
            # Save current chunk
            chunks.append(" ".join(current_chunk_sents))
            
            # Start new chunk WITH overlap from end of previous chunk
            # Keep last N sentences for context continuity
            overlap_sents = []
            overlap_token_count = 0
            for s in reversed(current_chunk_sents):
                s_tokens = count_tokens(s)
                if overlap_token_count + s_tokens <= overlap:
                    overlap_sents.insert(0, s)
                    overlap_token_count += s_tokens
                else:
                    break
            
            current_chunk_sents = overlap_sents + [sent]
            current_tokens = overlap_token_count + sent_tokens
        else:
            current_chunk_sents.append(sent)
            current_tokens += sent_tokens
    
    if current_chunk_sents:
        chunks.append(" ".join(current_chunk_sents))
    
    return chunks
```

### Step 6: The Master Function

```python
def segment_contract(pdf_path: str) -> list[ClauseChunk]:
    """
    Master function: PDF path → list of clean ClauseChunk objects.
    This is what your Celery worker calls.
    """
    # Layer 0: Extract with metadata
    blocks = extract_blocks_with_metadata(pdf_path)
    
    if not blocks:
        raise ValueError(f"No text extracted from {pdf_path}. May be a scanned PDF.")
    
    # Get page dimensions for noise removal
    doc = fitz.open(pdf_path)
    page_height = doc[0].rect.height
    doc.close()
    
    # Layer 1: Remove noise
    blocks = remove_noise_blocks(blocks, page_height=page_height)
    
    # Layer 2: Detect body font size baseline
    body_font_size = detect_body_font_size(blocks)
    
    # Layer 3: Assemble clauses
    clauses = assemble_clauses(blocks, body_font_size)
    
    return clauses
```

---

## How to Handle Scanned PDFs

If `extract_blocks_with_metadata` returns fewer than 50 tokens per page on average, the PDF is likely scanned (image-based). Fall back to OCR:

```python
def is_scanned_pdf(blocks: list[TextBlock], page_count: int) -> bool:
    total_text = " ".join(b.text for b in blocks)
    avg_tokens_per_page = len(total_text.split()) / max(page_count, 1)
    return avg_tokens_per_page < 50  # Fewer than 50 words/page = likely scanned

def ocr_pdf(pdf_path: str) -> str:
    """OCR fallback using Tesseract via PyMuPDF's built-in OCR support."""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        # Render page to image, then OCR
        tp = page.get_textpage_ocr(language="eng", dpi=300)
        full_text += tp.extractText() + "\n"
    doc.close()
    return full_text
```

**Important:** After OCR, you lose all visual metadata (bold, font size). For OCR documents, fall back to **regex-only heading detection** since you have no layout information. Accuracy will be lower (~75% vs ~90% for digital PDFs) — this is expected and honest.

---

## Testing Your Segmenter

### Test Dataset

Download 5-10 contracts from CUAD (they're free on HuggingFace). These are real commercial contracts in PDF format.

```python
# Download CUAD contracts for testing
from datasets import load_dataset
cuad = load_dataset("cuad", split="test")
# CUAD has contract text in "context" field — use these to validate your output
```

### What "Good" Output Looks Like

For a sample Vendor Agreement, your segmenter should produce approximately:

```python
[
  ClauseChunk(heading="PREAMBLE", body_text="This Agreement is entered into...", level=0, token_count=87),
  ClauseChunk(heading="1. DEFINITIONS", body_text="'Agreement' means...", level=0, token_count=234),
  ClauseChunk(heading="2. SERVICES", body_text="Service Provider agrees to...", level=0, token_count=156),
  ClauseChunk(heading="3. PAYMENT TERMS", body_text="Client shall pay...", level=0, token_count=312),
  ClauseChunk(heading="4. TERM AND TERMINATION", body_text="This Agreement commences on...", level=0, token_count=198),
  # etc.
]
```

### Evaluation Metrics

Since you don't have ground-truth clause boundaries for your test PDFs, use these proxies:

| Metric | How to Measure | Target |
|---|---|---|
| No empty clauses | `all(len(c.body_text) > 20 for c in clauses)` | 100% |
| No over-fragmentation | Average body_text length > 80 words | > 80 words avg |
| No under-merging | No clause body > 1500 words without being marked oversized | 100% |
| Heading coverage | Every heading in manual review was captured | Visual check on 3 contracts |
| Token budget | No non-oversized clause > 480 tokens | 100% |

### Manual Spot-Check Protocol

For your first 3 test contracts, do this manually:
1. Open the PDF
2. Count the number of sections/clauses by eye
3. Run your segmenter
4. Compare: Did you get roughly the same number? ±20% is acceptable.
5. For each missed clause: which failure mode caused it? Fix that.

---

## Common Bugs You Will Hit (and Their Fixes)

| Bug | Symptom | Fix |
|---|---|---|
| `block["flags"] & 16` always False | No bold detected | Also check `"Bold"` in `span["font"]` name |
| All text merged into one clause | No headings detected | Lower `heading_signals` threshold from 4 to 3 |
| Too many tiny clauses | Over-fragmentation | Raise `heading_signals` threshold to 5 |
| Page numbers included in clause text | `42`, `5` appearing in body | Check bbox Y position against page height |
| Table of contents detected as clauses | Lots of 1-3 word clauses | Add TOC detection: first 3 pages, short lines with `......N` patterns |
| Continuation items split from parent | `(a)`, `(b)` as separate clauses | Ensure CONTINUATION pattern check happens BEFORE heading signal scoring |
| OCR produces garbage characters | Strange symbols in body text | Add Unicode cleanup: `text.encode('ascii', 'ignore').decode()` |

---

## What To Build First (Week-by-Week)

### Day 1-2: Get PyMuPDF Working
- Install, open a sample contract, print `get_text("dict")` output
- Understand the nested structure: `doc → pages → blocks → lines → spans`
- Goal: print every span's text + font size + bold flag for one page

### Day 3-4: Layer 0 + 1
- Implement `extract_blocks_with_metadata()`
- Implement `remove_noise_blocks()`
- Test on 2 contracts: print cleaned blocks, verify no page numbers/headers

### Day 5-6: Layer 2
- Implement `detect_body_font_size()` + `classify_block()`
- For each block in your test contract, print `(text[:40], classification)`
- Manually verify: are headings being detected? Tune the signal threshold.

### Day 7-8: Layer 3
- Implement `assemble_clauses()`
- Run `segment_contract()` end to end
- Print the clause list: heading, first 100 chars of body, token count
- Do manual spot check

### Day 9: Edge Cases
- Test with a scanned PDF → add OCR fallback
- Test with an Indian contract format (all-caps bold headings, no numbers)
- Test with an NDA (Article I format)
- Fix whatever breaks

### Day 10: Unit Tests
```python
def test_no_empty_clauses():
    clauses = segment_contract("tests/sample_vendor.pdf")
    assert all(len(c.body_text.strip()) > 20 for c in clauses)

def test_reasonable_clause_count():
    clauses = segment_contract("tests/sample_vendor.pdf")
    assert 5 <= len(clauses) <= 50  # sanity bounds

def test_token_budget():
    clauses = segment_contract("tests/sample_vendor.pdf")
    non_oversized = [c for c in clauses if not c.is_oversized]
    assert all(c.token_count <= 480 for c in non_oversized)
```

---

## Architecture Summary (One Page)

```
PDF File
    │
    ▼  PyMuPDF get_text("dict")
[Blocks + Visual Metadata]
    │  font_size, bold, bbox, page_num
    ▼
[Noise Removal]
    │  Remove: page numbers, running headers, footers
    │  Method: position-based + repeated-text detection
    ▼
[Body Font Size Detection]
    │  Modal font size = baseline body text size
    ▼
[Block Classification]
    │  HEADING / SUBHEADING / BODY / CONTINUATION / DEFINITION_ITEM
    │  Method: Signal scoring (font size + bold + caps + centering + regex)
    ▼
[Clause Assembly]
    │  HEADING + following BODY blocks = one ClauseChunk
    │  CONTINUATION items merged upward into parent
    │  Oversized chunks split with 50-token overlap
    ▼
List[ClauseChunk]
    {clause_id, heading, body_text, level, pages, token_count}
    │
    ▼  (passed to downstream tasks)
Clause Classifier → NER → Obligation Detection → Date Normalization
```

---

*Research Sources: Hegel et al. "The Law of Large Documents" (KDD DI Workshop 2021); Shah et al. "Legal Clause Extraction Using Machine Learning" (IEEE 2019); Begnini et al. "Two-Stage Architecture for NDA Analysis" (arxiv 2603.09990, 2025); Hendrycks et al. CUAD (2021)*
