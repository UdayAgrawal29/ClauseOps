# ClauseOps — Clause Segmentation Implementation Plan

## Goal

Implement the full 4-layer hybrid clause segmentation pipeline that takes a PDF legal contract and produces structured `ClauseChunk` objects, handling tables, definitions, multi-column layouts, noise removal, and heading detection. OCR is **deferred** to a later phase.

## Background & Analysis

I've reviewed all three documents:
- [ClauseOps_Complete_Blueprint.md](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/ClauseOps_Complete_Blueprint.md) — Full project scope
- [ClauseOps_Clause_Segmentation_Deep_Dive.md](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/ClauseOps_Clause_Segmentation_Deep_Dive.md) — v1 segmentation architecture
- [ClauseOps_Segmentation_v2_Fixes.md](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/ClauseOps_Segmentation_v2_Fixes.md) — v2 fixes (table masking, multi-column, per-page OCR, structured definitions)

### Assessment of the Gemini "Giant OCR Block" Feedback

The Gemini suggestion about the "Giant OCR Block Anomaly" is **technically correct** — the v2 code does create one massive `TextBlock` per scanned page, bypassing heading detection. The proposed fix (using `tp.extractDICT()` to get spatial blocks from OCR) is a sound approach. **However**, since you've said OCR is not your current concern, I'll:

1. **Skip the OCR implementation entirely for now** — no `is_page_scanned()`, no `ocr_single_page()`, no OCR block handling
2. **Design the architecture cleanly** so OCR can be plugged in later without refactoring
3. Keep the `block_type` field on `TextBlock` so when OCR is added, the downstream pipeline can distinguish OCR-derived blocks

### Assessment of the `bbox_overlaps` Threshold Suggestion

The suggestion to lower the threshold from `0.5` to `0.2` is **reasonable but aggressive**. A 20% overlap can catch legitimate text near tables (like captions and footnotes). I'll use **`0.3`** as a compromise — catches table cells without being overly aggressive on nearby text.

---

## User Review Required

> [!IMPORTANT]
> **Project Structure Decision**: The blueprint mentions this is a full-stack app (FastAPI + Celery + React). For this segmentation module, I'll create a clean Python package structure under the project root. The segmentation module will be self-contained and importable by the future Celery worker. Does this structure work for you?
>
> ```
> ClauseOps/
> ├── clauseops/                  # Python package root
> │   ├── __init__.py
> │   ├── segmentation/           # The segmentation module
> │   │   ├── __init__.py
> │   │   ├── models.py           # Data classes (TextBlock, ClauseChunk, DefinitionItem)
> │   │   ├── extractor.py        # Layer 0: PDF block extraction + table detection
> │   │   ├── noise.py            # Layer 1: Noise removal
> │   │   ├── classifier.py       # Layer 2: Block classification (heading detection)
> │   │   ├── assembler.py        # Layer 3: Clause assembly
> │   │   └── pipeline.py         # Master function: segment_contract()
> │   └── utils/
> │       └── __init__.py
> ├── tests/
> │   ├── __init__.py
> │   ├── test_segmentation.py    # Unit tests
> │   └── sample_pdfs/            # Test PDFs (you'll add these)
> ├── scripts/
> │   └── test_segmenter.py       # Quick CLI script to test on a PDF
> ├── requirements.txt
> └── pyproject.toml
> ```

> [!IMPORTANT]
> **Tokenizer Dependency**: The v1 plan uses `AutoTokenizer.from_pretrained("nlpaueb/legal-bert-base-uncased")` for token counting. This requires downloading a ~500MB model tokenizer. For the segmentation MVP, I'll use a **lightweight approximation** (`len(text.split()) * 1.3` ≈ token count) with an option to plug in the real tokenizer later. This avoids forcing a HuggingFace download just to count tokens. Sound good?

> [!WARNING]
> **spaCy Dependency for `split_oversized_clause`**: The v1 code loads `en_core_web_sm` inside a loop for sentence boundary detection on oversized clauses. I'll load spaCy **once** at module level and pass it through, avoiding repeated `spacy.load()` calls. This also means spaCy is a required dependency.

---

## Open Questions

1. **Do you have any sample PDF contracts** to test with? If not, I'll include a script that downloads a few from CUAD for testing.
2. **Python version**: Are you using Python 3.10+ ? The code uses `match` statements and type hints that require 3.10+. If you're on 3.9, I'll adjust.
3. **Virtual environment**: Do you have one set up, or should I create one?

---

## Proposed Changes

### Data Models

#### [NEW] [models.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/models.py)

Core data structures used across the pipeline:
- `TextBlock` — represents a single PDF text block with visual metadata (font size, bold, bbox, page_num, indentation, centering, all-caps)
- `DefinitionItem` — a single defined term parsed from a Definitions section (term, definition, raw_text, token_count)
- `ClauseChunk` — a fully assembled clause unit (heading, body_text, level, pages, token_count, chunk_type: CLAUSE/TABLE/DEFINITION_GROUP, sub_chunks for oversized clauses)

---

### Layer 0: Structural Extraction

#### [NEW] [extractor.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/extractor.py)

PDF block extraction with table masking. Key functions:
- `extract_tables_from_page(page)` — Two-strategy table detection (lines_strict → text fallback). Returns bounding boxes + markdown.
- `bbox_overlaps(block_bbox, table_bbox, threshold=0.3)` — Intersection-over-block-area overlap check. Threshold set to **0.3** (compromise between 0.5 and 0.2).
- `extract_blocks_with_metadata(pdf_path)` — Main extraction function. Uses `get_text("dict", sort=True)`. Skips image blocks, masks table regions. Returns `(text_blocks, table_records)`.
- `detect_multi_column_page()` / `sort_blocks_for_multi_column()` — Heuristic multi-column detection and left-first sorting.
- **No OCR handling** — scanned pages are simply skipped with a warning logged. The function signature is designed so OCR can be plugged in later.

---

### Layer 1: Noise Removal

#### [NEW] [noise.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/noise.py)

- `remove_noise_blocks(blocks, page_height)` — Removes:
  - Running headers/footers (text appearing identically on 3+ pages)
  - Page numbers (short numeric text in top/bottom 8% zones)
  - Very short artifacts (< 3 chars)

---

### Layer 2: Block Classification (Heading Detection)

#### [NEW] [classifier.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/classifier.py)

The most critical layer — multi-signal heading detection:
- `detect_body_font_size(blocks)` — Modal font size detection as baseline.
- `classify_block(block, body_font_size)` — Signal scoring system:
  - Font size > body × 1.15 → +3 points
  - Bold text → +2 points
  - ALL CAPS → +2 points
  - Centered on page → +1 point
  - Matches heading regex → +3 points
  - Continuation pattern check runs FIRST (prevents `(a)`, `(b)` from being classified as headings)
  - Threshold: ≥4 signals + short text → HEADING/SUBHEADING
  - Definition item detection via `"Term" means ...` pattern
- **13 heading regex patterns** covering: numbered (`1.`, `1.1`, `1.1.1`), Article, Section, Clause, lettered (`A.`), continuation (`(a)`, `(i)`), legal markers (WHEREAS, NOW THEREFORE, IN WITNESS WHEREOF), and schedules/exhibits/annexes.

---

### Layer 3: Clause Assembly

#### [NEW] [assembler.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/assembler.py)

Combines classified blocks into meaningful clause units:
- `assemble_clauses(blocks, body_font_size, tables)` — Core assembly logic:
  - HEADING → flushes previous clause, starts new one
  - BODY/CONTINUATION → appended to current clause
  - DEFINITION_ITEM → parsed into structured `DefinitionItem` with term/definition separation
  - Tables inserted at correct page positions
  - Oversized clauses (> 480 tokens) split with 50-token sentence-level overlap
- `split_oversized_clause(text, max_tokens, overlap)` — spaCy sentence boundary splitting with overlapping windows
- `parse_definition_item(text)` — Parses `"Term" means ...` patterns into (term, definition) pairs
- `extract_heading_number(heading)` — Extracts section numbers from heading text
- `count_tokens(text)` — Lightweight token estimation (word count × 1.3), with option to use real tokenizer

---

### Pipeline

#### [NEW] [pipeline.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/pipeline.py)

Master orchestration:
- `segment_contract(pdf_path)` → `list[ClauseChunk]`
- Chains: Extract → Noise Removal → Font Baseline → Classify → Assemble
- Returns clean list of `ClauseChunk` objects ready for downstream ML models

#### [NEW] [\_\_init\_\_.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/clauseops/segmentation/__init__.py)

Public API: exports `segment_contract`, `ClauseChunk`, `TextBlock`, `DefinitionItem`

---

### Project Setup

#### [NEW] [pyproject.toml](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/pyproject.toml)

Project metadata and dependencies:
- `pymupdf >= 1.24.0`
- `spacy >= 3.7.0`

#### [NEW] [requirements.txt](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/requirements.txt)

Flat dependency list for `pip install -r requirements.txt`

---

### Testing & CLI

#### [NEW] [test_segmenter.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/scripts/test_segmenter.py)

CLI script to test the segmenter on any PDF:
```
python scripts/test_segmenter.py path/to/contract.pdf
```
Prints formatted output: clause headings, body previews, token counts, types.

#### [NEW] [test_segmentation.py](file:///c:/Users/Uday Agrawal/Desktop/Projects/ClauseOps/tests/test_segmentation.py)

Unit tests:
- No empty clauses
- Reasonable clause count (5–50)
- Token budget (no non-oversized clause > 480 tokens)
- Heading detection accuracy on synthetic blocks
- Noise removal effectiveness
- Definition parsing correctness

---

## Key Improvements Over the Blueprint Docs

| Area | Blueprint Docs | My Implementation |
|---|---|---|
| Token counting | Downloads 500MB LegalBERT tokenizer | Lightweight word-count approximation (swappable) |
| spaCy loading | `spacy.load()` inside `split_oversized_clause` loop | Loaded once at module level |
| OCR | Half-implemented, creates broken giant blocks | Cleanly deferred; architecture ready for plug-in |
| Table overlap threshold | 0.5 (too conservative per Gemini) | 0.3 (balanced) |
| `import re` placement | Inside a loop in `extract_blocks_with_metadata` | Top of module |
| Bold detection | Only checks `span["flags"] & 16` + font name | Also checks `"bold"` case-insensitively in font name |
| Error handling | None | Logging + graceful fallbacks |
| Code structure | Single monolithic code block | Clean modular package with separation of concerns |

---

## Verification Plan

### Automated Tests
1. Run `pytest tests/` for unit tests on synthetic data
2. Run `python scripts/test_segmenter.py <sample.pdf>` on real contracts

### Manual Verification
1. Download 2-3 CUAD contracts (I'll provide a helper script)
2. Run the segmenter, visually compare output clause count vs. manual count
3. Verify: no empty clauses, no fragmented headings, definitions grouped correctly
4. Check: tables detected and masked from regular text flow
