"""
ClauseOps — Deontic Obligation Classifier (v3)

Rule-based engine that classifies each clause's obligation modality using
deontic logic (shall/must/may/will modal verb detection).

v3 changes (quality overhaul):
  - Fix A: Semantic "will" filtering — rejects passive/declarative uses of "will"
    (e.g., "The purpose will be IT Development" is descriptive, not obligatory).
    Uses spaCy dependency parsing to check for passive voice and inanimate subjects.
  - Fix B: Passive voice agent extraction — when passive voice detected,
    looks for "by + AGENT" to find the real obligated party.
  - Fix C: Expanded stative verb blocklist (+30 non-action verbs).
  - Fix D: Expanded non-entity party blocklist (contract defined terms).
  - (v2) Fix 2: Dependency-tree verb extraction via spaCy.
  - (v2) Fix 3: NER-validated party names.
  - Modal detection works PER-SENTENCE (caller passes single sentences).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ─── Lazy spaCy loader ──────────────────────────────────────────────────────
_nlp = None

def _get_nlp():
    """Lazy-load spaCy model (cached after first call)."""
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


# ─── Dataclass ───────────────────────────────────────────────────────────────
@dataclass
class ObligationRecord:
    """A single detected obligation within a clause."""
    clause_id: str
    obligation_type: str        # OBLIGATION | PROHIBITION | PERMISSION | CONDITIONAL | DECLARATIVE
    obligated_party: str        # "ESSI", "Licensee", "either party", etc.
    action_verb: str            # "pay", "deliver", "notify", "terminate"
    beneficiary: str | None     # "Talent", "Licensor", etc.
    modal_trigger: str          # The actual text that triggered detection
    confidence: float           # 1.0 for exact modal match, 0.8 for inferred
    financial_params: list[str] = field(default_factory=list)  # Edge Case 4: PERCENTAGE/MONEY values


# ─── Modal verb patterns ─────────────────────────────────────────────────────
# Order matters: PROHIBITION patterns (negated) MUST be checked before OBLIGATION
_PROHIBITION_PATTERNS = [
    re.compile(r"\bshall\s+not\b", re.I),
    re.compile(r"\bmust\s+not\b", re.I),
    re.compile(r"\bmay\s+not\b", re.I),
    re.compile(r"\bwill\s+not\b", re.I),
    re.compile(r"\bis\s+prohibited\s+from\b", re.I),
    re.compile(r"\bare\s+prohibited\s+from\b", re.I),
    re.compile(r"\bshall\s+in\s+no\s+event\b", re.I),
    re.compile(r"\bunder\s+no\s+circumstances\b", re.I),
]

_OBLIGATION_PATTERNS = [
    re.compile(r"\bshall\b", re.I),
    re.compile(r"\bmust\b", re.I),
    re.compile(r"\bis\s+required\s+to\b", re.I),
    re.compile(r"\bare\s+required\s+to\b", re.I),
    re.compile(r"\bagrees?\s+to\b", re.I),
    re.compile(r"\bcovenants?\s+to\b", re.I),
    re.compile(r"\bundertakes?\s+to\b", re.I),
    re.compile(r"\bwill\b", re.I),  # 'will' in legal context = obligation
    re.compile(r"\bis\s+obligated\s+to\b", re.I),
]

_PERMISSION_PATTERNS = [
    re.compile(r"\bmay\b", re.I),
    re.compile(r"\bis\s+entitled\s+to\b", re.I),
    re.compile(r"\bare\s+entitled\s+to\b", re.I),
    re.compile(r"\bhas\s+the\s+right\s+to\b", re.I),
    re.compile(r"\bhave\s+the\s+right\s+to\b", re.I),
    re.compile(r"\bis\s+permitted\s+to\b", re.I),
    re.compile(r"\bare\s+permitted\s+to\b", re.I),
    re.compile(r"\bat\s+(?:its|their|his|her)\s+(?:sole\s+)?(?:option|discretion)\b", re.I),
]

_CONDITIONAL_PATTERNS = [
    re.compile(r"\bin\s+the\s+event\s+(?:that|of)\b", re.I),
    re.compile(r"\bupon\s+(?:the\s+)?(?:occurrence|completion|termination|expiration|receipt)\b", re.I),
    re.compile(r"\bif\s+(?:a|the|any|such)\b", re.I),
    re.compile(r"\bprovided\s+that\b", re.I),
    re.compile(r"\bsubject\s+to\b", re.I),
    re.compile(r"\bunless\b", re.I),
]

# ─── Verbs that create obligations (action verbs, not stative) ───────────────
_ACTION_VERBS = {
    "pay", "deliver", "provide", "notify", "terminate", "renew",
    "reimburse", "indemnify", "maintain", "submit", "report",
    "assign", "grant", "license", "disclose", "return",
    "remunerate", "purchase", "issue", "assist", "arrange",
    "prepare", "process", "develop", "establish", "conduct",
    "file", "make", "keep", "hold", "bear", "carry",
    "cease", "refrain", "ensure", "guarantee", "warrant",
    "cooperate", "comply", "perform", "execute", "fulfill",
    "cure", "remedy", "correct", "obtain", "acquire",
    "transfer", "convey", "distribute", "allocate",
    "publish", "negotiate", "procure", "handle", "promote",
    "advertise", "approve", "inspect", "audit", "remove",
    "place", "display", "sell", "market", "represent",
}

# ─── Fix C: Expanded stative / boilerplate verbs to skip ────────────────────
_SKIP_VERBS = {
    # Copula / auxiliary
    "be", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did",
    # Definitional / descriptive
    "mean", "include", "constitute", "deem", "contain",
    "apply", "limit", "affect", "impair", "relate",
    # Volitional / mental (not actionable compliance tasks)
    "wish", "desire", "intend", "believe", "consider",
    "understand", "acknowledge", "recognize", "know",
    # Temporal / durational (not actions)
    "arise", "accrue", "lapse", "expire", "elapse",
    "survive", "continue", "remain", "persist", "endure",
    # Stative / result (describe state, not action)
    "state", "indicate", "specify", "describe", "set",
    "entitle", "enable", "allow", "permit", "authorize",
    "base", "depend", "rest", "rely",
    "exist", "prevail", "pertain", "refer",
    "form", "comprise", "consist",
    # Passive-only verbs that never create actionable tasks
    "construe", "interpret", "infer", "imply",
    "locate", "situate", "reside",
    "exceed", "surpass", "preclude",
}

# ─── Fix D: Inanimate nouns that should never be party names ─────────────────
_INANIMATE_SUBJECTS = {
    "purpose", "term", "agreement", "contract", "business",
    "venture", "office", "account", "capital", "fund", "funds",
    "value", "interest", "amount", "payment", "fee", "cost",
    "right", "duty", "obligation", "provision", "section",
    "article", "assignment", "transfer", "liability",
    "notice", "consent", "meeting", "vote", "decision",
    "property", "asset", "contribution", "distribution",
    "nothing", "everything", "anything", "something",
}


# ─── Fix A: Semantic "will" filter ──────────────────────────────────────────
def _is_obligatory_will(sentence_text: str) -> bool:
    """
    Determine if 'will' in this sentence creates an actionable obligation.
    
    Returns False (reject) for:
      1. PASSIVE VOICE: "X will be done" — no agent performing the action
      2. STATIVE SUBJECT: subject is inanimate ("The purpose will be...")
      3. DEFINITIONAL: "will be [noun]", "will mean", "will have the meaning"
      
    Returns True (accept) for:
      1. AGENT + ACTION: "Members will contribute their Capital"
      2. NAMED PARTY: "Stryker will notify Conformis"
      
    Research basis: LEXDEMOD corpus — obligation requires a "capable agent"
    performing an "action verb", not a passive description.
    """
    nlp = _get_nlp()
    doc = nlp(sentence_text)
    
    # Find the "will" token
    will_token = None
    for token in doc:
        if token.text.lower() == "will" and token.pos_ in ("AUX", "VERB"):
            will_token = token
            break
    
    if will_token is None:
        return True  # No "will" found — shouldn't happen but be safe
    
    # Rule 1: Check for passive voice — "will be + past_participle"
    # Pattern: will → be → VBN (past participle)
    for child in will_token.head.children if will_token.dep_ == "aux" else doc:
        if child.dep_ == "auxpass" or (child.text.lower() == "be" and child.dep_ == "aux"):
            # Found "will be" pattern — now check if main verb is passive
            main_verb = will_token.head if will_token.dep_ == "aux" else None
            if main_verb and main_verb.tag_ in ("VBN",):
                # This IS passive: "will be maintained", "will be held"
                # Check if there's an active agent via "by + PERSON/ORG"
                has_agent = False
                for prep_child in main_verb.children:
                    if prep_child.dep_ == "agent" or (prep_child.text.lower() == "by" and prep_child.dep_ == "prep"):
                        has_agent = True
                        break
                if not has_agent:
                    return False  # Passive with no agent → REJECT
    
    # Rule 2: Check if the main verb governed by "will" is passive (VBN tag)
    if will_token.dep_ == "aux":
        head = will_token.head
        if head.tag_ == "VBN":
            # "will be located", "will be determined", etc.
            return False
    
    # Rule 3: Check for inanimate/abstract grammatical subject
    # Find the subject of the clause containing "will"
    root = will_token.head if will_token.dep_ == "aux" else will_token
    for child in root.children:
        if child.dep_ in ("nsubj", "nsubjpass"):
            subj_text = child.text.lower()
            subj_lemma = child.lemma_.lower()
            # Check if subject is an inanimate noun
            if subj_lemma in _INANIMATE_SUBJECTS or subj_text in _INANIMATE_SUBJECTS:
                return False
            # Check if subject is a determiner phrase with inanimate head
            # e.g., "The principal office" — head noun is "office"
            if child.pos_ == "NOUN" and subj_lemma in _INANIMATE_SUBJECTS:
                return False
            break  # Only check the first subject
    
    # Rule 4: Check for definitional patterns
    lower = sentence_text.lower()
    definitional_patterns = [
        "will be known as", "will be called", "will be referred to",
        "will be deemed", "will have the meaning", "will mean",
    ]
    for pat in definitional_patterns:
        if pat in lower:
            return False
    
    return True  # Passes all filters → accept as obligatory


# ─── Fix B: Passive voice agent extraction ──────────────────────────────────
def _extract_passive_agent(sentence_text: str) -> str | None:
    """
    In passive voice sentences, extract the agent from 'by + PARTY'.
    
    "payment will be made by the Borrower" → "Borrower"
    "records will be maintained by Stryker" → "Stryker"
    """
    nlp = _get_nlp()
    doc = nlp(sentence_text)
    
    for token in doc:
        if token.dep_ == "agent" or (token.text.lower() == "by" and token.dep_ == "prep"):
            # Look for PROPN or NNP children of "by"
            for child in token.children:
                if child.dep_ == "pobj" and child.pos_ in ("PROPN", "NOUN"):
                    # Get the full span (e.g., "the Borrower" → "Borrower")
                    agent_text = child.text
                    # If it has compound modifiers, include them
                    for comp in child.children:
                        if comp.dep_ == "compound":
                            agent_text = comp.text + " " + agent_text
                    return agent_text
    
    return None


# Max length for a valid party name
_MAX_PARTY_NAME_LEN = 50


# ─── Fix 2: Dependency-tree verb extraction ─────────────────────────────────
def _extract_verb_from_modal(text: str, modal_match: re.Match) -> str | None:
    """
    Use spaCy dependency parsing to find the main verb governed by the
    modal auxiliary.

    For "ESSI shall not publish a press release", after finding "shall not"
    at position X, we parse the text and find that "publish" is the ROOT
    verb whose auxiliary is "shall".

    Falls back to regex-based extraction if spaCy fails.
    """
    nlp = _get_nlp()
    
    # Parse just the relevant portion (modal + next 30 words) for efficiency
    start = modal_match.start()
    # Find the end of the sentence from the modal position
    remaining = text[start:]
    # Limit to ~200 chars for efficiency
    snippet = remaining[:200]
    
    doc = nlp(snippet)
    
    modal_text = modal_match.group(0).lower().split()[-1]  # "shall", "must", "may", "will"
    
    # Strategy 1: Find the ROOT verb in the snippet
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            verb = token.lemma_.lower()
            if verb not in _SKIP_VERBS:
                return verb
    
    # Strategy 2: Find any VERB that has an "aux" child matching our modal
    for token in doc:
        if token.pos_ == "VERB" and token.lemma_.lower() not in _SKIP_VERBS:
            for child in token.children:
                if child.dep_ in ("aux", "auxpass") and child.text.lower() in {"shall", "must", "may", "will"}:
                    return token.lemma_.lower()
    
    # Strategy 3: Find the first VERB token after the modal tokens
    found_modal = False
    for token in doc:
        if token.text.lower() in {"shall", "must", "may", "will", "not"}:
            found_modal = True
            continue
        if found_modal and token.pos_ == "VERB" and token.lemma_.lower() not in _SKIP_VERBS:
            return token.lemma_.lower()
    
    # Strategy 4: Regex fallback — first word after modal that's in action verbs list
    after_modal = text[modal_match.end():]
    words = re.findall(r'\b[a-z]+\b', after_modal.lower())
    for word in words[:15]:
        if word in _ACTION_VERBS:
            return word
    
    return None


# ─── Fix 3: Party name validation ───────────────────────────────────────────
def _validate_party_name(
    candidate: str,
    known_parties: list[str],
) -> str | None:
    """
    Validate a candidate party name against known PARTY entities.
    
    Returns the validated name, or None if invalid.
    
    Validation rules:
    1. Must be <= 50 characters
    2. Must fuzzy-match a known party (substring match)
    3. Must not be a sentence fragment (no verbs, max 5 words)
    """
    candidate = candidate.strip()
    
    # Rule 1: Length check
    if len(candidate) > _MAX_PARTY_NAME_LEN:
        return None
    
    # Rule 2: Word count check (party names rarely exceed 5 words)
    words = candidate.split()
    if len(words) > 6:
        return None
    
    # Rule 3: Should not contain common verb indicators
    fragment_indicators = {
        "shall", "must", "may", "will", "are", "is", "was", "were",
        "provided", "required", "pursuant", "herein", "hereunder",
        "notwithstanding", "accordance", "connection", "respect",
        "acquired", "information", "confidential",
        # Additional fragments that slip through as party names
        "such", "restrictions", "occurrence", "event", "provisions",
        "foregoing", "obligations", "rights", "terms", "conditions",
        "no", "any", "all", "each", "neither", "both", "either",
        "force", "majeure", "site", "homepage", "section",
        "damages", "liability", "warranty", "remedies",
        "amounts", "payments", "fees", "costs", "expenses",
        "who", "which", "that", "what", "where", "when",
        # Fix D: Contract defined terms that are NOT parties
        "purpose", "term", "agreement", "contract", "amendment",
        "contributions", "duties", "interests",
        "appraiser", "arbitrator", "mediator",
        "capital", "funds", "assets", "property", "account",
        "notice", "consent", "approval", "request",
        "venture", "enterprise", "business", "operation",
        "assignment", "transfer", "distribution",
        "provision", "clause", "paragraph", "article",
        "foregoing", "following", "above", "below",
        "default", "breach", "failure", "violation",
        "nothing", "something", "everything", "anything",
    }
    lower_words = {w.lower() for w in words}
    if lower_words & fragment_indicators:
        return None
    
    # Rule 4: First word should be capitalized (proper noun) or a known role
    known_roles = {
        "party", "parties", "licensor", "licensee", "seller", "buyer",
        "vendor", "purchaser", "employer", "employee", "contractor",
        "consultant", "lessor", "lessee", "landlord", "tenant",
        "borrower", "lender", "company", "corporation", "talent",
        "artist", "provider", "recipient", "owner",
    }
    first_word = words[0] if words else ""
    if first_word and not first_word[0].isupper() and first_word.lower() not in known_roles:
        return None
    
    # If known parties exist, try fuzzy matching
    if known_parties:
        candidate_lower = candidate.lower()
        for party in known_parties:
            party_lower = party.lower()
            # Exact match
            if candidate_lower == party_lower:
                return candidate
            # Candidate is substring of known party
            if candidate_lower in party_lower:
                return party  # Return the full known party name
            # Known party is substring of candidate
            if party_lower in candidate_lower:
                return party  # Return the full known party name
    
    # If no known parties, accept the candidate if it passes basic checks
    if not known_parties:
        return candidate
    
    return None


def _get_best_party(
    candidate: str | None,
    known_parties: list[str],
    fallback: str = "Contracting Party",
) -> str:
    """Get the best party name, with validation and fallback."""
    if candidate:
        validated = _validate_party_name(candidate, known_parties)
        if validated:
            return validated
    
    if known_parties:
        return known_parties[0]
    
    return fallback


# ─── Main classifier ────────────────────────────────────────────────────────
def classify_obligation(
    clause_id: str,
    body_text: str,
    relations: list[dict],
    entity_summary: dict,
    clause_type: str = "",
) -> list[ObligationRecord]:
    """
    Classify the obligations in a single clause/sentence.

    v2: Now designed to be called per-sentence from task_generator.py.
    Modal detection operates on the passed body_text (which should be a
    single sentence for accurate scoping).

    Returns a list of ObligationRecord (one per detected obligation).
    """
    # Skip non-content clause types
    skip_types = {"PREAMBLE", "DEFINITIONS", "DEFINITION_GROUP", "SIGNATURE_BLOCK"}
    if clause_type.upper() in skip_types:
        return []

    if not body_text or not body_text.strip():
        return []

    known_parties = entity_summary.get("PARTY", [])
    if isinstance(known_parties, str):
        known_parties = [known_parties]

    records: list[ObligationRecord] = []

    # Detect the dominant modality of this text
    modality, trigger, confidence, modal_match = _detect_modality(body_text)

    # ─── Gate: If no deontic modality detected, no obligations exist ──────
    # This is the critical filter for Fix A: when "will" is rejected by the
    # semantic filter, modality returns DECLARATIVE. We MUST stop here —
    # otherwise Path A (relations) and Path B (body text) will still generate
    # tasks from the declarative sentence.
    if modality == "DECLARATIVE":
        return []

    # Detect if there's a conditional wrapper
    is_conditional = _has_conditional(body_text)

    # Extract the verb from the modal using dependency parsing (Fix 2)
    dep_verb = None
    if modal_match is not None:
        dep_verb = _extract_verb_from_modal(body_text, modal_match)

    # ─── Path A: Build from relations (if available) ─────────────────────
    if relations:
        for rel in relations:
            verb = rel.get("verb", "")
            if verb.lower() not in _ACTION_VERBS:
                continue

            subject = rel.get("subject", "Unknown Party")
            obj_name = rel.get("object", "")
            obj_label = rel.get("object_label", "")

            # Fix 3: Validate party name
            subject = _get_best_party(subject, known_parties)

            # Determine beneficiary vs financial parameter (Edge Case 4)
            beneficiary = None
            financial = []
            if obj_label in {"PARTY", "ORG"}:
                beneficiary = obj_name
            elif obj_label in {"PERCENTAGE", "MONEY"}:
                financial.append(f"{obj_name}")

            obligation_type = modality
            if is_conditional and modality in {"OBLIGATION", "PROHIBITION"}:
                obligation_type = "CONDITIONAL"

            records.append(ObligationRecord(
                clause_id=clause_id,
                obligation_type=obligation_type,
                obligated_party=subject,
                action_verb=verb,
                beneficiary=beneficiary,
                modal_trigger=trigger,
                confidence=confidence,
                financial_params=financial,
            ))

    # ─── Path B: No relations — extract from body text ────────────────────
    if not records:
        body_obligations = _extract_from_body(
            clause_id, body_text, entity_summary, modal_match, dep_verb
        )
        if body_obligations:
            for rec in body_obligations:
                if is_conditional and rec.obligation_type in {"OBLIGATION", "PROHIBITION"}:
                    rec.obligation_type = "CONDITIONAL"
                rec.modal_trigger = trigger
                rec.confidence = confidence * 0.9
            records.extend(body_obligations)

    # ─── Fallback: If clause has DURATION entities but still no records ───
    if not records:
        durations = entity_summary.get("DURATION", [])
        if durations and modality in {"OBLIGATION", "PROHIBITION", "PERMISSION"}:
            party = _get_best_party(None, known_parties)
            verb = dep_verb or "comply"  # dep_verb should almost always work now
            records.append(ObligationRecord(
                clause_id=clause_id,
                obligation_type="CONDITIONAL" if is_conditional else modality,
                obligated_party=party,
                action_verb=verb,
                beneficiary=None,
                modal_trigger=trigger,
                confidence=confidence * 0.7,
                financial_params=[],
            ))

    # Deduplicate
    return _dedupe_records(records)


def _detect_modality(text: str) -> tuple[str, str, float, re.Match | None]:
    """
    Detect the dominant deontic modality of a text.

    Returns (modality_type, trigger_text, confidence, match_object).
    PROHIBITION is checked BEFORE OBLIGATION (negation-first).
    """
    # Check PROHIBITION first (negated modals)
    for pat in _PROHIBITION_PATTERNS:
        m = pat.search(text)
        if m:
            # Fix A: "will not" in passive/declarative → still a prohibition
            # (prohibitions on passive constructions are still valid restrictions)
            return "PROHIBITION", m.group(0), 1.0, m

    # Check OBLIGATION
    for pat in _OBLIGATION_PATTERNS:
        m = pat.search(text)
        if m:
            trigger_text = m.group(0).lower().strip()
            # Fix A: If the trigger is "will", apply semantic filter
            if trigger_text == "will":
                if not _is_obligatory_will(text):
                    # "will" is used descriptively/passively → skip to PERMISSION check
                    continue
                # Accepted — but at lower confidence than "shall"/"must"
                return "OBLIGATION", m.group(0), 0.9, m
            return "OBLIGATION", m.group(0), 1.0, m

    # Check PERMISSION
    for pat in _PERMISSION_PATTERNS:
        m = pat.search(text)
        if m:
            return "PERMISSION", m.group(0), 1.0, m

    return "DECLARATIVE", "", 0.0, None


def _has_conditional(text: str) -> bool:
    """Check if the clause has a conditional wrapper."""
    for pat in _CONDITIONAL_PATTERNS:
        if pat.search(text):
            return True
    return False


# ─── Party+Modal regex for body-text extraction ─────────────────────────────
_PARTY_MODAL_RE = re.compile(
    r"(?:(?:the|each|either|any)\s+)?"           # optional determiner
    r"((?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"        # Proper noun name
    r"|(?:Licensor|Licensee|Seller|Buyer|Vendor|Purchaser"
    r"|Employer|Employee|Contractor|Consultant"
    r"|Lessor|Lessee|Landlord|Tenant"
    r"|Borrower|Lender|Company|Corporation"
    r"|Talent|Artist|Provider|Recipient"
    r"|party|Party|parties|Parties"
    r"|either\s+party|each\s+party|any\s+party)))"
    r"\s+"
    r"(?:shall|must|may|will|agrees?\s+to|is\s+required\s+to)",
    re.IGNORECASE,
)


def _extract_from_body(
    clause_id: str,
    body_text: str,
    entity_summary: dict,
    modal_match: re.Match | None,
    dep_verb: str | None,
) -> list[ObligationRecord]:
    """
    Extract obligations directly from body text when no relations exist.
    Uses regex to find [Party] + [modal] + [verb] patterns.

    v2: Uses dep_verb from spaCy dependency parsing instead of static list.
    v2: Validates party names against known PARTY entities.
    """
    records = []
    known_parties = entity_summary.get("PARTY", [])
    if isinstance(known_parties, str):
        known_parties = [known_parties]

    # Try regex-based party+modal detection
    for match in _PARTY_MODAL_RE.finditer(body_text):
        party_text = match.group(1).strip()
        
        # Fix 3: Validate party name
        validated_party = _get_best_party(party_text, known_parties)

        # Fix 2: Extract verb using dependency tree from this specific match
        verb = _extract_verb_from_modal(body_text, match) or dep_verb

        if verb:
            modality, trigger, conf, _ = _detect_modality(body_text[match.start():match.end() + 100])
            records.append(ObligationRecord(
                clause_id=clause_id,
                obligation_type=modality if modality != "DECLARATIVE" else "OBLIGATION",
                obligated_party=validated_party,
                action_verb=verb,
                beneficiary=None,
                modal_trigger=trigger,
                confidence=conf,
                financial_params=[],
            ))

    # If regex fails but we have PARTY entities, use the first party + clause modality
    if not records and known_parties:
        modality, trigger, conf, _ = _detect_modality(body_text)
        if modality != "DECLARATIVE":
            verb = dep_verb  # Use the dep-parsed verb (Fix 2)
            if not verb:
                # Last resort: try static list
                verb = _find_first_verb(body_text)
            records.append(ObligationRecord(
                clause_id=clause_id,
                obligation_type=modality,
                obligated_party=known_parties[0],
                action_verb=verb or "perform",  # "perform" is better than "comply"
                beneficiary=None,
                modal_trigger=trigger,
                confidence=conf * 0.8,
                financial_params=[],
            ))

    return records


def _find_first_verb(text: str) -> str | None:
    """Find the first action verb in a text snippet (static list fallback)."""
    words = re.findall(r'\b[a-z]+\b', text.lower())
    for word in words[:15]:
        if word in _ACTION_VERBS:
            return word
    return None


def _dedupe_records(records: list[ObligationRecord]) -> list[ObligationRecord]:
    """Remove duplicate obligations (same party + same verb)."""
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for rec in records:
        key = (rec.obligated_party.lower(), rec.action_verb.lower(), rec.obligation_type)
        if key not in seen:
            seen.add(key)
            deduped.append(rec)
    return deduped


# ─── Contract-level classifier ──────────────────────────────────────────────
def classify_contract_obligations(
    clauses_data: list[dict],
) -> list[list[ObligationRecord]]:
    """
    Classify obligations for an entire contract.

    Parameters
    ----------
    clauses_data : list[dict]
        Each dict must have:
          - clause_id: str
          - body_text: str
          - relations: list[dict]
          - entity_summary: dict
          - clause_type: str (from classifier)

    Returns
    -------
    list[list[ObligationRecord]]
        One list of obligations per clause, aligned to input order.
    """
    return [
        classify_obligation(
            clause_id=cd.get("clause_id", f"clause_{i}"),
            body_text=cd.get("body_text", ""),
            relations=cd.get("relations", []),
            entity_summary=cd.get("entity_summary", {}),
            clause_type=cd.get("clause_type", ""),
        )
        for i, cd in enumerate(clauses_data)
    ]
