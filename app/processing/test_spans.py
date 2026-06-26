"""Unit tests for the pure span-offset computation (spec task 8.3).

These exercise :mod:`app.processing.spans` in isolation (plain strings, no
clauseops/DB). They cover the behaviours Requirements 5.1/5.2/5.3 hinge on:

* round-trip equality — ``source_text[start:end] == span`` when a span is found,
* bounds — ``0 <= start <= end <= len(source_text)``,
* not-found / empty / None spans → ``None`` (no fabricated offsets),
* repeated substrings resolve to the first occurrence.

The grounding round-trip (Property 1, task 8.5) and span-offset bounds
(Property 2, task 8.6) property tests live separately.
"""

from __future__ import annotations

from app.processing.spans import (
    SpanOffsets,
    compute_span_offsets,
    find_span_offsets,
)


# ---------------------------------------------------------------------------
# find_span_offsets
# ---------------------------------------------------------------------------


def test_find_span_round_trip_and_bounds():
    """A present span returns offsets that round-trip and stay in bounds."""
    text = "The Licensee shall pay the fee within 30 days."
    offsets = find_span_offsets(text, "Licensee")
    assert offsets is not None
    start, end = offsets
    assert text[start:end] == "Licensee"
    assert 0 <= start <= end <= len(text)


def test_find_span_at_start():
    text = "Buyer shall deliver the goods."
    assert find_span_offsets(text, "Buyer") == (0, 5)


def test_find_span_at_end():
    text = "Payment is due on receipt"
    start, end = find_span_offsets(text, "receipt")
    assert text[start:end] == "receipt"
    assert end == len(text)


def test_find_span_not_a_substring_returns_none():
    text = "The Vendor shall maintain confidentiality."
    assert find_span_offsets(text, "Purchaser") is None


def test_find_span_empty_span_returns_none():
    # "".find("") would be 0; we must NOT fabricate a zero-length span.
    assert find_span_offsets("some clause text", "") is None


def test_find_span_none_span_returns_none():
    assert find_span_offsets("some clause text", None) is None


def test_find_span_empty_source_returns_none():
    assert find_span_offsets("", "anything") is None


def test_find_span_none_source_returns_none():
    assert find_span_offsets(None, "anything") is None


def test_find_span_repeated_substring_picks_first_occurrence():
    text = "pay pay pay"
    start, end = find_span_offsets(text, "pay")
    assert (start, end) == (0, 3)
    assert text[start:end] == "pay"


def test_find_span_full_text_match():
    text = "exact"
    assert find_span_offsets(text, "exact") == (0, len(text))


# ---------------------------------------------------------------------------
# compute_span_offsets
# ---------------------------------------------------------------------------


def test_compute_all_spans_present_round_trip():
    text = "The Supplier shall deliver the report within 14 days of the Effective Date."
    party = "Supplier"
    action = "deliver the report"
    deadline = "within 14 days"

    result = compute_span_offsets(text, party, action, deadline)

    assert isinstance(result, SpanOffsets)
    assert text[result.agent_start:result.agent_end] == party
    assert text[result.action_start:result.action_end] == action
    assert text[result.deadline_start:result.deadline_end] == deadline
    # Bounds hold for every present pair.
    for start, end in (
        (result.agent_start, result.agent_end),
        (result.action_start, result.action_end),
        (result.deadline_start, result.deadline_end),
    ):
        assert 0 <= start <= end <= len(text)


def test_compute_missing_action_leaves_action_none_but_party_present():
    text = "The Tenant shall vacate the premises."
    result = compute_span_offsets(text, "Tenant", "make repairs")
    # Party grounds; action is absent → action offsets None (not fabricated).
    assert text[result.agent_start:result.agent_end] == "Tenant"
    assert result.action_start is None
    assert result.action_end is None
    # No deadline supplied → deadline offsets None.
    assert result.deadline_start is None
    assert result.deadline_end is None


def test_compute_none_party_and_action():
    text = "Some clause text without a clean party span."
    result = compute_span_offsets(text, None, None, None)
    assert result == SpanOffsets()  # all None


def test_compute_party_not_verbatim_due_to_cleaning_returns_none():
    # Mirrors the real pipeline: TaskRecord.obligated_party is cleaned (brackets
    # stripped), so the cleaned form may not be verbatim in source_text.
    text = "Pursuant to clause 3, [Licensor] shall indemnify the Licensee."
    # Cleaned party "Licensor" (without brackets) IS still a substring here.
    result = compute_span_offsets(text, "Licensor", "indemnify the Licensee")
    assert text[result.agent_start:result.agent_end] == "Licensor"
    assert text[result.action_start:result.action_end] == "indemnify the Licensee"


def test_compute_party_absent_after_truncation_returns_none():
    # source_text is truncated in the pipeline; a party beyond the cut is absent.
    text = "Short clause body."
    result = compute_span_offsets(text, "A Party Mentioned Only Later", "do something")
    assert result.agent_start is None and result.agent_end is None
    assert result.action_start is None and result.action_end is None
