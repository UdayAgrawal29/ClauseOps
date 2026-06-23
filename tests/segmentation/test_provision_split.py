"""
Property + unit tests for the Phase-A provision splitter in docling_pipeline.

Properties:
  - Coverage: splitting loses no body text (children concatenate back to body).
  - Monotonic granularity: result count >= 1, never merges away content.
  - No over-split: cross-references ("pursuant to Section 4.01", "Sections 3.06
    and 4.02 hereof") and single-marker bodies are NOT split.
  - Determinism.

These exercise the pure functions only (no Docling / no PDF needed).

Run:
  venv\\Scripts\\python.exe -m pytest tests/segmentation/test_provision_split.py -q
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from clauseops.segmentation.docling_pipeline import (
    _split_clause_into_provisions,
    _is_genuine_provision_start,
    _PROVISION_MARKER_RE,
    _carve_provision_heading,
)
from clauseops.segmentation.models import ClauseChunk


def _mk(body: str, heading: str = "ARTICLE III") -> ClauseChunk:
    return ClauseChunk(
        clause_id="t", heading=heading, heading_number=None, body_text=body,
        level=0, start_page=0, end_page=0, token_count=len(body.split()),
        is_oversized=False, chunk_type="CLAUSE",
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ── Real-world style merged article (DeltaThree shape) splits into provisions ──
def test_splits_merged_article():
    body = (
        "Services Provided by PrimeCall "
        "Section 3.01 [Printing of Calling Cards] . Printing of Calling Cards. "
        "PrimeCall shall negotiate and contract on behalf of DeltaThree for the printing "
        "of the Calling Cards. DeltaThree shall reimburse PrimeCall for all costs. "
        "Section 3.02 [Toll-Free Access Number] . Toll-Free Access Number. "
        "PrimeCall shall procure on behalf of DeltaThree a unique toll-free 800 access number. "
        "Section 3.03 [Pricing and Marketing] . Pricing and Marketing. "
        "PrimeCall shall provide DeltaThree with pricing and marketing services."
    )
    out = _split_clause_into_provisions(_mk(body))
    assert len(out) >= 3
    # Coverage: concatenated child bodies contain all section texts.
    joined = _norm(" ".join(c.body_text for c in out))
    for needle in ["Printing of Calling Cards", "Toll-Free Access Number",
                   "Pricing and Marketing", "reimburse PrimeCall"]:
        assert needle in joined


# ── Cross-references must NOT trigger splits ──────────────────────────────────
def test_cross_references_not_split():
    body = (
        "The parties shall negotiate in good faith pursuant to this Section 4.01 hereof "
        "and in accordance with Sections 3.06 and 4.02 hereof, provided that nothing in "
        "Section 9.03 above shall limit the remedies available under Section 7.2 of this Agreement."
    )
    out = _split_clause_into_provisions(_mk(body, heading="Dispute"))
    assert len(out) == 1  # all are citations, no genuine starts


# ── Single genuine marker → no split (need >= 2) ─────────────────────────────
def test_single_marker_not_split():
    body = (
        "Section 5.01. Payment Terms. Any amounts due hereunder shall be calculated and "
        "paid in U.S. dollars on a monthly basis within twenty-five (25) business days "
        "following the receipt of the reports. All payments shall be made via wire transfer."
    )
    out = _split_clause_into_provisions(_mk(body, heading="Payments"))
    assert len(out) == 1


def test_carve_heading_bracket_and_plain():
    assert _carve_provision_heading(
        "Section 3.01 [Printing of Calling Cards] . Printing. Body here."
    ) == "Section 3.01 — Printing of Calling Cards"
    h = _carve_provision_heading("Section 5.01. Payment Terms. Any amounts due...")
    assert h.startswith("Section 5.01")


# ── Coverage property: no body text dropped, regardless of input ─────────────
_seg = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=0, max_size=60)


@settings(max_examples=200)
@given(
    a=_seg, b=_seg, c=_seg,
    n1=st.integers(min_value=1, max_value=9),
    n2=st.integers(min_value=1, max_value=9),
)
def test_coverage_no_text_lost(a, b, c, n1, n2):
    # Construct a body with two genuine Section markers separated by content.
    body = (f"Intro clause text here padding padding. "
            f"Section {n1}.01 Title One. {a} The party shall do {b}. "
            f"Section {n2}.02 Title Two. {c} The party shall also act.")
    chunk = _mk(body)
    out = _split_clause_into_provisions(chunk)
    # Every alphabetic run from the body must survive in some child body.
    joined = _norm(" ".join(x.body_text for x in out))
    for token in re.findall(r"[A-Za-z]{3,}", body):
        assert token in joined


@settings(max_examples=100)
@given(a=_seg, b=_seg)
def test_determinism(a, b):
    body = (f"Section 2.01 Alpha. {a} The party shall alpha. "
            f"Section 2.02 Beta. {b} The party shall beta.")
    c1 = [x.body_text for x in _split_clause_into_provisions(_mk(body))]
    c2 = [x.body_text for x in _split_clause_into_provisions(_mk(body))]
    assert c1 == c2


# ── Non-CLAUSE chunks pass through untouched ─────────────────────────────────
def test_non_clause_passthrough():
    t = ClauseChunk(clause_id="x", heading="[TABLE]", heading_number=None,
                    body_text="Section 1.01 a | Section 1.02 b", level=0,
                    start_page=0, end_page=0, token_count=5, is_oversized=False,
                    chunk_type="TABLE")
    out = _split_clause_into_provisions(t)
    assert len(out) == 1 and out[0] is t
