"""
ClauseOps - Alias Extraction and Resolution

Extracts alias mappings from contract preambles/definitions and resolves
alias references in entity output.

2026 Approach: Uses NER-based antecedent validation with stem-based concept
filtering and inter-clause boundary detection to distinguish party aliases
from defined contract terms — without hardcoded stopword lists.
"""

from __future__ import annotations

import re

# Quote characters (ASCII + Unicode escapes)
_QUOTE_RE = r"[\"\u201c\u201d\u2018\u2019']"

# Common party role words used as aliases
_PARTY_ROLE_WORDS = {
    "licensor", "licensee", "vendor", "buyer", "seller", "franchisor",
    "franchisee", "employer", "employee", "consultant", "client",
    "service provider", "customer", "contractor", "developer",
    "company", "counterparty", "distributor", "agent", "borrower", "lender",
}

# Universal contract stopwords — terms that are NEVER party aliases
# in any jurisdiction or contract type.
_ALIAS_STOPWORDS = {
    "agreement", "document", "schedule", "exhibit", "annex", "appendix",
    "section", "article", "clause", "party", "parties", "effective date",
    "term", "initial term", "renewal term",
}

# Concept word ROOTS (stems). We match against stemmed alias words so that
# "products" matches "product", "services" matches "service", etc.
# This eliminates the need to enumerate every plural/variant form.
_CONCEPT_ROOTS = {
    "period", "date", "revenue", "fee",
    "content", "program", "product", "technology",
    "mark", "work", "material",
    "payment", "rate", "amount", "cost", "price",
    "territory", "placement",
    "service", "media", "software", "platform",
    "communication", "session",
    "attribute", "website",
    "deliverable", "output",
    "indemnity", "warranty",
    "obligation", "property",
}

# Company suffixes — if an alias ends with one of these, it's always
# a real company name (e.g., "Building Products Inc.", "Service Corp")
_COMPANY_SUFFIXES = {"inc", "corp", "llc", "ltd", "gmbh", "ag", "plc", "co"}


def _stem_word(word: str) -> str:
    """
    Minimal Porter-style suffix strip for concept-word matching.
    Only handles the most common English suffixes in legal text.
    No external dependencies needed.

    Examples:
        products -> product, services -> service, technologies -> technology,
        communications -> communication, warranties -> warranty,
        materials -> material, obligations -> obligation
    """
    w = word.lower().rstrip(".")

    # -ies → -y (warranties → warranty, technologies → technology)
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    # -es → remove (services → service, prices → price)
    if w.endswith("es") and len(w) > 3:
        candidate = w[:-2]
        # But not "states" → "stat" — check if removing -es gives a concept root
        if candidate in _CONCEPT_ROOTS or w[:-1] in _CONCEPT_ROOTS:
            return candidate if candidate in _CONCEPT_ROOTS else w[:-1]
        return w[:-2]
    # -s → remove (products → product, marks → mark)
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        return w[:-1]
    return w


def _has_concept_root(alias: str) -> bool:
    """
    Check if any word in the alias, after stemming, matches a concept root.
    This is the generalized replacement for exact _CONCEPT_WORDS matching.
    """
    for word in alias.lower().split():
        stem = _stem_word(word)
        if stem in _CONCEPT_ROOTS:
            return True
    return False


def _has_company_suffix(alias: str) -> bool:
    """
    Check if alias ends with a company suffix (Inc., Corp., LLC, etc.).
    If so, it's always a real company name regardless of concept words.
    """
    last_word = alias.strip().rstrip(".").split()[-1].lower()
    return last_word in _COMPANY_SUFFIXES


# Alias trigger: finds the alias inside parens/quotes — e.g., (the "NVOS")
_ALIAS_TRIGGER_PAT = re.compile(
    rf"\(\s*(?:herein(?:after)?\s+)?(?:referred\s+to\s+as\s+)?(?:meaning\s+)?(?:the\s+)?{_QUOTE_RE}(?P<alias>[^\"\u201c\u201d\u2018\u2019']{{2,50}}){_QUOTE_RE}\s*\)",
    re.IGNORECASE,
)
# Comma-based trigger: , hereinafter "Alias"
_ALIAS_TRIGGER_COMMA = re.compile(
    rf",\s*(?:herein(?:after)?\s+)(?:referred\s+to\s+as\s+)?(?:the\s+)?{_QUOTE_RE}(?P<alias>[^\"\u201c\u201d\u2018\u2019']{{2,50}}){_QUOTE_RE}",
    re.IGNORECASE,
)

# Pattern to find ALL alias triggers in the text (for boundary detection)
_ANY_ALIAS_TRIGGER = re.compile(
    rf"\(\s*(?:herein(?:after)?\s+)?(?:referred\s+to\s+as\s+)?(?:meaning\s+)?(?:the\s+)?{_QUOTE_RE}[^\"\u201c\u201d\u2018\u2019']{{2,50}}{_QUOTE_RE}\s*\)",
    re.IGNORECASE,
)


def _looks_like_party_alias(alias: str) -> bool:
    """
    Determine if an alias string looks like a party name vs a defined term.

    Accepts:
    - All-caps acronyms (ESSI, NVOS, HGF) — always party abbreviations
    - Known role words (Licensor, Licensee, Contractor)
    - Aliases with company suffixes (Building Products Inc.)
    - Title Case multi-word phrases ONLY if they don't contain concept roots

    Rejects:
    - Universal stopwords (agreement, term, effective date)
    - Anything containing concept word roots (product, service, technology, etc.)
    - Single common English words (Term, Grant, Retail)
    """
    alias_clean = alias.strip().strip(".:")
    alias_lower = alias_clean.lower()

    # Hard reject: universal stopwords
    if alias_lower in _ALIAS_STOPWORDS:
        return False

    # Hard accept: known party role words
    if alias_lower in _PARTY_ROLE_WORDS:
        return True

    # Hard accept: all-caps acronyms (2-12 chars) — ESSI, NVOS, HGF, WPT, MA
    if alias_clean.isupper() and 2 <= len(alias_clean) <= 12:
        return True

    # Hard accept: company suffix bypass — "Service Corp International" is a
    # real company even though "service" is a concept word
    if _has_company_suffix(alias_clean):
        return True

    # Stem-based concept word check — catches "The Products", "Technology",
    # "Licensed Content", "Viewing Period" etc. without exact enumeration
    if _has_concept_root(alias_clean):
        return False

    # Title Case multi-word — accept if no concept roots were found above
    # This lets through "Harvest Gold", "Novo Healthnet", "Joint Venture"
    if " " in alias_clean and alias_clean == alias_clean.title():
        return True

    # Single word, mixed case, 3-20 chars — only accept if it starts with
    # uppercase (looks like a proper noun: "Rogers", "Chase")
    if 3 <= len(alias_clean) <= 20 and alias_clean[0].isupper() and " " not in alias_clean:
        # Reject common English words that happen to be capitalized
        _COMMON_LEGAL_NOUNS = {
            "term", "grant", "retail", "basic", "master", "notice",
            "shares", "stock", "equity", "option", "schedule",
            "exhibit", "annex", "appendix", "letter", "order",
        }
        if alias_lower in _COMMON_LEGAL_NOUNS:
            return False
        return True

    return False


def _is_valid_antecedent(ent_text: str) -> bool:
    """
    Validate that a candidate antecedent entity is a real organization/person
    name, not a fragment like "Communications" or "Holdings".

    A valid antecedent must be either:
    - Multi-token (at least 2 words): "Rogers Cable Communications Inc."
    - All-caps single token: "NVOS", "HGF"
    - At least 5 characters long
    """
    text = ent_text.strip()
    if len(text) < 5:
        return False

    words = text.split()
    if len(words) >= 2:
        return True
    # Single word: only valid if all-caps (acronym)
    if text.isupper() and len(text) >= 2:
        return True

    return False


def _has_intervening_trigger(text: str, antecedent_end: int, trigger_start: int) -> bool:
    """
    Check if there's another alias trigger ('...') between the candidate
    antecedent's end position and the current trigger's start position.

    If there IS an intervening trigger, the antecedent belongs to a different
    party/definition clause and should be rejected.

    Example:
        "MOUNT KNOWLEDGE HOLDINGS INC. ... ('MA') ... products ... ('Technology')"
        ^--- antecedent                    ^--- intervening     ^--- current trigger
        The ('MA') trigger sits between MOUNT KNOWLEDGE and ('Technology'),
        so MOUNT KNOWLEDGE is NOT the antecedent for Technology.
    """
    between = text[antecedent_end:trigger_start]
    return bool(_ANY_ALIAS_TRIGGER.search(between))


def extract_alias_map(text: str, nlp, max_chars: int = None) -> dict[str, str]:
    """
    Extract alias -> full name mappings from contract text using
    NER-based antecedent validation with proximity constraints and
    inter-clause boundary detection.

    Strategy:
    1. Find alias triggers (quoted terms in parens).
    2. For each trigger, find the nearest ORG/PERSON entity
       within 200 characters BEFORE the trigger.
    3. Validate that the antecedent is a real entity (multi-token or all-caps).
    4. Verify no other alias trigger sits between the antecedent and this one.
    5. If no valid antecedent exists, reject the alias (it's a defined term).
    """
    window = text[:max_chars] if max_chars else text
    alias_map: dict[str, str] = {}

    doc = nlp(window)

    for pattern in [_ALIAS_TRIGGER_PAT, _ALIAS_TRIGGER_COMMA]:
        for match in pattern.finditer(window):
            alias = match.group("alias").strip()
            trigger_start = match.start()

            if not alias or not _looks_like_party_alias(alias):
                continue

            # Proximity-constrained antecedent search:
            # Only consider ORG/PERSON entities that END within 200 chars
            # before the trigger start.
            best_ent = None
            best_end = -1
            for ent in doc.ents:
                if ent.label_ not in {"ORG", "PERSON"}:
                    continue
                ent_text = ent.text.strip()
                # Must end before the trigger
                if ent.end_char > trigger_start:
                    continue
                # Must be within 200 chars (same sentence, roughly)
                distance = trigger_start - ent.end_char
                if distance > 200:
                    continue
                # Must be a valid antecedent (not a fragment)
                if not _is_valid_antecedent(ent_text):
                    continue
                # Inter-clause boundary check: reject if another alias trigger
                # sits between this antecedent and our current trigger
                if _has_intervening_trigger(window, ent.end_char, trigger_start):
                    continue
                # Take the closest valid entity (highest end_char)
                if ent.end_char > best_end:
                    best_end = ent.end_char
                    best_ent = ent_text

            if best_ent:
                alias_map[alias] = best_ent

    return alias_map


def extract_alias_map_from_clauses(clauses, nlp) -> dict[str, str]:
    """
    Build alias map from ClauseChunk list across the entire document
    to support super long-distance coreference.
    """
    parts: list[str] = []

    for clause in clauses:
        if clause.body_text:
            parts.append(clause.body_text)
        if clause.chunk_type == "DEFINITION_GROUP":
            for item in clause.definitions:
                if item and item.raw_text:
                    parts.append(item.raw_text)

    text = "\n".join(parts)
    return extract_alias_map(text, nlp, max_chars=None)


def resolve_aliases(entities: list[dict], alias_map: dict[str, str]) -> list[dict]:
    """
    Add resolved_name and is_alias flags to PARTY/ORG entities.
    """
    if not alias_map:
        return entities

    for ent in entities:
        if ent.get("label") in {"PARTY", "ORG"}:
            text = (ent.get("text") or "").strip()
            if text in alias_map:
                ent["resolved_name"] = alias_map[text]
                ent["is_alias"] = True
            else:
                ent["resolved_name"] = text
                ent["is_alias"] = False
    return entities


def build_dynamic_party_ruler(nlp, alias_map: dict[str, str]):
    """
    Build an EntityRuler with PARTY patterns for known aliases and full names.
    Uses case-insensitive matching (LOWER attribute).
    """
    if not alias_map:
        return None

    from spacy.pipeline import EntityRuler

    ruler = EntityRuler(nlp, overwrite_ents=True, phrase_matcher_attr="LOWER")
    patterns = []
    for alias, full_name in alias_map.items():
        if alias:
            patterns.append({"label": "PARTY", "pattern": alias})
        if full_name:
            patterns.append({"label": "PARTY", "pattern": full_name})
    if patterns:
        ruler.add_patterns(patterns)
        return ruler
    return None
