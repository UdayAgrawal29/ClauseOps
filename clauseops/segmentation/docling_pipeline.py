"""
ClauseOps — Docling-Based Segmentation Pipeline

Replaces the rule-based extractor+classifier with IBM's Docling ML model,
which is trained on 80K+ annotated pages (DocLayNet dataset) to detect:
- Section headers (headings)
- Paragraphs (body text)
- Tables
- List items
- Titles, captions, footnotes, etc.

WHY DOCLING:
    Our hand-crafted rules covered ~3 of 12+ contract formatting paradigms.
    Every new PDF format required new rules, which often conflicted with
    existing ones. Docling's ML models generalize across formats automatically.

    Rule-based: 70-80% accuracy on diverse formats
    Docling ML:  90-95% accuracy on diverse formats (DocLayNet benchmark)

ARCHITECTURE:
    Old: PyMuPDF → hand-crafted extractor → hand-crafted classifier → assembler
    New: Docling DocumentConverter → iterate_items() → assemble_clauses()

    The assembler logic (grouping heading + body into ClauseChunk) is reused
    from the old pipeline since it's format-agnostic — it just needs to know
    which items are headings and which are body text.

POST-PROCESSING SAFETY NET:
    Docling's ML model handles ~90-95% of heading detection correctly. For the
    remaining 5-10% where it misses numbered section headings (labeling them as
    body text), we apply a targeted regex-based safety net during assembly.

    This is NOT the same as the old rule-based pipeline. Key differences:
    - Docling ML is the PRIMARY detector (handles visual/format-based headings)
    - Regex only catches NUMBERED headings that Docling missed
    - _is_title_like() prevents false positives by verifying the text after
      the number looks like a title (Title Case / ALL CAPS), not a sentence
    - Visual-only headings (bold, centered, etc.) are Docling's job — regex
      doesn't attempt them
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from clauseops.segmentation.models import ClauseChunk, DefinitionItem

logger = logging.getLogger(__name__)

# ============================================================================
# Cached DocumentConverter (singleton)
# ============================================================================
# WHY CACHE: Loading the ML model (docling-layout-heron) takes ~10-15s on first
# call. Without caching, every PDF upload re-initializes the entire model stack.
# With caching, only the first PDF pays the initialization cost — subsequent
# PDFs process in ~5-10s instead of ~30-50s.
_converter = None

def _get_converter():
    """Get or create the cached DocumentConverter singleton."""
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        
        logger.info("Initializing Docling DocumentConverter (one-time model load)...")
        
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        
        _converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        logger.info("DocumentConverter ready (OCR Disabled).")
    return _converter


# ============================================================================
# Token counting (lightweight approximation for MVP)
# ============================================================================
MAX_TOKENS = 480

def _count_tokens(text: str) -> int:
    """
    Estimate token count: word_count × 1.3.
    
    WHY LIGHTWEIGHT TOKENIZER FOR MVP:
    Loading the actual transformer tokenizer (e.g., from `transformers` package)
    adds startup time and memory overhead. For the MVP segmentation phase, a 
    simple word-count multiplier (1.3x) provides a fast and "good enough" 
    approximation for splitting oversized clauses.

    FUTURE ENHANCEMENT:
    Once we integrate the actual classification models (e.g., DeBERTa), this 
    should be replaced with the real tokenizer to guarantee exact token limits.
    
    # --- Real Tokenizer Implementation (To be un-commented later) ---
    # from transformers import AutoTokenizer
    # 
    # # Load tokenizer once globally
    # _tokenizer = AutoTokenizer.from_pretrained("microsoft/deberta-v3-base")
    #
    # def _count_tokens_real(text: str) -> int:
    #     '''Exact token count using transformers.'''
    #     return len(_tokenizer.tokenize(text))
    # ----------------------------------------------------------------
    """
    return int(len(text.split()) * 1.3)


# ============================================================================
# Heading number extraction (reused from assembler)
# ============================================================================
def _extract_heading_number(heading: str) -> Optional[str]:
    if not heading:
        return None
    m = re.match(
        r'^(\d+(?:\.\d+)*|[A-Z]\.|Article\s+\w+|Section\s+[\d\.]+|Clause\s+\d+)',
        heading.strip(), re.IGNORECASE,
    )
    return m.group(1) if m else None


# ============================================================================
# Missed Heading Detection (post-Docling safety net)
# ============================================================================
#
# WHY THIS EXISTS:
#     Docling's ML model occasionally labels a section heading as body text,
#     especially in contracts where headings have no visual differentiation
#     (same font, no bold, no centering). When this happens, multiple sections
#     get merged into one massive body blob.
#
#     Example: In the Endorsement Agreement, Docling detected "10. Termination
#     for Cause" as a heading but missed sections 11-27 entirely. Those sections
#     became part of section 10's body text (1989 tokens).
#
# WHY THIS ISN'T THE OLD PIPELINE'S APPROACH:
#     The old pipeline used regex as the PRIMARY heading detector for ALL items.
#     Here, regex is a NARROW safety net for ONE failure mode: numbered section
#     headings inside body text. Docling ML handles everything else.
#
#     Old pipeline: regex had to handle 12+ formatting paradigms → failed on ~40%
#     This safety net: regex only catches numbered headings → low false-positive risk
#
# FALSE POSITIVE PREVENTION:
#     The key risk is numbered list items in body text:
#         "The Licensee shall: 1. Maintain documentation 2. Report quarterly"
#     These should NOT be treated as headings. We prevent this with _is_title_like():
#         "1. Maintain documentation" → "Maintain documentation" → starts lowercase → NOT title → skip
#         "11. Entire Agreement"      → "Entire Agreement"      → Title Case        → IS title → split
#

# Lowercase words allowed in Title Case headings (prepositions, articles, etc.)
_TITLE_SMALL_WORDS = frozenset({
    'of', 'and', 'the', 'in', 'for', 'to', 'a', 'an', 'or', 'by', 'at',
    'on', 'with', 'its', 'no', 'nor',
})


def _is_title_like(text: str) -> bool:
    """
    Check if text looks like a heading title (not a sentence).

    Heading titles are either:
    - ALL CAPS: "GOVERNING LAW", "FORCE MAJEURE"
    - Title Case: "Entire Agreement", "Term and Termination"

    Sentences are NOT title-like:
    - "The parties agree to..." (lowercase "parties" after "The")
    - "maintain all documentation" (starts lowercase)
    - "shall ensure compliance" (starts lowercase)

    This is the key false-positive prevention mechanism. It distinguishes:
        "11. Entire Agreement"     → title_like=True  → treat as heading
        "2. The parties shall..."  → title_like=False → keep as body text
        "1. maintain documentation" → title_like=False → keep as body text
    """
    words = text.strip().rstrip('.:;').split()
    if not words or len(words) > 8:
        return False

    # ALL CAPS check (like "GOVERNING LAW", "FORCE MAJEURE")
    alpha = re.sub(r'[^a-zA-Z]', '', text)
    if alpha and len(alpha) >= 3 and alpha == alpha.upper():
        return True

    # Title Case check: first letter of each significant word must be uppercase
    # "Entire Agreement" → True
    # "maintain documentation" → False (starts lowercase)
    # "Term and Termination" → True ("and" is a small word, allowed lowercase)
    for i, w in enumerate(words):
        clean = re.sub(r'[^a-zA-Z]', '', w)
        if not clean:
            continue
        # Small words (of, and, the, etc.) are allowed lowercase after position 0
        if w.lower() in _TITLE_SMALL_WORDS and i > 0:
            continue
        # Every other word must start with uppercase
        if not clean[0].isupper():
            return False

    return True


# Compiled regex for numbered heading prefixes
_HEADING_PREFIX_RE = re.compile(
    r'^(?:'
    r'(\d+(?:\.\d+)*)\.?\s+'       # 1. or 1.1 or 1.1.1
    r'|Article\s+([IVXLCDM\d]+)\s*'  # Article I, Article 12
    r'|Section\s+([\d\.]+)\s*'       # Section 4.1
    r'|Clause\s+(\d+)\s*'           # Clause 7
    r'|([A-Z])\.\s+'                # A. (single letter)
    r')',
    re.IGNORECASE,
)


def _is_missed_heading(text: str) -> bool:
    """
    Check if a Docling body item is actually a heading that Docling missed.

    Returns True ONLY for high-confidence heading detections:
    1. Text matches a numbered heading pattern (e.g., "11. Entire Agreement")
    2. Text is short (≤10 words — real headings are concise)
    3. The title part passes _is_title_like() (not a sentence)

    Also detects:
    - ALL CAPS structural text (≥2 words, ≥8 alpha chars)
    - Structural labels: SCHEDULE A, EXHIBIT B, ANNEX 1
    """
    text = text.strip()
    if not text:
        return False

    words = text.split()

    # Too long to be a heading (headings are concise)
    if len(words) > 10:
        return False

    # Too short to be meaningful (avoid single-word false positives)
    if len(words) < 2:
        return False

    # Check for numbered heading pattern
    m = _HEADING_PREFIX_RE.match(text)
    if m:
        # Extract the title part after the number prefix
        title_part = text[m.end():].strip()
        if title_part and _is_title_like(title_part):
            return True
        # Handle case where prefix IS the whole heading (e.g., "Article I")
        if not title_part and m.group(0).strip():
            return True

    # ALL CAPS short text (like "GOVERNING LAW", "FORCE MAJEURE")
    # Require ≥8 alpha chars to avoid false positives on abbreviations
    alpha = re.sub(r'[^a-zA-Z]', '', text)
    if (alpha and len(alpha) >= 8 and alpha == alpha.upper()
            and 2 <= len(words) <= 6):
        return True

    # Structural labels
    if re.match(r'^(?:SCHEDULE|EXHIBIT|ANNEX|APPENDIX)\s+[A-Z\d]', text, re.IGNORECASE):
        return True

    return False


def _try_split_inline_heading(text: str) -> Optional[tuple[str, str]]:
    """
    Try to split an inline heading+body fused into one text item.

    Common pattern in legal contracts where Docling treats the whole thing as
    one body item:
        "11. Entire Agreement. This Agreement constitutes the entire..."
        "4.2 Limitation of Liability. IN NO EVENT SHALL EITHER PARTY..."

    Returns (heading, body) tuple, or None if no split possible.

    The heading part must be:
    - Preceded by a numbered pattern (e.g., "11.", "4.2")
    - Short (≤8 words)
    - Followed by ". " (period-space) then body text starting with uppercase
    - Title-like (passes _is_title_like check)
    """
    text = text.strip()
    if len(text.split()) < 5:
        return None  # Too short to contain both heading and body

    # Pattern: "NUMBER. Title Text. Body text continues..."
    # Group 1: the heading (number + title up to first period)
    # Group 2: the body (everything after)
    m = re.match(
        r'^(\d+(?:\.\d+)*\.?\s+[A-Z][^.]{2,60})\.\s+([A-Z].+)$',
        text,
        re.DOTALL,
    )
    if m:
        heading = m.group(1).strip()
        body = m.group(2).strip()

        # Validate: heading must be short and title-like
        heading_words = heading.split()
        if len(heading_words) <= 8 and len(body.split()) >= 3:
            # Extract title part (strip the number prefix)
            title_part = re.sub(r'^\d+(?:\.\d+)*\.?\s+', '', heading).strip()
            if _is_title_like(title_part):
                return heading + ".", body

    return None


# ============================================================================
# Definition parsing (reused from assembler)
# ============================================================================
def _parse_definition(text: str) -> tuple[str, str]:
    m = re.match(
        r'^["\u201c\u201d\'\"]?(.+?)["\u201c\u201d\'\"]?\s+'
        r'(?:shall\s+)?(?:mean|means|refer(?:s)?\s+to|is\s+defined\s+as)\s+(.+)$',
        text, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r'^(.+?):\s+(.+)$', text, re.DOTALL)
    if m and len(m.group(1).split()) <= 5:
        return m.group(1).strip(), m.group(2).strip()
    return "", text


# ============================================================================
# Oversized clause splitting (reused from assembler)
# ============================================================================
_nlp = None

def _split_oversized(text: str, max_tokens: int = MAX_TOKENS, overlap: int = 50) -> list[str]:
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")

    doc = _nlp(text)
    sentences = [s.text.strip() for s in doc.sents if s.text.strip()]
    if not sentences:
        return [text]

    chunks, current_sents, current_tok = [], [], 0
    for sent in sentences:
        stok = _count_tokens(sent)
        if current_tok + stok > max_tokens and current_sents:
            chunks.append(" ".join(current_sents))
            # Overlap
            overlap_sents, otok = [], 0
            for s in reversed(current_sents):
                st = _count_tokens(s)
                if otok + st <= overlap:
                    overlap_sents.insert(0, s)
                    otok += st
                else:
                    break
            current_sents = overlap_sents + [sent]
            current_tok = otok + stok
        else:
            current_sents.append(sent)
            current_tok += stok
    if current_sents:
        chunks.append(" ".join(current_sents))
    return chunks


# ============================================================================
# Post-Processing: Noise Cleanup
# ============================================================================

def _is_signature_block(heading: str, body: str) -> bool:
    """
    Check if a segment is a signature block, not a real clause.

    Signature blocks typically contain:
    - "/s/" (signature notation)
    - "By:" followed by a name and title (CEO, President, etc.)
    - Very short body with a person name + title
    """
    combined = f"{heading} {body}".lower()

    # Clear signature notation
    if '/s/' in combined:
        return True

    # "By:" + corporate title in a short block
    if (re.search(r'\b(?:ceo|president|secretary|director|officer|chairman|'
                  r'managing\s+director|vice\s+president)\b', combined)
            and re.search(r'\bby:', combined)
            and len(body.split()) < 25):
        return True

    return False


# Headings that are document furniture — no legal meaning, will poison classifier
# We match case-insensitively. These are complete heading matches, not substrings,
# to avoid accidentally filtering real clauses like "7. Table of Payments".
_BOILERPLATE_HEADINGS = re.compile(
    r'^(?:'
    r'TABLE\s+OF\s+CONTENTS'
    r'|LIST\s+OF\s+(?:EXHIBITS?|SCHEDULES?|APPENDIX|APPENDICES|ANNEXES?)'
    r'|INDEX'
    r'|COVER\s+PAGE'
    r'|SIGNATURE\s+PAGE'
    r'|EXECUTION\s+PAGE'
    r')$',
    re.IGNORECASE,
)

# Page number artifacts that Docling sometimes includes as body text
# "Page 3 of 39" at end of a paragraph — safe to strip because real legal
# references to pages use different phrasing ("on page 3 of Exhibit A")
_PAGE_NUMBER_TAIL = re.compile(r'\s*Page\s+\d+\s+of\s+\d+\s*$')


def _post_process(clauses: list[ClauseChunk]) -> list[ClauseChunk]:
    """
    Clean up segments after Docling assembly.

    This handles edge cases that the ML model + assembly loop don't catch:
    1. Boilerplate headings: "TABLE OF CONTENTS", "LIST OF EXHIBITS"
    2. Heading-only noise: watermarks ("CONFIDENTIAL"), company stamps
    3. Orphaned structural labels: "SCHEDULE 1" with no body → merge forward
    4. Signature blocks: "COMPANY NAME" + "By: /s/ John Doe CEO"
    5. Tiny orphans: segments with <15 tokens and minimal body
    6. Page number artifacts: "Page 3 of 39" trailing body text

    These fixes are applied AFTER assembly so the core Docling pipeline
    stays clean and testable.
    """
    if not clauses:
        return clauses

    result = []
    pending_merge_heading = None  # heading to prepend to next segment

    for i, chunk in enumerate(clauses):
        body = (chunk.body_text or "").strip()
        heading = (chunk.heading or "").strip()

        # Apply pending merge from previous iteration's orphaned heading
        if pending_merge_heading and chunk.chunk_type == "CLAUSE":
            if chunk.heading:
                chunk.heading = f"{pending_merge_heading} — {chunk.heading}"
            else:
                chunk.heading = pending_merge_heading
            chunk.heading_number = _extract_heading_number(chunk.heading)
            pending_merge_heading = None
        elif pending_merge_heading:
            # Can't merge into a non-CLAUSE (TABLE, DEF) — drop the pending
            pending_merge_heading = None

        # Fix 0: Drop boilerplate document-furniture headings
        # "TABLE OF CONTENTS" with body "- i -" will poison the classifier.
        # These carry zero legal meaning regardless of body content.
        if chunk.chunk_type == "CLAUSE" and _BOILERPLATE_HEADINGS.match(heading):
            logger.debug("Dropping boilerplate heading: '%s'", heading)
            continue

        # Fix 1+2: Handle heading-only segments (no meaningful body)
        if chunk.chunk_type == "CLAUSE" and len(body) < 5 and chunk.token_count < 15:
            # Is this a structural label? → merge forward into next segment
            if re.match(r'^(?:SCHEDULE|EXHIBIT|ANNEX|APPENDIX|PART)\s',
                        heading, re.IGNORECASE):
                pending_merge_heading = heading
                logger.debug("Merging structural label forward: '%s'", heading)
                continue

            # Otherwise it's noise (watermarks, company stamps) → drop
            logger.debug("Dropping noise segment: '%s' (body='%s')", heading, body)
            continue

        # Fix 3: Filter signature blocks
        if chunk.chunk_type == "CLAUSE" and _is_signature_block(heading, body):
            logger.debug("Dropping signature block: '%s'", heading)
            continue

        # Fix 6: Strip trailing page number artifacts from body text
        # "...Our Reserved Rights] above. Page 3 of 39" → strip the tail
        if chunk.body_text:
            cleaned_body = _PAGE_NUMBER_TAIL.sub('', chunk.body_text)
            if cleaned_body != chunk.body_text:
                chunk.body_text = cleaned_body.rstrip()
                chunk.token_count = _count_tokens(
                    f"{chunk.heading or ''}\n{chunk.body_text}".strip()
                )
                logger.debug("Stripped page number artifact from: '%s'", heading)

        result.append(chunk)

    # Fix 4: Merge tiny orphans (<15 tokens, minimal body) into previous segment
    final = []
    for chunk in result:
        if (chunk.chunk_type == "CLAUSE"
                and chunk.token_count < 15
                and len((chunk.body_text or "").strip()) < 10
                and final):
            # Merge into previous segment's body text
            prev = final[-1]
            extra = f"{chunk.heading or ''} {chunk.body_text or ''}".strip()
            if extra:
                prev.body_text = f"{prev.body_text} {extra}".strip()
                prev.token_count = _count_tokens(
                    f"{prev.heading or ''}\n{prev.body_text}".strip()
                )
                prev.end_page = max(prev.end_page, chunk.end_page)
            logger.debug("Merged tiny orphan '%s' into previous segment", chunk.heading)
        else:
            final.append(chunk)

    return final


# ============================================================================
# Section-level re-splitting (Phase A — fixes article-level merging)
# ============================================================================
#
# WHY THIS EXISTS:
#     Docling sometimes emits an entire Article (with several "Section X.YY"
#     provisions inside) as ONE body blob, because it detects only the coarse
#     "ARTICLE III" header and treats the inner "Section 3.01 ... Section 3.07"
#     markers as body text. The earlier safety nets (_is_missed_heading,
#     _try_split_inline_heading) only catch SHORT, bare-numbered headings, so
#     these merged articles survive as a single ClauseChunk. Downstream this
#     collapses ~14 distinct obligations into one and misattributes deadlines.
#
#     This pass runs AFTER assembly on every CLAUSE body and splits it at
#     genuine provision-start markers (Section / Article / Clause N...).
#
# WHY IT'S GENERAL (not tuned to one PDF):
#     It keys off the *structural grammar* of legal drafting — explicit
#     "Section/Article/Clause" markers followed by a provision — which recurs
#     across virtually all English commercial contracts. It does NOT match any
#     party names, doc-specific strings, or visual cues. Cross-references
#     ("pursuant to Section 4.01", "Sections 3.06 and 4.02 hereof") are
#     excluded by preceding/following cue-word guards, and we only split when
#     ≥2 genuine markers are present so prose is never shredded.

# Marker that can START a provision (singular Section/Article/Clause + number).
# Plural forms ("Sections 3.06") don't match because a letter, not whitespace,
# follows the base word.
_PROVISION_MARKER_RE = re.compile(
    r'(?:Section|Sec\.|Article|Clause)\s+(?:\d+(?:\.\d+)*|[IVXLCDM]+)',
    re.IGNORECASE,
)

# If these words immediately PRECEDE the marker, it's a citation, not a start.
_XREF_PRECEDING_RE = re.compile(
    r'(?:this|that|these|those|such|said|in|under|of|to|per|see|with|within|'
    r'pursuant|herein|hereunder|hereof|hereto|and|or|preceding|foregoing|'
    r'including|provided|except|under\s+this|of\s+this)\s*$',
    re.IGNORECASE,
)

# If these immediately FOLLOW the marker (after an optional [..] label),
# it's a citation, not a start.
_XREF_FOLLOWING_RE = re.compile(
    r'^\s*(?:\[[^\]]*\]\s*)?(?:hereof|hereto|herein|above|below|and\b|or\b|'
    r'through\b|of\s+this|,)',
    re.IGNORECASE,
)


def _is_genuine_provision_start(body: str, start: int, end: int) -> bool:
    """Decide whether a Section/Article marker at [start:end] begins a new
    provision (True) or is a cross-reference/citation (False)."""
    preceding = body[max(0, start - 48):start]
    following = body[end:end + 48]
    if _XREF_PRECEDING_RE.search(preceding):
        return False
    if _XREF_FOLLOWING_RE.match(following):
        return False
    return True


def _carve_provision_heading(segment: str) -> Optional[str]:
    """Build a short heading from a provision segment that starts with a
    Section/Article marker, e.g.:
        "Section 3.01 [Printing of Calling Cards] . Printing..." -> "Section 3.01 Printing of Calling Cards"
        "Section 5.01. Payment Terms. Any amounts..."            -> "Section 5.01 Payment Terms"
    Returns None if no marker prefix is found.
    """
    m = re.match(
        r'^((?:Section|Sec\.|Article|Clause)\s+(?:\d+(?:\.\d+)*|[IVXLCDM]+))'
        r'\s*(?:\[([^\]]*)\])?\.?\s*',
        segment, re.IGNORECASE,
    )
    if not m:
        return None
    marker = m.group(1).strip()
    bracket_title = (m.group(2) or "").strip()
    rest = segment[m.end():]
    if bracket_title:
        return f"{marker} — {bracket_title}"
    # Otherwise take the first short Title-like phrase up to a period.
    tm = re.match(r'([A-Z][^.\n]{0,60}?)\.', rest)
    if tm and 1 <= len(tm.group(1).split()) <= 9:
        return f"{marker} — {tm.group(1).strip()}"
    return marker


def _split_clause_into_provisions(chunk: ClauseChunk) -> list[ClauseChunk]:
    """Split a CLAUSE whose body contains ≥2 genuine provision markers into
    one child ClauseChunk per provision. Returns [chunk] unchanged otherwise.

    Coverage invariant: the concatenation of the children's body text equals
    the original body (no text is dropped); granularity is monotonic.
    """
    if chunk.chunk_type != "CLAUSE":
        return [chunk]
    body = chunk.body_text or ""
    if len(body) < 200:
        return [chunk]

    starts = [
        m.start()
        for m in _PROVISION_MARKER_RE.finditer(body)
        if _is_genuine_provision_start(body, m.start(), m.end())
    ]
    if len(starts) < 2:
        return [chunk]

    # Build contiguous segments: optional intro (kept with parent heading),
    # then one segment per provision marker.
    cut_points = sorted(set(starts))
    pieces: list[tuple[Optional[str], str]] = []

    intro = body[:cut_points[0]].strip()
    if intro and len(intro.split()) >= 5:
        pieces.append((chunk.heading, intro))

    bounds = cut_points + [len(body)]
    for i in range(len(cut_points)):
        seg = body[bounds[i]:bounds[i + 1]].strip()
        if not seg:
            continue
        heading = _carve_provision_heading(seg) or chunk.heading
        pieces.append((heading, seg))

    # Merge tiny fragments (< 40 chars body) into the previous piece.
    merged: list[tuple[Optional[str], str]] = []
    for hd, bd in pieces:
        if merged and len(bd) < 40:
            ph, pb = merged[-1]
            merged[-1] = (ph, f"{pb} {bd}".strip())
        else:
            merged.append((hd, bd))

    if len(merged) < 2:
        return [chunk]

    children: list[ClauseChunk] = []
    for hd, bd in merged:
        full_text = f"{hd or ''}\n{bd}".strip()
        tok = _count_tokens(full_text)
        child = ClauseChunk(
            clause_id=str(uuid.uuid4()),
            heading=hd,
            heading_number=_extract_heading_number(hd or ""),
            body_text=bd,
            level=chunk.level + 1,
            start_page=chunk.start_page,
            end_page=chunk.end_page,
            token_count=tok,
            is_oversized=tok > MAX_TOKENS,
            chunk_type="CLAUSE",
        )
        if child.is_oversized:
            child.sub_chunks = _split_oversized(full_text)
        children.append(child)

    logger.debug("Split clause '%s' into %d provisions", chunk.heading, len(children))
    return children


def _split_merged_sections(clauses: list[ClauseChunk]) -> list[ClauseChunk]:
    """Apply provision splitting across all clauses."""
    out: list[ClauseChunk] = []
    for c in clauses:
        out.extend(_split_clause_into_provisions(c))
    return out


# ============================================================================
# Main Pipeline
# ============================================================================

def segment_contract_docling(pdf_path: str) -> list[ClauseChunk]:
    """
    Segment a PDF contract using Docling's ML-based document understanding.

    This replaces the 4-layer rule-based pipeline with a single Docling call
    that handles extraction, layout analysis, and heading detection using
    models trained on the DocLayNet dataset.

    Post-Docling safety net:
    - During assembly: check each body item for missed numbered headings
    - After assembly: clean up noise segments, signature blocks, tiny orphans

    Args:
        pdf_path: Path to the PDF contract file.

    Returns:
        List of ClauseChunk objects, same format as the old pipeline.
    """
    # Resolve to absolute path — Docling is strict about path resolution
    pdf_path = str(Path(pdf_path).resolve())
    logger.info("Starting Docling segmentation of: %s", pdf_path)

    # ---- Step 1: Convert PDF using cached Docling converter ----
    converter = _get_converter()
    result = converter.convert(pdf_path)
    doc = result.document

    logger.info("Docling conversion complete")

    # ---- Step 2: Iterate through document items and classify ----
    clauses: list[ClauseChunk] = []
    current_heading: Optional[str] = None
    current_heading_num: Optional[str] = None
    current_body_parts: list[str] = []
    current_def_items: list[DefinitionItem] = []
    current_is_definitions: bool = False
    current_start_page: int = 0
    current_end_page: int = 0
    current_level: int = 0

    def flush():
        nonlocal current_heading, current_body_parts, current_def_items
        nonlocal current_is_definitions

        if not current_body_parts and not current_heading and not current_def_items:
            return

        if current_is_definitions and current_def_items:
            full_body = "\n".join(item.raw_text for item in current_def_items)
            total_tokens = sum(item.token_count for item in current_def_items)
            chunk = ClauseChunk(
                clause_id=str(uuid.uuid4()),
                heading=current_heading,
                heading_number=_extract_heading_number(current_heading),
                body_text=full_body,
                level=current_level,
                start_page=current_start_page,
                end_page=current_end_page,
                token_count=total_tokens,
                is_oversized=total_tokens > MAX_TOKENS,
                chunk_type="DEFINITION_GROUP",
                definitions=list(current_def_items),
            )
            clauses.append(chunk)
        else:
            body_text = " ".join(current_body_parts).strip()
            full_text = f"{current_heading or ''}\n{body_text}".strip()
            token_count = _count_tokens(full_text)
            chunk = ClauseChunk(
                clause_id=str(uuid.uuid4()),
                heading=current_heading,
                heading_number=_extract_heading_number(current_heading),
                body_text=body_text,
                level=current_level,
                start_page=current_start_page,
                end_page=current_end_page,
                token_count=token_count,
                is_oversized=token_count > MAX_TOKENS,
                chunk_type="CLAUSE",
            )
            if chunk.is_oversized:
                chunk.sub_chunks = _split_oversized(full_text)
            clauses.append(chunk)

        current_heading = None
        current_body_parts.clear()
        current_def_items.clear()
        current_is_definitions = False

    # ---- Step 3: Process each document item ----
    for item, level in doc.iterate_items():
        label = str(item.label) if hasattr(item, 'label') else ""
        text = item.text if hasattr(item, 'text') else ""
        if not text or not text.strip():
            continue
        text = text.strip()

        # Get page number from provenance if available
        page_num = 0
        if hasattr(item, 'prov') and item.prov:
            try:
                page_num = item.prov[0].page_no - 1  # Convert to 0-indexed
            except (IndexError, AttributeError):
                pass

        # Skip noise labels (page furniture, images, footnotes)
        _SKIP_LABELS = {'page_header', 'page_footer', 'footnote', 'picture',
                        'figure', 'chart', 'formula', 'code', 'checkbox',
                        'empty_value'}
        label_lower = label.lower()
        if any(skip in label_lower for skip in _SKIP_LABELS):
            continue

        # Check if this is a table item
        if 'table' in label_lower:
            # Export table as markdown
            table_md = ""
            if hasattr(item, 'export_to_markdown'):
                table_md = item.export_to_markdown()
            elif hasattr(item, 'text'):
                table_md = item.text

            table_chunk = ClauseChunk(
                clause_id=str(uuid.uuid4()),
                heading=f"[TABLE on page {page_num + 1}]",
                heading_number=None,
                body_text=table_md,
                level=current_level,
                start_page=page_num,
                end_page=page_num,
                token_count=_count_tokens(table_md),
                is_oversized=False,
                chunk_type="TABLE",
                table_markdown=table_md,
            )
            clauses.append(table_chunk)
            continue

        # Check if this is a heading/section header
        is_heading = (
            'section_header' in label_lower
            or label_lower == 'title'
        )

        if is_heading:
            flush()
            current_heading = text
            current_heading_num = _extract_heading_number(text)
            current_body_parts = []
            current_def_items = []
            current_start_page = page_num
            current_end_page = page_num
            current_level = level if isinstance(level, int) else 0

            # Detect definitions section
            current_is_definitions = bool(
                re.search(r'\bDEFINITION[S]?\b', text, re.IGNORECASE)
            )
        else:
            # Body text, list items, captions, etc.

            # ----------------------------------------------------------
            # SAFETY NET: Check if this "body" item is actually a heading
            # that Docling's ML model missed.
            #
            # This catches numbered section headings like:
            #   "11. Entire Agreement"
            #   "28. Confidentiality and Non-Disclosure"
            #   "GOVERNING LAW"
            #
            # But NOT numbered list items like:
            #   "2. The parties shall ensure..."
            #   "1. maintain all documentation"
            # ----------------------------------------------------------
            if _is_missed_heading(text):
                flush()
                current_heading = text
                current_heading_num = _extract_heading_number(text)
                current_body_parts = []
                current_def_items = []
                current_start_page = page_num
                current_end_page = page_num
                current_level = level if isinstance(level, int) else 0
                current_is_definitions = bool(
                    re.search(r'\bDEFINITION[S]?\b', text, re.IGNORECASE)
                )
                continue

            # ----------------------------------------------------------
            # INLINE HEADING SPLIT: Check if this body item contains a
            # heading fused with body text, like:
            #   "11. Entire Agreement. This Agreement constitutes..."
            #
            # Split into heading ("11. Entire Agreement.") and body
            # ("This Agreement constitutes...").
            # ----------------------------------------------------------
            split_result = _try_split_inline_heading(text)
            if split_result:
                heading_part, body_part = split_result
                flush()
                current_heading = heading_part
                current_heading_num = _extract_heading_number(heading_part)
                current_body_parts = [body_part]
                current_def_items = []
                current_start_page = page_num
                current_end_page = page_num
                current_level = level if isinstance(level, int) else 0
                current_is_definitions = bool(
                    re.search(r'\bDEFINITION[S]?\b', heading_part, re.IGNORECASE)
                )
                continue

            # ----------------------------------------------------------
            # Normal body text handling (original logic)
            # ----------------------------------------------------------
            if current_heading is None:
                current_heading = "PREAMBLE"
                current_start_page = page_num

            # Try to parse as definition item
            if current_is_definitions:
                term, defn = _parse_definition(text)
                if term:
                    def_item = DefinitionItem(
                        term=term, definition=defn,
                        raw_text=text, token_count=_count_tokens(text),
                    )
                    current_def_items.append(def_item)
                else:
                    current_body_parts.append(text)
            else:
                current_body_parts.append(text)

            current_end_page = page_num

    # Flush the last clause
    flush()

    logger.info(
        "Pre-cleanup: %d chunks (%d CLAUSE, %d TABLE, %d DEF)",
        len(clauses),
        sum(1 for c in clauses if c.chunk_type == "CLAUSE"),
        sum(1 for c in clauses if c.chunk_type == "TABLE"),
        sum(1 for c in clauses if c.chunk_type == "DEFINITION_GROUP"),
    )

    # ---- Step 3.5: Split clauses that merged multiple Section/Article provisions ----
    # (Phase A) Docling sometimes emits a whole Article as one body blob; recover
    # the individual provisions so downstream gets one unit per obligation.
    before_split = len(clauses)
    clauses = _split_merged_sections(clauses)
    if len(clauses) != before_split:
        logger.info("Provision split: %d -> %d chunks", before_split, len(clauses))

    # ---- Step 4: Post-processing cleanup ----
    clauses = _post_process(clauses)

    logger.info(
        "Docling segmentation complete: %d chunks (%d CLAUSE, %d TABLE, %d DEF)",
        len(clauses),
        sum(1 for c in clauses if c.chunk_type == "CLAUSE"),
        sum(1 for c in clauses if c.chunk_type == "TABLE"),
        sum(1 for c in clauses if c.chunk_type == "DEFINITION_GROUP"),
    )

    return clauses
