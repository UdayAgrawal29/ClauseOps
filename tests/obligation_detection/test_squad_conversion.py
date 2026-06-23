"""
Property-based tests for the SQuAD QA converter (Phase 0).

Covers the conversion-time correctness properties from the QA-extraction plan:

  Property 1 — Grounding / extractiveness:
      every emitted (answerable) answer is an exact substring of the context.
  Property 6 — Idempotence of conversion:
      a grounded annotation round-trips, i.e.
      context[answer_start : answer_start + len(text)] == text.

Plus structural invariants:
  - trim_action always returns a PREFIX of its input (offset-preserving).
  - DECLARATIVE rows always produce no-answer (impossible) examples.
  - locate_span always returns a true slice of the context.

Run:
  venv\\Scripts\\python.exe -m pytest tests/obligation_detection/test_squad_conversion.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# ── Import convert_to_squad.py (lives under scripts/, not an installed pkg) ──
_CONVERTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "obligation_detection" / "convert_to_squad.py"
)
_spec = importlib.util.spec_from_file_location("convert_to_squad", _CONVERTER_PATH)
convert_to_squad = importlib.util.module_from_spec(_spec)
sys.modules["convert_to_squad"] = convert_to_squad
_spec.loader.exec_module(convert_to_squad)

build_qa_examples = convert_to_squad.build_qa_examples
trim_action = convert_to_squad.trim_action
locate_span = convert_to_squad.locate_span
ACTIONABLE = convert_to_squad.ACTIONABLE_MODALITIES


# ── Strategies ──────────────────────────────────────────────────────────────
# Printable text without surrogate/control oddities; keep it legal-clause-ish.
_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=80,
)
_modality = st.sampled_from(list(ACTIONABLE))


def _assert_grounded(example: dict) -> None:
    """An answerable example's answer must be an exact slice of its context."""
    if example["is_impossible"]:
        assert example["answers"]["text"] == []
        assert example["answers"]["answer_start"] == []
        return
    ctx = example["context"]
    text = example["answers"]["text"][0]
    start = example["answers"]["answer_start"][0]
    assert 0 <= start <= len(ctx)
    assert start + len(text) <= len(ctx)
    assert ctx[start:start + len(text)] == text  # Property 1 + 6


# ── trim_action: always a prefix (offset-preserving) ────────────────────────
@settings(max_examples=300)
@given(_text)
def test_trim_action_is_prefix(action):
    trimmed = trim_action(action)
    # rstrip can drop trailing chars, so trimmed must equal a leading slice.
    assert action[:len(trimmed)] == trimmed
    assert len(trimmed) <= len(action)


# ── locate_span: returns a real slice or None ───────────────────────────────
@settings(max_examples=300)
@given(_text, _text)
def test_locate_span_returns_true_slice(context, answer):
    hit = locate_span(context, answer)
    if hit is not None:
        start, text = hit
        assert context[start:start + len(text)] == text


# ── Grounding: synthetic actionable records always round-trip ───────────────
@settings(max_examples=300)
@given(
    prefix=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40),
    agent=_text,
    mid=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40),
    action=_text,
    suffix=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40),
    modality=_modality,
)
def test_actionable_records_are_grounded(prefix, agent, mid, action, suffix, modality):
    # Construct a context that literally contains agent and action verbatim.
    context = f"{prefix}{agent}{mid}{action}{suffix}"
    record = {
        "clause_text": context,
        "modality": modality,
        "agent": agent,
        "action": action,
        "source": "synthetic",
    }
    built = build_qa_examples(record, index=0)
    # May be None only if a span genuinely can't be located (shouldn't happen
    # here since both are substrings), or trim produced an empty action.
    if built is None:
        assert trim_action(action) == "" or locate_span(context, agent) is None
        return
    assert len(built) == 2
    for ex in built:
        _assert_grounded(ex)
        assert not ex["is_impossible"]  # actionable → answerable


# ── DECLARATIVE always abstains (no-answer) ─────────────────────────────────
@settings(max_examples=100)
@given(context=st.text(min_size=1, max_size=200), idx=st.integers(min_value=0, max_value=10_000))
def test_declarative_is_no_answer(context, idx):
    assume(context.strip())  # empty/whitespace clauses are legitimately dropped
    record = {
        "clause_text": context,
        "modality": "DECLARATIVE",
        "agent": None,
        "action": None,
        "source": "synthetic",
    }
    built = build_qa_examples(record, index=idx)
    assert built is not None
    assert len(built) == 2
    for ex in built:
        assert ex["is_impossible"]
        _assert_grounded(ex)


# ── Real-data sanity: every emitted example from the actual corpus grounds ──
def test_real_corpus_grounding():
    raw_path = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "obligation_detection" / "training_data" / "raw_annotations.jsonl"
    )
    if not raw_path.exists():
        pytest.skip("raw_annotations.jsonl not present")

    rows = convert_to_squad.load_raw(raw_path)
    assert rows, "expected non-empty corpus"

    total = 0
    for i, row in enumerate(rows):
        built = build_qa_examples(row, index=i)
        if built is None:
            continue
        for ex in built:
            _assert_grounded(ex)
            total += 1
    assert total > 0
