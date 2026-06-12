# ClauseOps — Clause Segmentation: Implementation Walkthrough

## What Was Built

A complete 4-layer hybrid clause segmentation pipeline that converts raw PDF legal contracts into structured `ClauseChunk` objects, ready for downstream ML models.

## Project Structure

```
ClauseOps/
├── clauseops/
│   ├── __init__.py                    # Package root (v0.1.0)
│   ├── segmentation/
│   │   ├── __init__.py                # Public API exports
│   │   ├── models.py                  # TextBlock, ClauseChunk, DefinitionItem
│   │   ├── extractor.py               # Layer 0: PDF extraction + table masking
│   │   ├── noise.py                   # Layer 1: Noise removal
│   │   ├── classifier.py              # Layer 2: Heading detection
│   │   ├── assembler.py               # Layer 3: Clause assembly
│   │   └── pipeline.py                # Master: segment_contract()
│   └── utils/
│       └── __init__.py
├── tests/
│   ├── __init__.py
│   └── test_segmentation.py           # 50 unit tests
├── scripts/
│   └── test_segmenter.py              # CLI test tool
├── venv/                              # Virtual environment
├── requirements.txt
└── pyproject.toml
```

## Pipeline Architecture

```
PDF File
    │
    ▼  Layer 0: extractor.py
[Structural Extraction]
    │  PyMuPDF get_text("dict", sort=True)
    │  find_tables() per page (lines_strict → text fallback)
    │  Table masking via bbox_overlaps(threshold=0.3)
    │  Multi-column detection + left-first re-sorting
    ▼
[TextBlocks + Visual Metadata] + [Table Records]
    │
    ▼  Layer 1: noise.py
[Noise Removal]
    │  Cross-page repetition → remove headers/footers
    │  Position-based → remove page numbers (top/bottom 8%)
    │  Length-based → remove artifacts (< 3 chars)
    ▼
[Clean TextBlocks]
    │
    ▼  Layer 2: classifier.py
[Block Classification]
    │  Modal font size detection (body baseline)
    │  Multi-signal scoring:
    │    Font +3 | Bold +2 | CAPS +2 | Center +1 | Regex +3
    │  CONTINUATION checked first (prevents (a),(b) fragmentation)
    │  → HEADING / SUBHEADING / BODY / CONTINUATION / DEFINITION_ITEM
    ▼
[Classified Blocks]
    │
    ▼  Layer 3: assembler.py
[Clause Assembly]
    │  HEADING + BODY blocks → single CLAUSE chunk
    │  DEFINITION_ITEM → structured DefinitionItem objects
    │  TABLE records → TABLE chunks at correct position
    │  Oversized (>480 tokens) → split with 50-token overlap
    ▼
List[ClauseChunk]
    type: CLAUSE | TABLE | DEFINITION_GROUP
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Token counting: word_count × 1.3** | Avoids 500MB tokenizer download for MVP. Commented code shows how to switch to real tokenizer later. |
| **Table overlap threshold: 0.3** | Compromise between v1's 0.5 (misses tight cells) and Gemini's 0.2 (too aggressive on captions). |
| **spaCy loaded once at module level** | Original blueprint loaded it inside a loop — wasteful for every oversized clause. |
| **OCR deferred entirely** | Clean architecture with `block_type` field ready for future OCR plugin. No half-broken OCR code. |
| **Continuation checked BEFORE scoring** | Critical fix from the research — prevents `(a)`, `(b)` items from being classified as headings. |
| **Definitions as structured children** | Each `DefinitionItem` preserves term↔definition mapping instead of blind token splitting. |

## Test Results

```
50 passed in 2.81s

✅ Data models (3 tests)
✅ Block classification — headings, body, continuation, definitions (12 tests)
✅ Body font size detection (2 tests)
✅ Noise removal — page numbers, headers, artifacts (5 tests)
✅ Definition parsing — means, shall mean, refers to, colon (5 tests)
✅ Token counting (3 tests)
✅ Heading number extraction (8 tests)
✅ Bbox overlap detection (4 tests)
✅ Oversized clause splitting (3 tests)
✅ End-to-end on synthetic PDF (5 tests)
```

## How to Use

```bash
# Activate the virtual environment
.\venv\Scripts\Activate.ps1

# Run on a PDF contract
python scripts/test_segmenter.py path/to/contract.pdf

# With verbose logging
python scripts/test_segmenter.py contract.pdf --verbose

# JSON output for programmatic use
python scripts/test_segmenter.py contract.pdf --json > output.json

# Run unit tests
python -m pytest tests/ -v
```

## What's Next

1. **Test on real PDFs** — Drop any contract PDF into `scripts/test_segmenter.py` to see real results
2. **Tune heading threshold** — If over-fragmenting, raise threshold from 4 to 5 in classifier.py
3. **Add OCR support** — Follow the notes in `extractor.py` to plug in per-page OCR
4. **Switch to real tokenizer** — Follow the comments in `assembler.py:count_tokens()`
5. **Build Clause Classification** — Fine-tune DeBERTa-v3 on LEDGAR (the next pipeline stage)
