"""Pure span-offset computation for the grounded source-span viewer (task 8.3).

The signature feature of ClauseOps highlights the *exact* characters in a
clause's source text that produced each task. That is only possible because the
``clauseops`` extractor guarantees every extracted party and action is a
*verbatim substring* of the clause body (the **Grounding Invariant**). This
module turns that guarantee into concrete character offsets.

Design (Requirements 5.1/5.2/5.3, design.md "Grounded Source-Span Viewer"):

* Offsets are computed at *task-creation time* via ``source_text.find(span)``.
* When a span string is empty/``None`` or is **not** found as a substring of
  ``source_text``, the corresponding offset pair is left as ``None`` — we never
  fabricate an offset (Requirement 5.2 only constrains spans that are present).
* When a span *is* present, the computed offsets round-trip exactly
  (``source_text[start:end] == span``) and satisfy the bounds
  ``0 <= start <= end <= len(source_text)`` (Requirements 5.2 / 5.3).
* A repeated substring resolves to its **first** occurrence (``str.find``).

This module is deliberately free of any ``clauseops`` / database / Celery
imports: it operates only on plain strings so it can be unit- and property-
tested in isolation. The :mod:`app.processing.ml` seam (`_compute_span_offsets_seam`)
adapts each ``TaskRecord`` into the string inputs this module consumes, and
task 8.4 persists the resulting offsets onto ``tasks`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["SpanOffsets", "find_span_offsets", "find_span_offsets_near", "compute_span_offsets"]


@dataclass(frozen=True)
class SpanOffsets:
    """Computed character offsets for a single task's grounding spans.

    Every field is ``None`` when its span string was empty/``None`` or was not
    found verbatim in ``source_text``. Otherwise the ``*_start``/``*_end`` pair
    round-trips: ``source_text[start:end]`` equals the corresponding span.

    The ``agent_*`` and ``action_*`` pairs map directly to the ``tasks`` columns
    of the same name. The ``deadline_*`` pair grounds the deadline raw text; the
    MVP ``tasks`` schema has no column for it yet, so it is carried here for the
    viewer/Phase-2 use and documented as such (see module docstring).
    """

    agent_start: Optional[int] = None
    agent_end: Optional[int] = None
    action_start: Optional[int] = None
    action_end: Optional[int] = None
    deadline_start: Optional[int] = None
    deadline_end: Optional[int] = None


def find_span_offsets(
    source_text: Optional[str], span: Optional[str]
) -> Optional[tuple[int, int]]:
    """Locate ``span`` inside ``source_text`` and return ``(start, end)``.

    Returns ``None`` (never a fabricated offset) when:

    * ``source_text`` is empty/``None``, or
    * ``span`` is empty/``None``, or
    * ``span`` is not a substring of ``source_text``.

    When a match is found, the result is the first occurrence (``str.find``
    semantics) and is guaranteed to satisfy both the round-trip property
    (``source_text[start:end] == span``) and the bounds
    ``0 <= start <= end <= len(source_text)``.
    """

    # Empty/None inputs → no offset. Note ``"".find("")`` would return 0, so we
    # must guard the empty-span case explicitly to avoid fabricating a
    # zero-length span at position 0.
    if not source_text or not span:
        return None

    start = source_text.find(span)
    if start < 0:
        return None

    end = start + len(span)

    # Defensive: ``str.find`` guarantees these, but assert the contract so a
    # future change can never silently emit an out-of-bounds or non-grounded
    # offset. If somehow violated, decline to fabricate an offset.
    if not (0 <= start <= end <= len(source_text)):
        return None
    if source_text[start:end] != span:
        return None

    return (start, end)


def find_span_offsets_near(
    source_text: Optional[str], span: Optional[str], anchor: Optional[int]
) -> Optional[tuple[int, int]]:
    """Locate ``span`` in ``source_text``, preferring the occurrence nearest ``anchor``.

    Contracts repeat party names many times within a single clause
    ("...granted to LICENSEE... LICENSEE shall ..."), so a naive first-occurrence
    match attaches the highlight to the wrong instance. When we know where the
    *action* was located (``anchor`` = the action's start offset), the governing
    party is almost always the occurrence immediately preceding the action
    (``<Party> shall <action>``). This picks:

      1. the occurrence whose end is closest to (and at or before) ``anchor``;
      2. else the first occurrence at/after ``anchor``;
      3. else (no anchor / nothing found that way) the first occurrence.

    The returned span still round-trips exactly and satisfies the bounds
    invariant, so the grounding guarantee is preserved — only *which* matching
    occurrence is chosen changes.
    """

    if not source_text or not span:
        return None
    if anchor is None:
        return find_span_offsets(source_text, span)

    best_before: Optional[tuple[int, int]] = None
    first_after: Optional[tuple[int, int]] = None
    idx = source_text.find(span)
    while idx != -1:
        end = idx + len(span)
        if end <= anchor:
            best_before = (idx, end)  # keep advancing → closest-before wins
        elif first_after is None and idx >= anchor:
            first_after = (idx, end)
        idx = source_text.find(span, idx + 1)

    chosen = best_before or first_after
    if chosen is None:
        return find_span_offsets(source_text, span)

    start, end = chosen
    if not (0 <= start <= end <= len(source_text)) or source_text[start:end] != span:
        return None
    return chosen


def compute_span_offsets(
    source_text: Optional[str],
    party: Optional[str],
    action: Optional[str],
    deadline_raw: Optional[str] = None,
) -> SpanOffsets:
    """Compute agent/action/deadline offsets for one task.

    Args:
        source_text: The task's ``source_text`` (the clause body the extractor
            grounded against). Offsets are positions into this string.
        party: The extracted ``obligated_party`` span (or ``None``/empty).
        action: The extracted ``action`` span (or ``None``/empty).
        deadline_raw: The deadline raw-text span (or ``None``/empty).

    Returns:
        A :class:`SpanOffsets`. Each pair is ``None`` when the corresponding
        span is absent or not found; otherwise it round-trips exactly and
        respects the bounds invariant.
    """

    agent = find_span_offsets(source_text, party)
    act = find_span_offsets(source_text, action)
    deadline = find_span_offsets(source_text, deadline_raw)

    # Disambiguate a repeated party by anchoring it to the action it governs:
    # the party that actually performs THIS action is the occurrence next to the
    # action span, not necessarily the first occurrence in the clause.
    if act is not None and party:
        anchored = find_span_offsets_near(source_text, party, act[0])
        if anchored is not None:
            agent = anchored

    return SpanOffsets(
        agent_start=agent[0] if agent else None,
        agent_end=agent[1] if agent else None,
        action_start=act[0] if act else None,
        action_end=act[1] if act else None,
        deadline_start=deadline[0] if deadline else None,
        deadline_end=deadline[1] if deadline else None,
    )
