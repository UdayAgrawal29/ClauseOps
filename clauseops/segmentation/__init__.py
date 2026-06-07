"""
ClauseOps Clause Segmentation Module

Provides `segment_contract()` — the single entry point that converts a raw
PDF contract into structured ClauseChunk objects ready for downstream ML.

Uses IBM's Docling ML models trained on 80K+ annotated document pages.
Handles diverse formats automatically.

Usage:
    from clauseops.segmentation import segment_contract, ClauseChunk

    clauses = segment_contract("path/to/contract.pdf")
    for clause in clauses:
        print(clause.heading, clause.chunk_type, clause.token_count)
"""

import logging

from clauseops.segmentation.models import TextBlock, ClauseChunk, DefinitionItem
from clauseops.segmentation.docling_pipeline import segment_contract_docling

logger = logging.getLogger(__name__)

def segment_contract(pdf_path: str) -> list[ClauseChunk]:
    """Segment a PDF using Docling ML models."""
    return segment_contract_docling(pdf_path)

_BACKEND = "docling"
logger.info("ClauseOps segmentation backend: Docling (ML-based)")

__all__ = [
    "segment_contract",
    "TextBlock",
    "ClauseChunk",
    "DefinitionItem",
]
