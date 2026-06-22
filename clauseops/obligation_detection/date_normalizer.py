"""
ClauseOps — Date Normalizer

4-Layer date normalization engine:
  Layer 1: Absolute date parsing (dateparser)
  Layer 2: Relative duration resolution (anchor date + timedelta/BDay)
  Layer 3: Recurring date detection (each/every/quarterly patterns)
  Layer 4: Conditional date flagging (upon/if/when patterns)

Handles all 7 edge cases from PIPELINE_OUTPUTS_MIXED.md cross-reference.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

try:
    import dateparser
except ImportError:
    dateparser = None  # type: ignore

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

from dateutil.relativedelta import relativedelta

from clauseops.obligation_detection.number_parser import parse_duration

logger = logging.getLogger(__name__)


# ─── Dataclass ───────────────────────────────────────────────────────────────
@dataclass
class DeadlineRecord:
    """A single normalized deadline from a clause."""
    clause_id: str
    raw_text: str                       # "thirty (30) days"
    date_type: str                      # ABSOLUTE | RELATIVE | RECURRING | CONDITIONAL
    normalized_date: date | None        # 1999-07-21 (or None for conditional/recurring)
    anchor_date: date | None            # The signing date used for resolution
    requires_review: bool               # True for conditional/unresolvable dates
    deadline_label: str                 # "Cure Period", "Payment Due", etc.
    recurrence_description: str | None  # "10th day of each month" (RECURRING only)


# ─── Recurring pattern detection (Edge Case 2) ──────────────────────────────
_RECURRING_PATTERNS = [
    re.compile(r"\beach\s+(?:calendar\s+)?(?:month|quarter|year|week)\b", re.I),
    re.compile(r"\bevery\s+(?:calendar\s+)?(?:month|quarter|year|week)\b", re.I),
    re.compile(r"\bquarterly\b", re.I),
    re.compile(r"\bmonthly\b", re.I),
    re.compile(r"\bannually\b", re.I),
    re.compile(r"\bweekly\b", re.I),
    re.compile(r"\bsemi-?annually\b", re.I),
    re.compile(r"\bthe\s+\d+(?:st|nd|rd|th)\s+day\s+of\s+each\b", re.I),
    re.compile(r"\bfiscal\s+year\b", re.I),
    re.compile(r"\beach\s+fiscal\b", re.I),
    re.compile(r"\beach\s+subsequent\s+quarter\b", re.I),
]

# ─── Conditional pattern detection ──────────────────────────────────────────
_CONDITIONAL_DATE_PATTERNS = [
    re.compile(r"\bupon\s+(?:the\s+)?(?:occurrence|completion|termination|expiration|receipt|delivery)\b", re.I),
    re.compile(r"\bwhen\s+(?:the|such|any)\b", re.I),
    re.compile(r"\bupon\s+(?:written\s+)?(?:notice|demand|request)\b", re.I),
    re.compile(r"\bthe\s+date\s+(?:on\s+which|upon\s+which|when)\b", re.I),
    re.compile(r"\bthe\s+(?:closing|effective)\s+date\b", re.I),
    re.compile(r"\bthe\s+day\s+and\s+year\s+first\b", re.I),
    re.compile(r"\bthe\s+date\s+first\s+above\b", re.I),
]

# ─── Deadline label inference ────────────────────────────────────────────────
_LABEL_PATTERNS = {
    "Cure Period": re.compile(r"\bcure\b|\bremedy\b|\bcorrect\b|\bfail\b|\bbreach\b", re.I),
    "Payment Due": re.compile(r"\bpay\b|\bpayment\b|\bremit\b|\bremunerat\b|\binvoice\b", re.I),
    "Notice Period": re.compile(r"\bnotice\b|\bnotify\b|\bwritten\s+notice\b", re.I),
    "Termination Window": re.compile(r"\btermina\b|\bcancel\b|\bexpir\b", re.I),
    "Renewal Deadline": re.compile(r"\brenewal\b|\brenew\b|\bauto-?renew\b|\bextend\b", re.I),
    "Delivery Due": re.compile(r"\bdeliver\b|\bprovide\b|\bsubmit\b|\bfurnish\b", re.I),
    "Report Due": re.compile(r"\breport\b|\bstatement\b|\baudit\b|\bfinancial\b", re.I),
    "Confidentiality Period": re.compile(r"\bconfidential\b|\bnon-?disclosure\b|\bsecre\b", re.I),
    "Record Retention": re.compile(r"\bretain\b|\bpreserv\b|\bmaintain\s+record\b|\bkeep\s+record\b", re.I),
    "Exclusivity Period": re.compile(r"\bexclusive\b|\bexclusivity\b|\bnon-?compete\b", re.I),
    "Dispute Resolution": re.compile(r"\bdispute\b|\barbitrat\b|\bmedia\b", re.I),
}


def _infer_deadline_label(clause_text: str, clause_type: str) -> str:
    """Infer a human-readable label for the deadline based on context."""
    for label, pattern in _LABEL_PATTERNS.items():
        if pattern.search(clause_text):
            return label
    # Fallback to clause type
    type_map = {
        "PAYMENT": "Payment Due",
        "TERMINATION": "Termination Window",
        "RENEWAL": "Renewal Deadline",
        "DELIVERY_OBLIGATIONS": "Delivery Due",
        "REPORTING_AUDIT": "Report Due",
        "CONFIDENTIALITY": "Confidentiality Period",
        "INDEMNIFICATION": "Indemnification",
        "DISPUTE_RESOLUTION": "Dispute Resolution",
    }
    return type_map.get(clause_type.upper(), "Contractual Deadline")


# ─── Layer 1: Absolute Date Parsing ─────────────────────────────────────────
def _is_recurring(text: str) -> bool:
    """Check if a DATE entity text is a recurring schedule."""
    for pat in _RECURRING_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_conditional_date(text: str) -> bool:
    """Check if a date text is conditional (references unknown events)."""
    for pat in _CONDITIONAL_DATE_PATTERNS:
        if pat.search(text):
            return True
    return False


def _parse_absolute_date(text: str) -> date | None:
    """
    Attempt to parse a DATE entity text into a calendar date.

    Uses dateparser which handles 200+ formats:
      "June 21, 1999" → date(1999, 6, 21)
      "this 14th day of November 2017" → date(2017, 11, 14)
      "Feb 10, 2014" → date(2014, 2, 10)
      "December 19, 2019" → date(2019, 12, 19)

    Returns None for unparseable strings.
    """
    if not dateparser:
        logger.warning("dateparser not installed — cannot parse absolute dates")
        return None

    if not text or len(text.strip()) < 4:
        return None

    # Skip recurring dates — they should NOT be parsed as absolute
    if _is_recurring(text):
        return None

    # Skip conditional dates
    if _is_conditional_date(text):
        return None

    try:
        result = dateparser.parse(
            text,
            settings={
                "STRICT_PARSING": False,
                "PREFER_DATES_FROM": "past",
                "REQUIRE_PARTS": ["year"],  # Must have a year to be absolute
            },
        )
        if result:
            return result.date()
    except Exception:
        pass

    return None


# ─── Layer 2: Relative Duration Resolution ──────────────────────────────────
def _resolve_relative_duration(
    duration_text: str,
    anchor: date,
) -> date | None:
    """
    Resolve a DURATION entity relative to an anchor date.

    "thirty (30) days" + anchor 1999-06-21 → 1999-07-21
    "five (25) business days" + anchor → anchor + 25 BDay
    "three (3) months" + anchor → anchor + 3 months
    """
    num, unit = parse_duration(duration_text)
    if num is None:
        return None

    try:
        if unit == "business_days":
            if pd is not None:
                result = pd.Timestamp(anchor) + pd.offsets.BDay(num)
                return result.date()
            else:
                # Fallback: approximate (multiply by 7/5 to account for weekends)
                approx_cal_days = int(num * 7 / 5)
                return anchor + timedelta(days=approx_cal_days)

        elif unit == "calendar_days" or unit == "days":
            return anchor + timedelta(days=num)

        elif unit == "weeks":
            return anchor + timedelta(weeks=num)

        elif unit == "months":
            return (datetime.combine(anchor, datetime.min.time())
                    + relativedelta(months=num)).date()

        elif unit == "years":
            return (datetime.combine(anchor, datetime.min.time())
                    + relativedelta(years=num)).date()

    except Exception as e:
        logger.warning("Failed to resolve duration '%s': %s", duration_text, e)

    return None


# ─── Anchor Date Extraction (Edge Case 5) ───────────────────────────────────
def extract_anchor_date(clauses_data: list[dict], max_segments: int = 5) -> date | None:
    """
    Find the contract's effective/signing date from the first N segments.

    Scans segments 1–5 (not just segment 1) because NOVO's anchor date
    is in Segment 4 (Definition Group).

    Parameters
    ----------
    clauses_data : list[dict]
        Each dict must have 'entity_summary': {DATE: [...], ...}
    max_segments : int
        How many segments to scan (default 5).

    Returns
    -------
    date | None
        The first parseable absolute date, or None.
    """
    for i, cd in enumerate(clauses_data[:max_segments]):
        dates = cd.get("entity_summary", {}).get("DATE", [])
        if isinstance(dates, str):
            dates = [dates]

        for date_text in dates:
            if isinstance(date_text, list):
                for dt in date_text:
                    parsed = _parse_absolute_date(str(dt))
                    if parsed:
                        logger.info("Anchor date found in segment %d: %s → %s", i + 1, dt, parsed)
                        return parsed
            else:
                parsed = _parse_absolute_date(str(date_text))
                if parsed:
                    logger.info("Anchor date found in segment %d: %s → %s", i + 1, date_text, parsed)
                    return parsed

    logger.warning("No anchor date found in first %d segments", max_segments)
    return None


# ─── Main Normalization Function ─────────────────────────────────────────────
def normalize_dates_for_clause(
    clause_id: str,
    entities: list[dict],
    body_text: str,
    anchor_date: date | None,
    clause_type: str = "",
) -> list[DeadlineRecord]:
    """
    Normalize all DATE and DURATION entities in a single clause.

    Returns one DeadlineRecord per temporal entity.

    Applies the 4-layer architecture:
      Layer 1: Absolute DATE → calendar date
      Layer 2: DURATION → anchor + offset
      Layer 3: Recurring DATE → flagged with description
      Layer 4: Conditional → flagged for review
    """
    records: list[DeadlineRecord] = []

    for ent in entities:
        label = ent.get("label", "")
        text = ent.get("text", "")

        if not text:
            continue

        if label == "DATE":
            records.extend(
                _process_date_entity(clause_id, text, body_text, anchor_date, clause_type)
            )
        elif label == "DURATION":
            records.extend(
                _process_duration_entity(clause_id, text, body_text, anchor_date, clause_type)
            )

    return records


def _process_date_entity(
    clause_id: str,
    date_text: str,
    body_text: str,
    anchor_date: date | None,
    clause_type: str,
) -> list[DeadlineRecord]:
    """Process a single DATE entity through the 4-layer pipeline."""
    label = _infer_deadline_label(body_text, clause_type)

    # Layer 3: Recurring?
    if _is_recurring(date_text):
        return [DeadlineRecord(
            clause_id=clause_id,
            raw_text=date_text,
            date_type="RECURRING",
            normalized_date=None,
            anchor_date=anchor_date,
            requires_review=False,
            deadline_label=label,
            recurrence_description=date_text,
        )]

    # Layer 4: Conditional?
    if _is_conditional_date(date_text):
        return [DeadlineRecord(
            clause_id=clause_id,
            raw_text=date_text,
            date_type="CONDITIONAL",
            normalized_date=None,
            anchor_date=anchor_date,
            requires_review=True,
            deadline_label=label,
            recurrence_description=None,
        )]

    # Layer 1: Absolute date
    parsed = _parse_absolute_date(date_text)
    if parsed:
        return [DeadlineRecord(
            clause_id=clause_id,
            raw_text=date_text,
            date_type="ABSOLUTE",
            normalized_date=parsed,
            anchor_date=anchor_date,
            requires_review=False,
            deadline_label=label,
            recurrence_description=None,
        )]

    # Unparseable — flag for review
    return [DeadlineRecord(
        clause_id=clause_id,
        raw_text=date_text,
        date_type="CONDITIONAL",
        normalized_date=None,
        anchor_date=anchor_date,
        requires_review=True,
        deadline_label=label,
        recurrence_description=None,
    )]


def _is_event_relative_duration(duration_text: str, body_text: str) -> bool:
    """
    Fix 4: Check if a duration is relative to a future/unknown event
    rather than the contract signing date.

    Examples of event-relative durations:
      - "within thirty (30) days of written notice" → event = notice
      - "within six (6) weeks of termination" → event = termination
      - "within seven (7) days of receipt" → event = receipt
      - "within ten (10) days after the date of such notice" → event = notice

    These should NOT be resolved against the anchor date because the event
    hasn't happened yet. They should be flagged as CONDITIONAL.
    """
    # Find where the duration text appears in the body
    dur_lower = duration_text.lower()
    body_lower = body_text.lower()
    pos = body_lower.find(dur_lower)
    if pos < 0:
        # Try partial match (sometimes entity text is slightly different)
        # Just use the whole body text
        context = body_lower
    else:
        # Get context: 20 chars before + duration + 80 chars after
        start = max(0, pos - 20)
        end = min(len(body_lower), pos + len(dur_lower) + 80)
        context = body_lower[start:end]

    # Event-trigger patterns: phrases that indicate the duration is relative
    # to a future event, not the signing date. M2: the determiner group
    # (?:\w+\s+){0,2} allows "of its receipt", "of the written notice",
    # "of such termination" — earlier patterns missed possessives/adjectives.
    event_triggers = [
        r"of\s+(?:\w+\s+){0,2}(?:notice|demand|request)\b",
        r"of\s+(?:\w+\s+){0,2}(?:termination|expiration|cancellation)\b",
        r"of\s+(?:\w+\s+){0,2}(?:receipt|delivery|completion|acceptance)\b",
        r"of\s+(?:\w+\s+){0,2}(?:breach|default|failure)\b",
        r"after\s+(?:\w+\s+){0,2}(?:notice|demand|request|receipt|delivery)\b",
        r"after\s+having\s+\w+",
        r"after\s+(?:the\s+|any\s+|such\s+)?\w+(?:\s+\w+){0,2}\s+has\s+\w+",
        r"after\s+(?:the\s+)?\w+(?:\s+\w+){0,2}\s+(?:expresses|expressed|occurs|occurred|delivers|delivered|elects|elected)\b",
        r"after\s+(?:the\s+date\s+of\s+)?such\s+notice",
        r"after\s+(?:receiving|receipt\s+of)",
        r"following\s+(?:\w+\s+){0,2}(?:notice|notification|termination|expiration|closing|receipt|delivery)\b",
        r"from\s+(?:the\s+)?(?:date\s+of\s+)?(?:termination|expiration|notice|receipt)\b",
    ]

    for trigger in event_triggers:
        if re.search(trigger, context):
            return True

    # Also check: "within X days" NOT followed by "of the date hereof/Effective Date"
    # Those ARE anchor-relative and should be resolved normally.
    # If we find "of the date hereof" or "of the Effective Date", it's anchor-relative
    anchor_relative_patterns = [
        r"of\s+(?:the\s+)?(?:date\s+hereof|effective\s+date|execution)",
        r"of\s+(?:the\s+)?(?:date\s+first|signing|closing\s+date)",
        r"following\s+(?:the\s+)?(?:effective\s+date|date\s+hereof)",
        r"from\s+(?:the\s+)?(?:effective\s+date|date\s+hereof|execution)",
    ]
    for pattern in anchor_relative_patterns:
        if re.search(pattern, context):
            return False  # Explicitly anchor-relative

    return False


def _process_duration_entity(
    clause_id: str,
    duration_text: str,
    body_text: str,
    anchor_date: date | None,
    clause_type: str,
) -> list[DeadlineRecord]:
    """Process a single DURATION entity through the pipeline."""
    label = _infer_deadline_label(body_text, clause_type)

    # Fix 4: Check if this duration is relative to an event (not the anchor date)
    if _is_event_relative_duration(duration_text, body_text):
        logger.info(
            "Duration '%s' is event-relative — classifying as CONDITIONAL",
            duration_text,
        )
        return [DeadlineRecord(
            clause_id=clause_id,
            raw_text=duration_text,
            date_type="CONDITIONAL",
            normalized_date=None,
            anchor_date=anchor_date,
            requires_review=True,
            deadline_label=label,
            recurrence_description=None,
        )]

    # Layer 2: Resolve relative to anchor
    if anchor_date:
        resolved = _resolve_relative_duration(duration_text, anchor_date)
        if resolved:
            return [DeadlineRecord(
                clause_id=clause_id,
                raw_text=duration_text,
                date_type="RELATIVE",
                normalized_date=resolved,
                anchor_date=anchor_date,
                requires_review=False,
                deadline_label=label,
                recurrence_description=None,
            )]

    # No anchor or resolution failed — flag for review
    return [DeadlineRecord(
        clause_id=clause_id,
        raw_text=duration_text,
        date_type="RELATIVE",
        normalized_date=None,
        anchor_date=anchor_date,
        requires_review=True,
        deadline_label=label,
        recurrence_description=None,
    )]


# ─── Contract-level normalization ────────────────────────────────────────────
def normalize_contract_dates(
    clauses_data: list[dict],
    anchor_date: date | None = None,
) -> list[list[DeadlineRecord]]:
    """
    Normalize dates for an entire contract.

    If anchor_date is not provided, attempts to auto-detect from
    the first 5 segments (Edge Case 5).

    Parameters
    ----------
    clauses_data : list[dict]
        Each dict must have: clause_id, entities, body_text, clause_type

    Returns
    -------
    list[list[DeadlineRecord]]
        One list of deadlines per clause, aligned to input order.
    """
    if anchor_date is None:
        anchor_date = extract_anchor_date(clauses_data)

    return [
        normalize_dates_for_clause(
            clause_id=cd.get("clause_id", f"clause_{i}"),
            entities=cd.get("entities", []),
            body_text=cd.get("body_text", ""),
            anchor_date=anchor_date,
            clause_type=cd.get("clause_type", ""),
        )
        for i, cd in enumerate(clauses_data)
    ]
