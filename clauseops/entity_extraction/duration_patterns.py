"""
ClauseOps - Duration Pattern Extraction

Regex-based duration detection used to supplement spaCy NER.
Focuses on relative and explicit time spans (e.g., "30 days", "two years").
"""

from __future__ import annotations

import re
# Time units (singular/plural + common legal modifiers)
_UNIT_RE = (
    r"(?:"
    r"business\s+days?|working\s+days?|calendar\s+days?|"
    r"days?|weeks?|months?|years?"
    r")"
)

# Numeric values (supports comma-separated and decimals)
_NUM_RE = r"\b\d+(?:,\d{2,3})*(?:\.\d+)?\b"

# Written numbers (partial but covers common contract durations)
_WRITTEN_NUMBERS = [
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "ninety",
    "hundred",
]
_WRITTEN_RE = r"\b(?:" + "|".join(_WRITTEN_NUMBERS) + r")\b"

# Compiled patterns
_DURATION_PATTERNS = [
    re.compile(rf"{_NUM_RE}\s+{_UNIT_RE}", re.IGNORECASE),
    re.compile(rf"{_WRITTEN_RE}(?:\s*\(\d+\))?\s+{_UNIT_RE}", re.IGNORECASE),
    re.compile(rf"{_NUM_RE}\s*-\s*year\b", re.IGNORECASE),
    re.compile(rf"{_WRITTEN_RE}\s*-\s*year\b", re.IGNORECASE),
]


def _dedupe_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Keep the longest non-overlapping spans."""
    if not spans:
        return []

    spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int]] = []
    for start, end in spans:
        if not kept:
            kept.append((start, end))
            continue
        last_start, last_end = kept[-1]
        if start >= last_end:
            kept.append((start, end))
            continue
        # Overlap: keep the longer span
        if (end - start) > (last_end - last_start):
            kept[-1] = (start, end)
    return kept


def find_durations(text: str) -> list[dict]:
    """
    Find duration spans in text using regex patterns.

    Returns list of dicts: {"text", "start", "end", "label", "source"}
    """
    spans: list[tuple[int, int]] = []
    for pattern in _DURATION_PATTERNS:
        for match in pattern.finditer(text):
            spans.append(match.span())

    spans = _dedupe_spans(spans)
    results: list[dict] = []
    for start, end in spans:
        results.append({
            "text": text[start:end],
            "start": start,
            "end": end,
            "label": "DURATION",
            "source": "rule",
        })
    return results
