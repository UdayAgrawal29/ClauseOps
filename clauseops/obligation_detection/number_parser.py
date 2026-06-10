"""
ClauseOps — Number Parser Utility

Converts written-out numbers and parenthetical-digit patterns from legal
contract DURATION text into Python integers.

Key rule (from Edge Case 1 — DeltaThree "five (25) business days"):
  If a parenthetical digit is present, ALWAYS use it. The parenthetical
  is the legally binding number in contract drafting convention.
"""

from __future__ import annotations

import re

# ─── Written-number lookup ───────────────────────────────────────────────────
_WORD_TO_NUM: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

# ─── Regex patterns ──────────────────────────────────────────────────────────
# Match parenthetical digit: (30), (25), (12), etc.
_PAREN_DIGIT_RE = re.compile(r"\(\s*(\d+)\s*\)")
# Match bare digit at start: "30 days", "60 days"
_BARE_DIGIT_RE = re.compile(r"^\s*(\d[\d,]*)")
# Match hyphenated written: "twenty-five", "thirty-two"
_HYPHENATED_RE = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[\s-]+"
    r"(one|two|three|four|five|six|seven|eight|nine)\b",
    re.IGNORECASE,
)


def parse_number(text: str) -> int | None:
    """
    Extract the numeric value from a DURATION text.

    Priority order (highest → lowest):
      1. Parenthetical digit  — "thirty (30) days"  → 30
      2. Bare leading digit   — "60 days"           → 60
      3. Hyphenated written   — "twenty-five days"   → 25
      4. Single written word  — "twelve months"      → 12

    Returns None if no number can be extracted.

    Examples
    --------
    >>> parse_number("thirty (30) days")
    30
    >>> parse_number("five (25) business days")   # Edge Case 1: parenthetical wins
    25
    >>> parse_number("TWELVE (12) MONTHS")
    12
    >>> parse_number("sixty days")
    60
    >>> parse_number("three months")
    3
    >>> parse_number("twenty-five days")
    25
    >>> parse_number("one (1) year")
    1
    """
    if not text:
        return None

    # 1) Parenthetical digit — ALWAYS wins if present
    paren_match = _PAREN_DIGIT_RE.search(text)
    if paren_match:
        raw = paren_match.group(1).replace(",", "")
        try:
            return int(raw)
        except ValueError:
            pass

    # 2) Bare leading digit — "60 days", "10,000 months" (unlikely but safe)
    bare_match = _BARE_DIGIT_RE.search(text)
    if bare_match:
        raw = bare_match.group(1).replace(",", "")
        try:
            return int(raw)
        except ValueError:
            pass

    # 3) Hyphenated written — "twenty-five", "thirty-two"
    hyph_match = _HYPHENATED_RE.search(text)
    if hyph_match:
        tens_word = hyph_match.group(1).lower()
        ones_word = hyph_match.group(2).lower()
        tens = _WORD_TO_NUM.get(tens_word, 0)
        ones = _WORD_TO_NUM.get(ones_word, 0)
        return tens + ones

    # 4) Single written word — scan for the first known word
    for word in text.lower().split():
        word = word.strip("(),.-")
        if word in _WORD_TO_NUM:
            return _WORD_TO_NUM[word]

    return None


# ─── Unit parsing ────────────────────────────────────────────────────────────
_UNIT_PATTERNS = [
    (re.compile(r"\b(?:business\s+days?|working\s+days?)\b", re.I), "business_days"),
    (re.compile(r"\bcalendar\s+days?\b", re.I), "calendar_days"),
    (re.compile(r"\bdays?\b", re.I), "days"),
    (re.compile(r"\bweeks?\b", re.I), "weeks"),
    (re.compile(r"\bmonths?\b", re.I), "months"),
    (re.compile(r"\byears?\b", re.I), "years"),
]


def parse_unit(text: str) -> str:
    """
    Extract the time unit from a DURATION text.

    Returns one of: 'business_days', 'calendar_days', 'days', 'weeks',
    'months', 'years'. Falls back to 'days' if nothing matches.

    Examples
    --------
    >>> parse_unit("thirty (30) business days")
    'business_days'
    >>> parse_unit("TWELVE (12) MONTHS")
    'months'
    >>> parse_unit("five (5) years")
    'years'
    """
    for pattern, unit in _UNIT_PATTERNS:
        if pattern.search(text):
            return unit
    return "days"  # conservative fallback


def parse_duration(text: str) -> tuple[int | None, str]:
    """
    Parse a full DURATION entity text into (number, unit).

    Examples
    --------
    >>> parse_duration("thirty (30) days")
    (30, 'days')
    >>> parse_duration("five (25) business days")
    (25, 'business_days')
    >>> parse_duration("TWELVE (12) MONTHS")
    (12, 'months')
    >>> parse_duration("three months")
    (3, 'months')
    """
    return parse_number(text), parse_unit(text)
