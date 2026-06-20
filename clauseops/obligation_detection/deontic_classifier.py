"""
ClauseOps — Deontic Obligation Classifier (v5 — Custom BERT Modality + spaCy Extraction)

ARCHITECTURE CHANGE: v4→v5
  v4 used Custom BERT for Modality AND NER (Token Classification).
  v5 uses:
    1. Custom BERT for Modality (OBLIGATION/PROHIBITION/PERMISSION/DECLARATIVE).
    2. spaCy Dependency Parsing (en_core_web_trf) for Agent/Action extraction.

  Why? Token classification (NER) is fundamentally the wrong architecture for extracting
  long legal action predicates (e.g. 40+ words) and cannot mathematically map subjects to verbs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from clauseops.obligation_detection.bert_classifier import extract_clause_bert
from clauseops.obligation_detection import qa_extractor

logger = logging.getLogger(__name__)

# ─── QA extractor availability (cached once) ────────────────────────────────
# When the offline QA model is present, it replaces the brittle spaCy
# dependency-parse extraction. When absent (e.g. before training), we fall
# back to the legacy spaCy path so the pipeline keeps working.
_QA_ENABLED: bool | None = None


def _qa_enabled() -> bool:
    global _QA_ENABLED
    if _QA_ENABLED is None:
        _QA_ENABLED = qa_extractor.is_qa_available()
        if _QA_ENABLED:
            logger.info("QA extractor ENABLED for agent/action extraction.")
        else:
            logger.info("QA extractor unavailable; using legacy spaCy extraction.")
    return _QA_ENABLED

# ─── Lazy spaCy loader (en_core_web_trf — reuses Phase 3's model) ───────────
_nlp = None

def _get_nlp():
    """Lazy-load spaCy trf model. Reuses Phase 3's en_core_web_trf for
    accuracy (~95% on legal text vs sm's ~88%)."""
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_trf")
            logger.info("Loaded en_core_web_trf for obligation detection")
        except OSError:
            logger.warning("en_core_web_trf not found, falling back to en_core_web_sm")
            _nlp = spacy.load("en_core_web_sm")
    return _nlp

# ─── Dataclass ───────────────────────────────────────────────────────────────
@dataclass
class ObligationRecord:
    """A single detected obligation within a clause."""
    clause_id: str
    obligation_type: str        # OBLIGATION | PROHIBITION | PERMISSION | CONDITIONAL
    obligated_party: str        # "ESSI", "Licensee", etc.
    action_verb: str            # QA action span (or legacy verb): "pay", "deliver to the Agent ..."
    beneficiary: str | None     # "Talent", "Licensor", etc.
    modal_trigger: str          # The actual text that triggered detection
    confidence: float           # BERT Modality confidence score
    financial_params: list[str] = field(default_factory=list)
    action: str = ""            # Full extracted action span (QA path); mirrors action_verb
    agent_score: float = 0.0    # QA agent span confidence (0.0 in legacy path)
    action_score: float = 0.0   # QA action span confidence (0.0 in legacy path)
    requires_review: bool = False  # set when the agent is INFERRED (B3 passive) not extracted


# ─── Clause types to skip (structural / pure boilerplate only) ──────────────
# Phase B: classification is now ADVISORY, not a hard gate. We only skip types
# that are structural or never contain party obligations. The catch-all
# ENTIRE_AGREEMENT was REMOVED — per Limitation.txt #1 it holds real obligation
# clauses (Grant of License, Delivery, Survival, Further Assurances), so gating
# on it silently dropped obligations. The modality classifier is the real gate.
_SKIP_CLAUSE_TYPES = {
    "PREAMBLE", "DEFINITIONS", "DEFINITION_GROUP", "SIGNATURE_BLOCK",
    "GOVERNING_LAW", "SEVERABILITY", "COUNTERPARTS",
}

# ─── Quick modal check (for the cheap gate — Step 1) ────────────────────────
_ANY_MODAL_RE = re.compile(
    r"\b(?:shall|must|may|will|agrees?\s+to|is\s+required\s+to|"
    r"covenants?\s+to|undertakes?\s+to|is\s+obligated\s+to|"
    r"is\s+entitled\s+to|has\s+the\s+right\s+to|"
    r"is\s+prohibited\s+from|is\s+permitted\s+to)\b",
    re.IGNORECASE,
)

# ─── Prohibition check (negated modals — for sub-typing after BERT) ──────────
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

# ─── Modal patterns for finding the PRIMARY modal in a clause ────────────────
_RANKED_MODALS = [
    (re.compile(r"\bshall\b", re.I), "shall"),
    (re.compile(r"\bmust\b", re.I), "must"),
    (re.compile(r"\bis\s+required\s+to\b", re.I), "is required to"),
    (re.compile(r"\bagrees?\s+to\b", re.I), "agrees to"),
    (re.compile(r"\bcovenants?\s+to\b", re.I), "covenants to"),
    (re.compile(r"\bundertakes?\s+to\b", re.I), "undertakes to"),
    (re.compile(r"\bwill\b", re.I), "will"),
    (re.compile(r"\bmay\b", re.I), "may"),
]

# ─── Conditional patterns ───────────────────────────────────────────────────
_CONDITIONAL_PATTERNS = [
    re.compile(r"\bin\s+the\s+event\s+(?:that|of)\b", re.I),
    re.compile(r"\bupon\s+(?:the\s+)?(?:occurrence|completion|termination|expiration|receipt)\b", re.I),
    re.compile(r"\bif\s+(?:a|the|any|such)\b", re.I),
    re.compile(r"\bprovided\s+that\b", re.I),
    re.compile(r"\bsubject\s+to\b", re.I),
    re.compile(r"\bunless\b", re.I),
]

# ─── Action verbs (for verb extraction fallback) ──────────────────────────────
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
    "install", "equip", "contribute", "invest", "fund",
    "insure", "protect", "restrict", "prohibit", "forbid",
    "withhold", "depict", "exhibit", "accept", "receive",
}

# ─── Stative verbs to skip during verb extraction ───────────────────────────
_SKIP_VERBS = {
    "be", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did",
    "mean", "include", "constitute", "deem", "contain",
    "apply", "limit", "affect", "relate",
    "state", "indicate", "specify", "describe",
    "exist", "prevail", "pertain", "refer",
    "form", "comprise", "consist",
    "construe", "interpret", "imply",
    "exceed", "surpass", "preclude",
}

# ─── Known legal role words (for party resolution) ──────────────────────────
_KNOWN_ROLES = {
    "party", "parties", "licensor", "licensee", "seller", "buyer",
    "vendor", "purchaser", "employer", "employee", "contractor",
    "consultant", "lessor", "lessee", "landlord", "tenant",
    "borrower", "lender", "company", "corporation", "talent",
    "artist", "provider", "recipient", "owner",
    "member", "members", "manager", "partner", "partners",
    "agent", "principal", "guarantor", "surety",
    "affiliate", "affiliates", "subsidiary", "subsidiaries",
}

# ─── Invalid subjects that should never be parties ────────────────────────────
_REJECT_SUBJECTS = {
    "this section", "this clause", "this agreement", "this paragraph",
    "the following", "all matters", "such matters", "these provisions",
    "the foregoing", "nothing", "everything", "anything", "something",
    "the same", "such", "each", "any", "all", "no",
    "this contract", "this document", "the contract", "the agreement",
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CLASSIFIER (v5 — Hybrid Rules + Custom BERT Modality)
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE G — modal-less obligation recovery (infinitive / imperative lists)
# ═══════════════════════════════════════════════════════════════════════════════
#
# WHY: Some contracts (JVs, MOUs, term sheets) phrase obligations as infinitive
# or imperative lists under an "OBLIGATIONS OF {Party}" heading or a
# "{Party} shall ... as follows:" lead-in, e.g.:
#       ARTICLE 9 - OBLIGATIONS OF NVOS
#       9.1 To maintain all financial records ...
#       9.2 Assign and direct operational staff ...
# These lines carry NO modal, so the cheap _ANY_MODAL_RE gate skips them and the
# trained models never see them. Phase G reconstructs the implied sentence
# ("NVOS shall maintain all financial records ...") and feeds THAT to the SAME
# modality classifier + QA extractor. The model still decides (DECLARATIVE -> skip),
# so this only adds recall where a real governing party exists; it does not
# fabricate or bypass the models.

_OBLIGATIONS_HEADING_RE = re.compile(
    r"\bOBLIGATION[S]?\s+OF\s+(?:THE\s+)?(?P<party>[A-Z][\w&.\- ]{1,40}?)\s*$",
    re.IGNORECASE,
)

# "<subject> shall/will/agrees to ... as follows:" (may continue on same line)
_AS_FOLLOWS_RE = re.compile(
    r"\b(?:shall|will|agree[s]?\s+to|covenant[s]?\s+to|undertake[s]?\s+to)\b"
    r"[^.]*?\bas\s+follows\b",
    re.IGNORECASE,
)

_ITEM_PREFIX_RE = re.compile(r"^[\s\u2022\u25cf\u25aa\u2023\-\*]*(?:\d+(?:\.\d+)*\.?\s*)?[\s\u2022\u25cf\u25aa\u2023\-\*]*")
_INFINITIVE_RE = re.compile(r"^to\s+([a-z].+)$", re.IGNORECASE)

# Imperative action verbs that commonly head modal-less obligation list items.
_IMPERATIVE_VERBS = {
    "assign", "provide", "maintain", "arrange", "make", "complete", "direct",
    "grow", "promote", "issue", "purchase", "remunerate", "deliver", "ensure",
    "prepare", "keep", "submit", "pay", "indemnify", "notify", "furnish",
    "supply", "perform", "execute", "comply", "cooperate", "contribute",
    "develop", "construct", "establish", "manage", "operate", "distribute",
    "market", "sell", "report", "obtain", "procure", "reimburse", "return",
}


def _governing_agent_from_heading(heading: str) -> str | None:
    """Extract the obligated party from an 'OBLIGATIONS OF {Party}' heading."""
    if not heading:
        return None
    # Heading may carry a section prefix ("ARTICLE 9 - OBLIGATIONS OF NVOS").
    tail = re.split(r"[-\u2014:]", heading)[-1].strip() if heading else heading
    for candidate in (heading, tail):
        m = _OBLIGATIONS_HEADING_RE.search(candidate)
        if m:
            party = m.group("party").strip(" .:-")
            if party and len(party.split()) <= 6:
                return party
    return None


def _strip_item_prefix(text: str) -> str:
    return _ITEM_PREFIX_RE.sub("", text, count=1).strip()


def _bare_party_subheader(item: str, known_parties: list[str]) -> str | None:
    """A short line that is just a party name acting as a sub-list header
    (e.g. '5.1.1 NVOS' -> 'NVOS'). Returns the party or None."""
    if not item or len(item.split()) > 4:
        return None
    low = item.lower().strip(" .:-")
    for p in known_parties:
        if p and low == p.lower():
            return p
    return None


def _split_leading_party(item: str, known_parties: list[str]) -> tuple[str | None, str]:
    """If a list item begins with a known party fused with its directive
    (e.g. 'NVOS ● Complete and finalize ...'), return (party, remainder)."""
    for p in sorted([p for p in known_parties if p], key=len, reverse=True):
        if item.lower().startswith(p.lower()):
            rest = _strip_item_prefix(item[len(p):])
            if rest and rest != item:
                return p, rest
    return None, item


def _as_directive(item: str) -> str | None:
    """If `item` is a modal-less obligation directive, return the verb-phrase to
    splice after '<agent> shall'. Otherwise None.

    "To maintain all financial records" -> "maintain all financial records"
    "Assign and direct operational staff" -> "assign and direct operational staff"
    """
    if not item or len(item) < 8:
        return None
    m = _INFINITIVE_RE.match(item)
    if m:
        return m.group(1).strip()
    first = re.sub(r"[^a-zA-Z]", "", item.split()[0]).lower()
    if first in _IMPERATIVE_VERBS:
        return item[0].lower() + item[1:]
    return None


def _subject_before_modal(text: str) -> str | None:
    """Subject phrase appearing before the modal in an 'as follows:' lead-in."""
    m = re.search(r"^(.*?)\b(?:shall|will|agree[s]?\s+to|covenant[s]?\s+to|undertake[s]?\s+to)\b",
                  text, re.IGNORECASE)
    if not m:
        return None
    subj = _strip_item_prefix(m.group(1).strip())
    if subj and 1 <= len(subj.split()) <= 6:
        return subj
    return None


# ─── B1: duty-TABLE mining ──────────────────────────────────────────────────
_DUTY_TABLE_HEADER_RE = re.compile(
    r"\b(dut(?:y|ies)|responsibilit|obligation|deliverable|scope|task|commitment|role|service)s?\b",
    re.IGNORECASE,
)
_ORG_MARKER_RE = re.compile(r"\b(?:inc|llc|ltd|corp|corporation|company|gmbh|co|plc|lp)\b\.?", re.I)
_TABLE_ROLE_WORDS = {
    "member", "members", "licensee", "licensor", "party", "parties", "company",
    "venture", "vendor", "supplier", "contractor", "consultant", "buyer",
    "seller", "tenant", "landlord", "lessee", "lessor", "employer", "employee",
}


def _looks_like_markdown_table(text: str) -> bool:
    return sum(1 for ln in text.splitlines() if ln.count("|") >= 2) >= 2


def _parse_markdown_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.count("|") < 2:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all((not c) or re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue  # separator row
        if any(cells):
            rows.append(cells)
    return rows


def _match_table_party(cell: str, known_parties: list[str]) -> str | None:
    cell = cell.strip(" *\t")
    if not cell:
        return None
    low = cell.lower()
    for p in known_parties:
        if p and (low == p.lower() or (len(p) > 3 and (p.lower() in low or low in p.lower()))):
            return p
    if _ORG_MARKER_RE.search(low) and 1 <= len(cell.split()) <= 8:
        return cell
    if low.strip(" .:-") in _TABLE_ROLE_WORDS:
        return cell.title()
    return None


def _table_obligation_candidates(
    text: str, known_parties: list[str]
) -> list[tuple[str, str | None, str, bool]]:
    """B1: mine a duty/responsibility TABLE into party->duty obligations.

    Only fires for tables whose header indicates duties/responsibilities/
    deliverables/scope (NOT payment/royalty/contribution schedules), and only
    for rows whose first cell is a real party. The party and duty are taken
    verbatim from the cells (grounded), so this does not weaken safety.
    """
    rows = _parse_markdown_rows(text)
    if len(rows) < 2:
        return []
    header = " ".join(rows[0]).lower()
    if not _DUTY_TABLE_HEADER_RE.search(header):
        return []
    out: list[tuple[str, str | None, str, bool]] = []
    for cells in rows[1:]:
        if len(cells) < 2:
            continue
        party = _match_table_party(cells[0], known_parties)
        if not party:
            continue
        duty = " ".join(c for c in cells[1:] if c).strip(" *\t")
        if len(duty) < 8:
            continue
        recon = f"{party} shall {duty[0].lower()}{duty[1:]}"
        out.append((recon, party, duty, False))
    return out


# ─── B3: passive operational obligations ────────────────────────────────────
_PARTICIPLE_TO_VERB = {
    "kept": "keep", "held": "hold", "made": "make", "placed": "place",
    "maintained": "maintain", "prepared": "prepare", "paid": "pay",
    "delivered": "deliver", "provided": "provide", "distributed": "distribute",
    "insured": "insure", "remitted": "remit", "conducted": "conduct",
    "submitted": "submit", "filed": "file", "retained": "retain",
    "performed": "perform", "completed": "complete", "executed": "execute",
}
_OPERATIONAL_SUBJECT_RE = re.compile(
    r"\b(?:records?|books?|funds?|accounts?|statements?|minutes|meetings?|reports?|"
    r"payments?|distributions?|insurance|notices?|filings?|returns?|audits?)\b",
    re.IGNORECASE,
)
_PASSIVE_OBLIGATION_RE = re.compile(r"\b(?:will|shall)\s+be\s+([a-z]+)\b", re.IGNORECASE)
# An operational entity named IN the sentence (grounded) — preferred agent.
_INSENTENCE_ORG_RE = re.compile(
    r"\bthe\s+(?:Joint\s+Venture|Venture|Company|Corporation|Partnership)\b", re.IGNORECASE,
)


def _operational_party(known_parties: list[str]) -> str | None:
    """An organizational/collective contract party for implied operational
    duties (the Company/Venture/...). Never invents — must be a known party."""
    for p in known_parties:
        if p and re.search(r"\b(?:company|venture|corporation|llc|inc|ltd)\b", p, re.I):
            return p
    return None


def _passive_obligation_rewrite(
    raw: str, heading_agent: str | None, known_parties: list[str]
) -> tuple[str, str] | None:
    """B3: rewrite a passive operational obligation ("records will be kept ...")
    into active voice. The agent is, in priority order: the section's governing
    party, an operational entity named IN the sentence ("the Venture"), or a
    known organizational party. Returns (reconstructed, agent) or None. The
    caller flags the result requires_review (agent is inferred, not the
    grammatical subject).
    """
    m = _PASSIVE_OBLIGATION_RE.search(raw)
    if not m:
        return None
    verb = _PARTICIPLE_TO_VERB.get(m.group(1).lower())
    if not verb:
        return None
    pre = raw[:m.start()]
    if not _OPERATIONAL_SUBJECT_RE.search(pre):
        return None

    agent = heading_agent
    if not agent:
        insent = _INSENTENCE_ORG_RE.search(raw)
        agent = insent.group(0) if insent else _operational_party(known_parties)
    if not agent:
        return None

    subject = _strip_item_prefix(pre).strip().rstrip(",").strip()
    rest = raw[m.end():].strip()
    obj = f"{subject} {rest}".strip()
    return f"{agent} shall {verb} {obj}".strip(), agent


def _build_obligation_candidates(
    doc,
    heading: str,
    known_parties: list[str],
) -> list[tuple[str, str | None, str, bool]]:
    """Yield (model_text, party_hint, original_text, force_review) candidates.

    Modal sentences pass through unchanged. Modal-less directive lines in an
    obligation-list context (Phase G), duty TABLES (B1), and passive operational
    obligations (B3) are reconstructed so they reach the models.
    """
    # ── B1: duty TABLE? mine party->duty rows instead of sentence logic ──────
    text = doc.text
    if _looks_like_markdown_table(text):
        return _table_obligation_candidates(text, known_parties)

    governing = _governing_agent_from_heading(heading)
    current_agent = governing
    out: list[tuple[str, str | None, str, bool]] = []

    for sent in doc.sents:
        raw = sent.text.strip()
        if not raw or len(raw) < 10:
            continue

        # Lead-in like "Each of the Parties shall contribute ... as follows:"
        if _AS_FOLLOWS_RE.search(raw):
            subj = _subject_before_modal(raw)
            if subj:
                current_agent = subj
            out.append((raw, None, raw, False))
            continue

        item = _strip_item_prefix(raw)

        # Bare party sub-header ("5.1.1 NVOS") switches the governing agent.
        ph = _bare_party_subheader(item, known_parties)
        if ph:
            current_agent = ph
            continue

        if _ANY_MODAL_RE.search(raw):
            # B3: passive operational obligation the model would gate as DECLARATIVE.
            passive = _passive_obligation_rewrite(raw, governing, known_parties)
            if passive:
                recon, agent = passive
                out.append((recon, agent, raw, True))  # inferred agent -> review
            else:
                out.append((raw, None, raw, False))
            continue

        # Modal-less (Phase G): inline "PARTY <directive>" switches the agent.
        lead_party, directive_src = _split_leading_party(item, known_parties)
        agent = lead_party or current_agent
        if lead_party:
            current_agent = lead_party

        directive = _as_directive(directive_src)
        if directive and agent:
            recon = f"{agent} shall {directive}"
            out.append((recon, agent, directive_src, False))

    return out


def classify_obligation(
    clause_id: str,
    body_text: str,
    relations: list[dict],
    entity_summary: dict,
    clause_type: str = "",
    heading: str = "",
) -> list[ObligationRecord]:
    """
    Classify the obligations in a single clause.

    v5 Architecture: Sentence-Level Processing
      Step 1: Segment massive paragraphs into individual sentences via spaCy
      Step 2: BERT VERIFICATION — Custom BERT confirms obligation/prohibition/permission for EACH sentence
      Step 3: EXTRACT — find primary obligation (party + verb) via dep parsing for EACH sentence
      Step 4: RETURN — List of ObligationRecords found in the clause

    Phase G: modal-less obligation list items (under an "Obligations of {Party}"
    heading or a "{Party} shall ... as follows:" lead-in) are reconstructed into
    "<agent> shall <directive>" and fed to the SAME models, recovering
    obligations that the modal gate would otherwise skip.
    """
    if clause_type.upper() in _SKIP_CLAUSE_TYPES:
        return []

    if not body_text or len(body_text.strip()) < 20:
        return []

    body_upper = body_text[:200].upper()
    if any(marker in body_upper for marker in [
        "WHEREAS", "RECITALS", "WITNESSETH", "IN WITNESS WHEREOF",
        "NOW, THEREFORE", "NOW THEREFORE",
    ]):
        return []

    nlp = _get_nlp()
    doc = nlp(body_text)

    known_parties = entity_summary.get("PARTY", [])
    if isinstance(known_parties, str):
        known_parties = [known_parties]

    candidates = _build_obligation_candidates(doc, heading, known_parties)

    records = []

    for model_text, party_hint, orig_text, force_review in candidates:
        if not _ANY_MODAL_RE.search(model_text):
            continue

        # ─── Step 2: BERT MODALITY VERIFICATION (Per Sentence) ───────────
        bert_result = extract_clause_bert(model_text)
        obligation_type = bert_result["modality"]
        confidence = bert_result["confidence"]

        if obligation_type == "DECLARATIVE":
            logger.debug("BERT rejected sentence: %s", model_text[:50])
            continue

        # ─── Step 3: SUB-TYPING (prohibition / conditional from original) ─
        modal_trigger, modal_match = _find_primary_modal(model_text)
        is_conditional = _has_conditional(orig_text)

        if obligation_type == "OBLIGATION":
            for pat in _PROHIBITION_PATTERNS:
                if pat.search(orig_text):
                    obligation_type = "PROHIBITION"
                    break

        if is_conditional and obligation_type in ("OBLIGATION", "PROHIBITION"):
            obligation_type = "CONDITIONAL"

        # ─── Agent + Action extraction ───────────────────────────────────
        agent_score = 0.0
        action_score = 0.0

        if _qa_enabled():
            # NEW: offline extractive QA (grounded, cannot hallucinate).
            qa = qa_extractor.extract_agent_action(model_text, obligation_type)
            party = qa["agent"] or party_hint
            if party is None:
                # No-answer monotonicity (Property 4): never fabricate a party.
                logger.debug("QA abstained on agent; skipping: %s", model_text[:50])
                continue
            action_phrase = qa["action"] or ""
            verb = action_phrase if action_phrase else "perform"
            agent_score = qa["agent_score"]
            action_score = qa["action_score"]
        else:
            # LEGACY: spaCy dependency-parse extraction (until QA model exists).
            verb = None
            if modal_match:
                verb = _extract_verb_from_modal(model_text, modal_match)
            if not verb:
                verb = _extract_verb_spacy(model_text)
            if not verb:
                verb = _find_first_action_verb(model_text)
            if not verb:
                verb = "perform"
            party = party_hint or _extract_obligated_party(model_text, known_parties, entity_summary)
            action_phrase = verb

        beneficiary = _extract_beneficiary(relations, party)

        financial = []
        for rel in relations:
            if rel.get("object_label") in ("MONEY", "PERCENTAGE"):
                financial.append(rel.get("object", ""))

        record = ObligationRecord(
            clause_id=clause_id,
            obligation_type=obligation_type,
            obligated_party=party,
            action_verb=verb,
            beneficiary=beneficiary,
            modal_trigger=modal_trigger,
            confidence=confidence,
            financial_params=financial,
            action=action_phrase,
            agent_score=agent_score,
            action_score=action_score,
            requires_review=force_review,
        )
        records.append(record)

    return _dedupe_records(records)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _find_primary_modal(text: str) -> tuple[str, re.Match | None]:
    for pattern, name in _RANKED_MODALS:
        m = pattern.search(text)
        if m:
            return (m.group(0), m)
    return ("", None)


def _has_conditional(text: str) -> bool:
    for pat in _CONDITIONAL_PATTERNS:
        if pat.search(text):
            return True
    return False


def _extract_verb_from_modal(text: str, modal_match: re.Match) -> str | None:
    nlp = _get_nlp()
    start = modal_match.start()
    snippet = text[start:start + 200]
    doc = nlp(snippet)

    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            verb_lemma = token.lemma_.lower()
            if verb_lemma not in _SKIP_VERBS:
                return _extract_verb_phrase(token, snippet)

    for token in doc:
        if token.pos_ == "VERB" and token.lemma_.lower() not in _SKIP_VERBS:
            for child in token.children:
                if child.dep_ in ("aux", "auxpass") and child.text.lower() in {
                    "shall", "must", "may", "will"
                }:
                    return _extract_verb_phrase(token, snippet)

    found_modal = False
    for token in doc:
        if token.text.lower() in {"shall", "must", "may", "will", "not"}:
            found_modal = True
            continue
        if found_modal and token.pos_ == "VERB" and token.lemma_.lower() not in _SKIP_VERBS:
            return _extract_verb_phrase(token, snippet)

    return None


def _extract_verb_phrase(token, context: str) -> str:
    parts = [token.lemma_.lower()]
    for child in token.children:
        if child.dep_ in ("acomp", "xcomp", "prt", "advmod"):
            parts.append(child.text.lower())
    
    key_objects = {
        "confidentiality", "notice", "consent", "approval", "payment",
        "information", "records", "data", "access", "services",
        "insurance", "indemnification", "compensation"
    }
    for child in token.children:
        if child.dep_ == "dobj" and child.text.lower() in key_objects:
            parts.append(child.text.lower())
            
    verb_phrase = " ".join(parts).strip()
    return _apply_semantic_mapping(verb_phrase, context)


def _apply_semantic_mapping(verb_phrase: str, context: str) -> str:
    _DIRECT_MAPPINGS = {
        "hold responsible": "hold responsible",
        "maintain confidentiality": "maintain confidentiality",
        "provide notice": "provide notice",
        "obtain consent": "obtain consent",
        "seek approval": "seek approval",
        "make payment": "make payment",
        "give notice": "provide notice",
        "keep confidential": "maintain confidentiality",
    }
    if verb_phrase in _DIRECT_MAPPINGS:
        return _DIRECT_MAPPINGS[verb_phrase]
    
    _CONTEXT_MAPPINGS = {
        "treat": {
            "confidential": "maintain confidentiality",
            "secret": "maintain confidentiality",
        },
        "hold": {
            "responsible": "hold responsible",
            "harmless": "hold harmless",
            "liable": "hold liable",
        },
        "keep": {
            "confidential": "maintain confidentiality",
            "secret": "maintain confidentiality",
        },
        "disclose": {
            "not": "maintain confidentiality",
        },
    }
    
    base_verb = verb_phrase.split()[0]
    if base_verb in _CONTEXT_MAPPINGS:
        context_lower = context.lower()
        for keyword, mapping in _CONTEXT_MAPPINGS[base_verb].items():
            if keyword in context_lower:
                return mapping
                
    return verb_phrase


def _extract_verb_spacy(text: str) -> str | None:
    nlp = _get_nlp()
    doc = nlp(text[:500])
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            verb = token.lemma_.lower()
            if verb not in _SKIP_VERBS:
                return verb
    return None


def _find_first_action_verb(text: str) -> str | None:
    words = re.findall(r'\b[a-z]+\b', text.lower())
    for word in words[:30]:
        if word in _ACTION_VERBS:
            return word
    return None


def _extract_obligated_party(
    body_text: str,
    known_parties: list[str],
    entity_summary: dict,
) -> str:
    nlp = _get_nlp()
    doc = nlp(body_text[:500])

    # 1. Agent in passive
    for token in doc:
        if token.pos_ == "VERB":
            has_auxpass = any(c.dep_ in ("auxpass", "aux") and c.text.lower() in {"be", "is", "are", "was", "were", "been"} for c in token.children)
            if has_auxpass:
                for child in token.children:
                    if child.dep_ == "agent" or (child.dep_ == "prep" and child.text.lower() in {"by", "from"}):
                        for grandchild in child.children:
                            if grandchild.dep_ == "pobj":
                                agent = _get_noun_phrase(grandchild)
                                if agent.lower() not in _REJECT_SUBJECTS:
                                    validated = _validate_party(agent, known_parties)
                                    if validated:
                                        return validated

    # 2. nsubj of ROOT
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            for child in token.children:
                if child.dep_ in ("nsubj", "nsubjpass"):
                    subject = _get_noun_phrase(child)
                    if subject.lower() in _REJECT_SUBJECTS:
                        continue
                    validated = _validate_party(subject, known_parties)
                    if validated:
                        return _resolve_generic_party(validated, known_parties, body_text)

    # 3. Subject of verb with modal
    for token in doc:
        if token.pos_ == "VERB":
            has_modal = any(
                c.dep_ == "aux" and c.text.lower() in {"shall", "must", "will", "may"}
                for c in token.children
            )
            if has_modal:
                for child in token.children:
                    if child.dep_ in ("nsubj", "nsubjpass"):
                        subject = _get_noun_phrase(child)
                        if subject.lower() in _REJECT_SUBJECTS:
                            continue
                        validated = _validate_party(subject, known_parties)
                        if validated:
                            return _resolve_generic_party(validated, known_parties, body_text)

    if known_parties:
        return known_parties[0]

    text_lower = body_text.lower()
    for role in _KNOWN_ROLES:
        if role in text_lower:
            return role.title()

    return "Contracting Party"


def _get_noun_phrase(token) -> str:
    parts = []
    for child in token.children:
        if child.dep_ in ("compound", "amod", "det") and child.i < token.i:
            parts.append(child.text)
    parts.append(token.text)
    return " ".join(parts)


def _resolve_generic_party(
    generic: str,
    known_parties: list[str],
    context: str,
) -> str:
    generic_lower = generic.lower()
    if generic_lower in {"each party", "either party", "any party", "a party"}:
        if known_parties:
            return known_parties[0]
    elif generic_lower in {"the parties", "both parties", "all parties"}:
        if len(known_parties) >= 2:
            return " and ".join(known_parties[:2])
        elif known_parties:
            return known_parties[0]
    return generic


def _validate_party(
    candidate: str,
    known_parties: list[str],
) -> str | None:
    candidate = candidate.strip()
    if not candidate or len(candidate) > 50:
        return None

    words = candidate.split()
    if len(words) > 6:
        return None

    if candidate.lower() in _KNOWN_ROLES:
        return candidate.title()

    if known_parties:
        candidate_lower = candidate.lower()
        for party in known_parties:
            party_lower = party.lower()
            if candidate_lower == party_lower:
                return candidate
            if candidate_lower in party_lower:
                return party
            if party_lower in candidate_lower:
                return party

    _INANIMATE_HEAD_NOUNS = {
        "purpose", "term", "duration", "funds", "contributions", "matters",
        "vote", "meetings", "interest", "majeure", "nothing", "everything",
        "anything", "something", "provisions", "rights", "duties", "obligations",
        "amounts", "payments", "costs", "expenses", "property", "assets",
        "agreement", "contract", "notice", "consent", "business", "venture",
        "account", "capital", "value", "membership", "information",
    }
    if words and words[0][0].isupper():
        head_noun = words[-1].lower()
        if head_noun in _INANIMATE_HEAD_NOUNS:
            return None
        if words[0].lower() in {"the", "all", "any", "no", "each", "every", "such"}:
            if len(words) < 2 or not words[1][0].isupper():
                return None
        return candidate

    return None


def _extract_beneficiary(
    relations: list[dict],
    obligated_party: str,
) -> str | None:
    for rel in relations:
        if rel.get("object_label") in ("PARTY", "ORG"):
            obj = rel.get("object", "")
            if obj.lower() != obligated_party.lower():
                return obj
    return None


def _dedupe_records(records: list[ObligationRecord]) -> list[ObligationRecord]:
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for rec in records:
        key = (rec.obligated_party.lower(), rec.action_verb.lower(), rec.obligation_type)
        if key not in seen:
            seen.add(key)
            deduped.append(rec)
    return deduped


def classify_contract_obligations(
    clauses_data: list[dict],
) -> list[list[ObligationRecord]]:
    return [
        classify_obligation(
            clause_id=cd.get("clause_id", f"clause_{i}"),
            body_text=cd.get("body_text", ""),
            relations=cd.get("relations", []),
            entity_summary=cd.get("entity_summary", {}),
            clause_type=cd.get("clause_type", ""),
            heading=cd.get("heading", ""),
        )
        for i, cd in enumerate(clauses_data)
    ]
