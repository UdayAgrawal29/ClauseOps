"""
ClauseOps — Data Models for Clause Segmentation

Core data structures used across the entire segmentation pipeline:
- TextBlock:      A single PDF text block with full visual metadata
- DefinitionItem: A parsed "Term" means ... definition entry
- ClauseChunk:    A fully assembled, segmented clause unit

These are plain dataclasses — no external dependencies.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextBlock:
    """
    A single block of text extracted from a PDF page with full visual metadata.

    PyMuPDF's get_text("dict") returns text grouped into blocks, where each
    block is a cluster of text lines that the PDF renderer considers spatially
    grouped. We augment each block with computed visual properties that are
    critical for heading detection in Layer 2.

    Attributes:
        text:         The full text content of the block (all lines joined).
        page_num:     Zero-indexed page number where this block appears.
        bbox:         Bounding box as (x0, y0, x1, y1) in PDF coordinates.
        font_size:    Average font size across all spans in this block.
        is_bold:      True if ANY span in the block uses a bold font.
        is_italic:    True if any span uses italic styling.
        is_all_caps:  True if the alphabetic portion of the text is all uppercase.
        is_centered:  True if the block's horizontal center is within 10% of page center.
        indentation:  The x0 coordinate (left edge) — used to detect indented subclauses.
        block_type:   'text' for normal digital text, 'ocr_text' for OCR-derived blocks.
                      The downstream pipeline uses this to apply different classification
                      strategies (OCR blocks lack font/bold metadata).
    """
    text: str
    page_num: int
    bbox: tuple          # (x0, y0, x1, y1) position on page
    font_size: float
    is_bold: bool
    is_italic: bool
    is_all_caps: bool
    is_centered: bool
    indentation: float   # x0 distance from left margin
    block_type: str      # 'text' | 'ocr_text'


@dataclass
class DefinitionItem:
    """
    A single defined term extracted from a Definitions section.

    Legal contracts typically have a Definitions clause containing entries like:
        "Confidential Information" means any information disclosed by one party...

    Instead of concatenating all definitions into one massive text blob (which
    would either get truncated at 512 tokens or arbitrarily split mid-definition),
    we parse each definition individually and store it as a structured child
    of the parent DEFINITION_GROUP ClauseChunk.

    This preserves the term↔definition mapping for downstream NER and
    ensures each definition gets its own model pass.

    Attributes:
        term:        The defined term (e.g., "Confidential Information").
        definition:  The definition body (e.g., "means any information disclosed...").
        raw_text:    The full original text of this definition entry.
        token_count: Estimated token count for this entry.
    """
    term: str
    definition: str
    raw_text: str
    token_count: int


@dataclass
class ClauseChunk:
    """
    A fully assembled, segmented clause unit — the primary output of the
    segmentation pipeline.

    Each ClauseChunk represents one logical section of a legal contract,
    ready to be passed to downstream ML models (classifier, NER, obligation
    detection).

    There are three types of chunks:
    - CLAUSE:            A standard legal clause (heading + body text)
    - TABLE:             A detected table, stored as Markdown for structure preservation
    - DEFINITION_GROUP:  A Definitions section with structured DefinitionItem children

    Attributes:
        clause_id:       Unique identifier (UUID string) for this chunk.
        heading:         The heading text (e.g., "3. PAYMENT TERMS"), or None.
        heading_number:  Extracted section number (e.g., "3" or "3.1"), or None.
        body_text:       Full body text of the clause.
        level:           Nesting level: 0=top-level, 1=sub-clause, 2=sub-sub.
        start_page:      Zero-indexed page where this clause begins.
        end_page:        Zero-indexed page where this clause ends.
        token_count:     Estimated token count for the full clause text.
        is_oversized:    True if token_count exceeds the transformer's input limit (480).
        chunk_type:      "CLAUSE" | "TABLE" | "DEFINITION_GROUP"
        table_markdown:  Markdown representation of the table (only for TABLE chunks).
        definitions:     List of DefinitionItem objects (only for DEFINITION_GROUP chunks).
        sub_chunks:      For oversized clauses: list of overlapping text windows
                         that each fit within the 480-token budget.
    """
    clause_id: str
    heading: Optional[str]
    heading_number: Optional[str]
    body_text: str
    level: int
    start_page: int
    end_page: int
    token_count: int
    is_oversized: bool
    chunk_type: str = "CLAUSE"                          # "CLAUSE" | "TABLE" | "DEFINITION_GROUP"
    table_markdown: Optional[str] = None                # Populated for TABLE chunks
    definitions: list[DefinitionItem] = field(default_factory=list)  # Populated for DEFINITION_GROUP
    sub_chunks: list[str] = field(default_factory=list)  # Populated for oversized CLAUSE chunks
