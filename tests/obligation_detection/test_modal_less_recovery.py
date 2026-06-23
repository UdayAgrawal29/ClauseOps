"""
Tests for Phase G (modal-less obligation recovery) helpers + Phase D event-relative.

Pure-function tests (no models / no PDF):
  - governing agent extracted from "OBLIGATIONS OF {Party}" headings
  - infinitive / imperative directives recognized; declaratives ignored
  - candidate builder reconstructs "<agent> shall <directive>" for modal-less
    list items, switches agent on bare party sub-headers, and only fires when a
    governing agent exists (precision)
  - event-relative date detection now catches "after having advised" /
    "after the mediator has expressed"

Run:
  venv\\Scripts\\python.exe -m pytest tests/obligation_detection/test_modal_less_recovery.py -q
"""

from __future__ import annotations

import spacy

from clauseops.obligation_detection import deontic_classifier as dc
from clauseops.obligation_detection.date_normalizer import _is_event_relative_duration

_NLP = spacy.load("en_core_web_sm")


# ── Heading agent extraction ─────────────────────────────────────────────────
def test_governing_agent_from_heading():
    assert dc._governing_agent_from_heading("ARTICLE 9 - OBLIGATIONS OF NVOS") == "NVOS"
    assert dc._governing_agent_from_heading("OBLIGATIONS OF THE COMPANY") == "COMPANY"
    assert dc._governing_agent_from_heading("ARTICLE 5 - START UP CAPITAL") is None
    assert dc._governing_agent_from_heading("") is None


# ── Directive recognition ────────────────────────────────────────────────────
def test_as_directive():
    assert dc._as_directive("To maintain all financial records of the Company") == \
        "maintain all financial records of the Company"
    assert dc._as_directive("Assign and direct operational staff") == \
        "assign and direct operational staff"
    assert dc._as_directive("Provide a minimum of seven thousand acres") == \
        "provide a minimum of seven thousand acres"
    # Not a directive (declarative / too short / non-verb lead)
    assert dc._as_directive("The Company is a Nevada corporation") is None
    assert dc._as_directive("This Agreement means the JV Agreement") is None


def test_strip_item_prefix():
    assert dc._strip_item_prefix("9.1 To maintain records") == "To maintain records"
    assert dc._strip_item_prefix("\u25cf Complete the plan") == "Complete the plan"
    assert dc._strip_item_prefix("5.1.1 NVOS") == "NVOS"


def test_bare_party_subheader():
    assert dc._bare_party_subheader("NVOS", ["NVOS", "HGF"]) == "NVOS"
    assert dc._bare_party_subheader("HGF", ["NVOS", "HGF"]) == "HGF"
    assert dc._bare_party_subheader("To maintain records", ["NVOS", "HGF"]) is None


# ── Candidate builder reconstructs modal-less obligations ────────────────────
def test_candidates_reconstruct_obligations_of_heading():
    body = ("9.1 To maintain all financial records of the Company and provide quarterly reporting. "
            "9.3 To remunerate HGF on the basis of thirty percent of net income.")
    doc = _NLP(body)
    cands = dc._build_obligation_candidates(doc, "ARTICLE 9 - OBLIGATIONS OF NVOS", ["NVOS", "HGF"])
    recon = [c[0] for c in cands]
    assert any(t.startswith("NVOS shall maintain all financial records") for t in recon)
    assert any("NVOS shall remunerate HGF" in t for t in recon)
    # party_hint carried
    assert all(c[1] == "NVOS" for c in cands)


def test_candidates_no_agent_no_reconstruction():
    # Without a governing agent, modal-less lines must NOT be reconstructed
    # (precision: avoid false-positive obligations).
    body = ("To maintain all financial records. To remunerate the consultant fairly.")
    doc = _NLP(body)
    cands = dc._build_obligation_candidates(doc, "ARTICLE 13 - CURRENCY", ["NVOS", "HGF"])
    assert cands == []


def test_candidates_modal_sentences_passthrough():
    body = "The Company shall prepare quarterly statements for review by the Parties."
    doc = _NLP(body)
    cands = dc._build_obligation_candidates(doc, "ARTICLE 15 - FINANCIAL STATEMENTS", ["NVOS", "HGF"])
    assert len(cands) == 1
    assert cands[0][1] is None  # no reconstruction; party_hint None
    assert "shall prepare quarterly statements" in cands[0][0]


def test_candidates_subheader_switches_agent():
    body = ("Each of the Parties shall contribute to the start-up as follows: as set out below. "
            "Make arrangements for construction and financing options. "
            "Arrange for product purchase contracts.")
    doc = _NLP(body)
    cands = dc._build_obligation_candidates(doc, "ARTICLE 5 - START UP CAPITAL", ["NVOS", "HGF"])
    recon = [c[0] for c in cands if c[1]]
    # The "as follows:" lead-in propagates its subject to the modal-less bullets.
    assert any("shall make arrangements" in t for t in recon)
    assert any("shall arrange for product purchase" in t for t in recon)


# ── Phase D: event-relative detection ────────────────────────────────────────
def test_event_relative_after_having_advised():
    body = ("Not earlier than ten (10) working days after having advised the other Party, "
            "an aggrieved Party may require mediation.")
    assert _is_event_relative_duration("ten (10) working days", body) is True


def test_event_relative_after_mediator_expressed():
    body = ("shall notify the other Party, in writing, thereof, not later than thirty (30) "
            "calendar days after the mediator has expressed his opinion.")
    assert _is_event_relative_duration("thirty (30) calendar days", body) is True


def test_anchor_relative_still_resolves():
    body = "DeltaThree shall establish a web site within three (3) months of the date hereof."
    assert _is_event_relative_duration("three (3) months", body) is False
