# ClauseOps Segmenter — Reality Check Analysis

## Executive Summary

> [!CAUTION]
> **The segmenter is broken on real contracts.** It produces 3-7 chunks from contracts that should have 15-25+ clauses. The root cause is two critical bugs: (1) the table detector is too aggressive, swallowing entire contract pages as "tables", and (2) heading detection fails because these contracts use **identical font sizes** for headings and body.

---

## PDF 1: GoPage Corp — Content License Agreement
**File:** `GopageCorp_20140221_10-K_EX-10.1...Content License Agreement.pdf`
**Pages:** 20

### Ground Truth (what's actually in the PDF)
```
Font size: 8.2 everywhere (headings AND body — same size)
Bold: Only a few random spans, NOT the section headings
Section headings seen in raw text:
  "1. Definitions."
  "2. License."
  "3. License Restrictions."
  "3.1 Content License Restrictions."
  "3.2 ..."
  "3.3 Trademark License."
  "3.4 Reservation of Rights."
  "4. Licensee Obligations."
  "4.1 Content Display."
  "4.2 Required Notices."
  "4.3 Terms of Use."
  "4.4 Content Hosting..."
  "5. Fees and Payment."
  "5.1 License Fees."
  ... and 10+ more sections
```
**Expected clauses: ~20+**

### Segmenter Output
```
Total blocks extracted: 15 (from 20 pages!)
After noise removal: 7 blocks
Tables detected: 18 (!)
Final clauses: 7 (3 TABLE, 4 CLAUSE)
Heading recall: 0%
```

### Root Cause
| Bug | Impact |
|-----|--------|
| **Table detector swallowed the contract** | 18 "tables" detected from a contract with NO real tables. The table finder's `strategy="text"` fallback treats structured legal text (numbered paragraphs) as table rows. |
| **Heading detection impossible** | Font size = 8.2 for EVERYTHING. No bold on headings. No ALL CAPS. The only signal is the regex pattern `^\d+\.\s+` — but the blocks never reach the classifier because they were already eaten by the table detector. |
| **Only 15 blocks from 20 pages** | After table masking, only 15 text blocks survive. Most content is inside "table" bounding boxes and gets excluded. |

---

## PDF 2: Ideanomics — Content License Agreement
**File:** `IdeanomicsInc...Content License Agreement.pdf`
**Pages:** 18

### Ground Truth
```
Font: CourierNewPSMT at 8.2pt everywhere
Some bold spans exist but are ON body text (not headings)
Section headings visible:
  "CONTENT LICENSE AGREEMENT" (centered, ALL CAPS)
  "TERMS AND CONDITIONS" (centered, ALL CAPS)
  "1. Definitions."
  "2. Rights Granted."
  "3. Restrictions on Use."
  "4. Delivery."
  "5. Additional Titles."
  etc.
```
**Expected clauses: ~15-18**

### Segmenter Output
```
Crashed with UnicodeEncodeError (smart quotes)
But from server logs: 5 blocks, 17 tables, 3 final chunks
```

### Root Cause
Same as PDF 1 — table detector is eating the entire document.

---

## PDF 3: EcoScience — Endorsement Agreement
**File:** `EcoScienceSolutionsInc...Endorsement Agreement.pdf`

### From server logs
```
10 text blocks, 6 tables → 4 final chunks (3 CLAUSE, 1 TABLE)
```
Same problem pattern.

---

## Root Cause Analysis

### BUG #1: Table Detector is Catastrophically Aggressive (CRITICAL)

The `strategy="text"` fallback in `extractor.py` treats structured legal paragraphs as tables. In contracts:
- Numbered paragraphs with indentation look like table columns
- Definition lists with hanging indents trigger the "text alignment" heuristic
- `min_words_vertical=3, min_words_horizontal=2` is far too permissive

**This single bug causes 80%+ of the content to be swallowed as "tables".** A 20-page contract produces only 7-15 text blocks because everything else falls inside a table bounding box and gets masked.

### BUG #2: Heading Detection Fails on Same-Size Fonts (CRITICAL)

Many real legal contracts (especially SEC filings) use **identical font sizes** for headings and body text — typically 8-10pt Courier or Times New Roman. Our heading classifier relies heavily on font size (+3 points for `>body*1.15`), which produces 0 signal when everything is the same size.

The headings ARE distinguishable, but only by their **text pattern** (e.g., `"4. Licensee Obligations."`) — the regex signal alone (+3 points) isn't enough to cross the threshold of 4 when there's no font-size or bold support.

### BUG #3: Blocks are Too Coarse

PyMuPDF's block-level extraction sometimes merges multiple paragraphs into a single block. In the GoPage contract, 20 pages produce only 15 blocks total — meaning multiple sections are concatenated into one giant block. The heading at the start of such a block is invisible to the classifier because we classify the whole block as BODY (it's too long to be a heading).

---

## Proposed Fixes

### Fix 1: Disable text-based table detection for contracts
The `strategy="text"` fallback has a ~90% false positive rate on legal contracts. We should:
- Only use `strategy="lines_strict"` (visible gridlines)
- OR add a validation check: reject "tables" that cover more than 60% of the page height (real tables rarely span entire pages)
- OR reject tables where the cell content looks like prose paragraphs

### Fix 2: Lower heading threshold OR use regex-only fallback
When all font sizes are identical:
- Regex match alone should be sufficient to classify as HEADING (lower threshold from 4 to 3)
- OR: if body_font_size ≈ max_font_size (variance < 0.5), switch to "flat font mode" where regex patterns get boosted weight

### Fix 3: Split merged blocks by paragraph boundaries
After extracting blocks, do a **line-level re-segmentation**:
- Within each block, look for lines that match heading patterns
- If found, split the block at that line (the heading becomes a new TextBlock, the rest stays as body)
- This handles the case where PyMuPDF merges "4. Obligations. The client shall pay..." into one block

### Fix 4: Reject "tables" with prose-like cell content
Add a heuristic: if the average cell text length > 50 chars, it's probably prose paragraphs that got mis-detected as a table. Real tables have short cell values (numbers, dates, short labels).
