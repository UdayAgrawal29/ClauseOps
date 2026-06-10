"""
ClauseOps — Task Generator (v2)

Combines clause classification + deontic obligation detection + normalized
dates to produce actionable TaskRecord objects with priorities and reminders.

v2 changes (from critical audit):
  - Fix 1: SENTENCE-LEVEL SCOPING — the biggest change. Instead of running
    obligation detection and date normalization on the full segment body and
    then doing a cartesian product, we now:
      1. Split body_text into sentences via spaCy's sent boundary detector
      2. Filter entities/relations to each sentence's scope
      3. Run obligation detection PER SENTENCE
      4. Run date normalization PER SENTENCE
      5. Pair obligations with dates WITHIN THE SAME SENTENCE only
    This eliminates the cartesian product explosion (Bug 1) and fixes
    paragraph-level modal detection (Bug 2).

  - Fix 5: BOILERPLATE FILTERING — clauses classified as GOVERNING_LAW,
    SEVERABILITY, COUNTERPARTS, NO_WAIVER, ENTIRE_AGREEMENT, etc. are
    filtered out because they don't create actionable compliance tasks.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from clauseops.obligation_detection.deontic_classifier import (
    ObligationRecord,
    classify_obligation,
)
from clauseops.obligation_detection.date_normalizer import (
    DeadlineRecord,
    normalize_dates_for_clause,
    extract_anchor_date,
)

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
class TaskRecord:
    """A single generated compliance task."""
    task_id: str
    contract_name: str
    clause_id: str
    clause_type: str
    title: str
    description: str
    obligated_party: str
    beneficiary: str | None
    obligation_type: str        # OBLIGATION | PROHIBITION | PERMISSION | CONDITIONAL
    due_date: date | None
    date_type: str              # ABSOLUTE | RELATIVE | RECURRING | CONDITIONAL
    priority: str               # CRITICAL | HIGH | MEDIUM | LOW
    requires_review: bool
    reminder_dates: list[date] = field(default_factory=list)
    source_text: str = ""       # original clause text (audit trail)


# ─── Task Templates ─────────────────────────────────────────────────────────
_TEMPLATES = {
    ("PAYMENT", "OBLIGATION"):        "Payment due: {party} shall {verb} {amount} by {deadline}",
    ("PAYMENT", "PROHIBITION"):       "Payment restriction: {party} must NOT {verb}",
    ("PAYMENT", "PERMISSION"):        "Payment right: {party} may {verb} {amount}",
    ("TERMINATION", "OBLIGATION"):    "Termination notice: {party} must {verb} within {duration}",
    ("TERMINATION", "PERMISSION"):    "Termination right: {party} may terminate with {duration} notice",
    ("TERMINATION", "PROHIBITION"):   "Termination restriction: {party} must NOT terminate",
    ("RENEWAL", "OBLIGATION"):        "Renewal deadline: Notify {party} {duration} before expiration",
    ("RENEWAL", "PERMISSION"):        "Renewal option: {party} may renew for {duration}",
    ("DELIVERY_OBLIGATIONS", "OBLIGATION"): "Delivery due: {party} shall {verb} within {duration}",
    ("REPORTING_AUDIT", "OBLIGATION"): "Report due: {party} shall {verb} by {deadline}",
    ("CONFIDENTIALITY", "OBLIGATION"): "Confidentiality: {party} must maintain for {duration}",
    ("CONFIDENTIALITY", "PROHIBITION"): "Confidentiality: {party} must NOT disclose",
    ("INDEMNIFICATION", "OBLIGATION"): "Indemnification: {party} shall indemnify {beneficiary}",
    ("ASSIGNMENT", "OBLIGATION"):      "Assignment: {party} shall {verb}",
    ("ASSIGNMENT", "PROHIBITION"):     "Assignment restriction: {party} must NOT assign",
    ("WARRANTIES", "OBLIGATION"):      "Warranty: {party} {verb}",
}

# Fallback templates for any clause type
_FALLBACK_TEMPLATES = {
    "OBLIGATION": "Obligation: {party} shall {verb} {amount} within {duration}",
    "PROHIBITION": "Prohibition: {party} must NOT {verb}",
    "PERMISSION": "Right: {party} may {verb}",
    "CONDITIONAL": "Review needed: Conditional obligation — {party} may need to {verb}",
    "RECURRING": "Recurring obligation: {party} shall {verb} ({recurrence})",
}

# ─── Fix 5: Extended skip types for boilerplate clauses ──────────────────────
_SKIP_CLAUSE_TYPES = {
    # Original skip types
    "PREAMBLE", "DEFINITIONS", "DEFINITION_GROUP", "SIGNATURE_BLOCK",
    # Fix 5: Boilerplate clause types that don't create actionable tasks
    "GOVERNING_LAW", "SEVERABILITY", "COUNTERPARTS", "NO_WAIVER",
    "ENTIRE_AGREEMENT", "CONSTRUCTION", "CAPTIONS", "HEADINGS",
    "INTERPRETATION", "RELATIONSHIP_OF_PARTIES", "INDEPENDENT_CONTRACTORS",
    "THIRD_PARTY_BENEFICIARIES", "NO_THIRD_PARTY_BENEFICIARIES",
    "AMENDMENT", "MODIFICATION", "WAIVER",
    # Common alias variations
    "CHOICE_OF_LAW", "APPLICABLE_LAW", "JURISDICTION",
}


def _compute_priority(
    clause_type: str,
    obligation_type: str,
    due_date: date | None,
    date_type: str,
) -> str:
    """
    Compute task priority based on clause type + obligation type + urgency.

    Priority cascade:
      CRITICAL:  PAYMENT + OBLIGATION + deadline < 30 days
      HIGH:      TERMINATION/RENEWAL + OBLIGATION + any deadline
      HIGH:      Any PROHIBITION
      MEDIUM:    DELIVERY/REPORTING + OBLIGATION + deadline
      MEDIUM:    Any CONDITIONAL
      MEDIUM:    RECURRING obligations
      LOW:       PERMISSION
    """
    ct = clause_type.upper()

    # PROHIBITION is always HIGH
    if obligation_type == "PROHIBITION":
        return "HIGH"

    # PERMISSION is always LOW
    if obligation_type == "PERMISSION":
        return "LOW"

    # CONDITIONAL is always MEDIUM
    if obligation_type == "CONDITIONAL":
        return "MEDIUM"

    # Check deadline urgency for PAYMENT obligations
    if ct == "PAYMENT" and obligation_type == "OBLIGATION" and due_date:
        days_until = (due_date - date.today()).days
        if days_until < 30:
            return "CRITICAL"
        return "HIGH"

    # TERMINATION/RENEWAL obligations are HIGH
    if ct in {"TERMINATION", "RENEWAL"} and obligation_type == "OBLIGATION":
        return "HIGH"

    # DELIVERY/REPORTING obligations are MEDIUM
    if ct in {"DELIVERY_OBLIGATIONS", "REPORTING_AUDIT"} and obligation_type == "OBLIGATION":
        return "MEDIUM"

    # RECURRING
    if date_type == "RECURRING":
        return "MEDIUM"

    # Default for OBLIGATION with any clause type
    if obligation_type == "OBLIGATION":
        return "MEDIUM"

    return "LOW"


def _compute_reminders(due_date: date | None) -> list[date]:
    """
    Compute cascading reminder dates (industry standard: 90/30/7/1 days before).
    Only includes future dates.
    """
    if not due_date:
        return []

    offsets = [90, 30, 7, 1]
    today = date.today()
    reminders = []
    for offset in offsets:
        reminder = due_date - timedelta(days=offset)
        if reminder >= today:
            reminders.append(reminder)
    return reminders


# ─── Title Formatting ────────────────────────────────────────────────────────
def _format_title(
    clause_type: str,
    obligation: ObligationRecord,
    deadline: DeadlineRecord | None,
) -> str:
    """Format a human-readable task title from template."""
    ct = clause_type.upper()
    ot = obligation.obligation_type

    # Look up template
    template = _TEMPLATES.get((ct, ot))
    if not template:
        if deadline and deadline.date_type == "RECURRING":
            template = _FALLBACK_TEMPLATES.get("RECURRING", "")
        else:
            template = _FALLBACK_TEMPLATES.get(ot, "Task: {party} — {verb}")

    # Build substitution values
    amount = ""
    if obligation.financial_params:
        amount = ", ".join(obligation.financial_params)

    duration = ""
    deadline_str = ""
    recurrence = ""
    if deadline:
        duration = deadline.raw_text
        if deadline.normalized_date:
            deadline_str = deadline.normalized_date.isoformat()
        else:
            deadline_str = deadline.raw_text
        if deadline.recurrence_description:
            recurrence = deadline.recurrence_description

    beneficiary = obligation.beneficiary or ""

    title = template.format(
        party=obligation.obligated_party,
        verb=obligation.action_verb,
        amount=amount,
        duration=duration,
        deadline=deadline_str,
        beneficiary=beneficiary,
        recurrence=recurrence,
    )

    # Clean up empty placeholders
    title = title.replace("  ", " ").replace(" by ", " ").replace(" within ", " ") if not duration and not deadline_str else title
    return title.strip()


def _build_description(
    obligation: ObligationRecord,
    deadline: DeadlineRecord | None,
    body_text: str,
) -> str:
    """Build a detailed task description."""
    parts = []

    parts.append(f"Obligation Type: {obligation.obligation_type}")
    parts.append(f"Obligated Party: {obligation.obligated_party}")
    parts.append(f"Action: {obligation.action_verb}")

    if obligation.beneficiary:
        parts.append(f"Beneficiary: {obligation.beneficiary}")

    if obligation.financial_params:
        parts.append(f"Financial: {', '.join(obligation.financial_params)}")

    parts.append(f"Modal Trigger: \"{obligation.modal_trigger}\"")
    parts.append(f"Confidence: {obligation.confidence:.0%}")

    if deadline:
        parts.append(f"---")
        parts.append(f"Date Type: {deadline.date_type}")
        parts.append(f"Raw Duration: \"{deadline.raw_text}\"")
        if deadline.normalized_date:
            parts.append(f"Resolved Date: {deadline.normalized_date.isoformat()}")
        if deadline.anchor_date:
            parts.append(f"Anchor Date: {deadline.anchor_date.isoformat()}")
        if deadline.recurrence_description:
            parts.append(f"Recurrence: {deadline.recurrence_description}")
        parts.append(f"Label: {deadline.deadline_label}")

    if deadline and deadline.requires_review:
        parts.append(f"⚠️ REQUIRES HUMAN REVIEW — date could not be resolved")

    # Source text (truncated for readability)
    if body_text:
        truncated = body_text[:300] + "..." if len(body_text) > 300 else body_text
        parts.append(f"---")
        parts.append(f"Source: {truncated}")

    return "\n".join(parts)


# ─── Sentence-level entity/relation filtering (Fix 1) ───────────────────────

def _entities_in_sentence(entities: list[dict], sentence_text: str) -> list[dict]:
    """
    Filter entities to only those whose text appears in the given sentence.
    
    This is the core of Fix 1: instead of associating ALL entities with ALL
    obligations in a segment, we only associate entities that are textually
    present in the same sentence.
    """
    sent_lower = sentence_text.lower()
    result = []
    for ent in entities:
        ent_text = ent.get("text", "")
        if ent_text and ent_text.lower() in sent_lower:
            result.append(ent)
    return result


def _relations_in_sentence(relations: list[dict], sentence_text: str) -> list[dict]:
    """
    Filter relations to only those whose subject AND verb appear in the sentence.
    """
    sent_lower = sentence_text.lower()
    result = []
    for rel in relations:
        subject = rel.get("subject", "").lower()
        verb = rel.get("verb", "").lower()
        # Both subject and verb must appear in the sentence
        if subject and verb and subject in sent_lower and verb in sent_lower:
            result.append(rel)
    return result


def _entity_summary_for_sentence(
    entity_summary: dict, sentence_text: str
) -> dict:
    """
    Filter entity_summary to only entities whose text appears in the sentence,
    but ALWAYS keep the full PARTY list (parties may be referenced implicitly).
    """
    sent_lower = sentence_text.lower()
    result = {}
    
    for label, values in entity_summary.items():
        if label == "PARTY":
            # Always keep full PARTY list — parties are often implicitly referenced
            result["PARTY"] = values
            continue
        
        if isinstance(values, list):
            filtered = []
            for v in values:
                if isinstance(v, str) and v.lower() in sent_lower:
                    filtered.append(v)
                elif isinstance(v, list):
                    # Nested list
                    for inner in v:
                        if isinstance(inner, str) and inner.lower() in sent_lower:
                            filtered.append(inner)
            if filtered:
                result[label] = filtered
        elif isinstance(values, str):
            if values.lower() in sent_lower:
                result[label] = values
    
    # Ensure PARTY key exists even if empty in source
    if "PARTY" not in result:
        result["PARTY"] = entity_summary.get("PARTY", [])
    
    return result

# ─── Sentence quality filter ────────────────────────────────────────────────
_BOILERPLATE_SENTENCE_PATTERNS = [
    # Definitions: "X means ...", "X is defined as ...", "As used herein ..."
    re.compile(r"^\s*[\"'\u201c]?[A-Z].*?\b(?:means?|refers?\s+to|is\s+defined\s+as)\b", re.I),
    re.compile(r"^\s*(?:As\s+used\s+(?:herein|in\s+this))", re.I),
    # Pure cross-references: "See Section X", "Pursuant to Section X"
    re.compile(r"^\s*(?:See|Refer\s+to|Pursuant\s+to)\s+Section", re.I),
    # Section headers that leaked as sentences
    re.compile(r"^\s*(?:Section|Article|Exhibit|Schedule|Annex)\s+\d", re.I),
    # Boilerplate conclusions: "This Agreement constitutes ...", "IN WITNESS WHEREOF ..."
    re.compile(r"^\s*(?:This\s+Agreement\s+(?:constitutes|represents|embodies|sets\s+forth))", re.I),
    re.compile(r"^\s*IN\s+WITNESS\s+WHEREOF", re.I),
    re.compile(r"^\s*(?:EXECUTED|SIGNED)\s+(?:as\s+of|this)", re.I),
    # Pure descriptive: "The following ...", "For purposes of ..."
    re.compile(r"^\s*(?:The\s+following|For\s+(?:purposes|the\s+purposes)\s+of)", re.I),
    # Currently/herein/hereby declaratives without modal
    re.compile(r"^\s*(?:Currently\s+this\s+means)", re.I),
]

# Modal indicators — a sentence must contain at least one to be actionable
_MODAL_INDICATORS = re.compile(
    r"\b(?:shall|must|may|will|agrees?\s+to|is\s+required\s+to|"
    r"are\s+required\s+to|covenants?\s+to|undertakes?\s+to|"
    r"is\s+obligated\s+to|is\s+entitled\s+to|has\s+the\s+right\s+to|"
    r"is\s+prohibited\s+from|are\s+prohibited\s+from|"
    r"is\s+permitted\s+to|are\s+permitted\s+to)\b",
    re.I,
)


def _is_actionable_sentence(sent_text: str) -> bool:
    """
    Determine if a sentence is likely to contain an actionable obligation.
    
    Filters out:
    - Pure definitions ("X means ...")
    - Cross-references ("See Section 5")
    - Section headers ("Section 10.1")
    - Boilerplate conclusions ("IN WITNESS WHEREOF")
    - Sentences without any modal/obligation indicator
    
    This is critical for controlling task volume after sentence splitting.
    Without this filter, a 10-sentence paragraph would generate 10 separate
    obligations even though most sentences are just context/definitions.
    """
    # Skip boilerplate patterns
    for pattern in _BOILERPLATE_SENTENCE_PATTERNS:
        if pattern.search(sent_text):
            return False
    
    # Must contain at least one modal indicator to be actionable
    if not _MODAL_INDICATORS.search(sent_text):
        return False
    
    return True


# ─── Main Task Generation (v2 — sentence-level) ─────────────────────────────
def generate_tasks_for_clause(
    clause_id: str,
    body_text: str,
    clause_type: str,
    entities: list[dict],
    relations: list[dict],
    entity_summary: dict,
    contract_name: str,
    anchor_date: date | None,
) -> list[TaskRecord]:
    """
    Generate tasks for a single clause.

    v2 architecture (Fix 1 — Sentence-Level Scoping):
      1. Split body_text into sentences via spaCy
      2. For each sentence:
         a. Filter entities/relations to that sentence
         b. Run deontic classification on that sentence
         c. Run date normalization on that sentence's entities
         d. Pair obligations with deadlines WITHIN that sentence
      3. Deduplicate across all sentences
      
    This eliminates the cartesian product explosion where obligations
    from sentence A were paired with dates from sentence B.
    """
    # Fix 5: Skip boilerplate clause types
    if clause_type.upper() in _SKIP_CLAUSE_TYPES:
        return []

    if not body_text or not body_text.strip():
        return []

    # Fix F: Handle empty/unknown clause types — check for preamble/recital
    if not clause_type or clause_type.strip() == "":
        body_upper = body_text.strip().upper()[:200]
        # Skip preamble/recital segments that have no clause type
        if any(marker in body_upper for marker in [
            "WHEREAS", "RECITALS", "WITNESSETH", "IN WITNESS WHEREOF",
            "NOW, THEREFORE", "NOW THEREFORE",
        ]):
            return []

    # Split into sentences using spaCy
    nlp = _get_nlp()
    doc = nlp(body_text)
    sentences = list(doc.sents)

    if not sentences:
        return []

    all_tasks: list[TaskRecord] = []

    for sent in sentences:
        sent_text = sent.text.strip()
        if len(sent_text) < 10:  # Skip tiny fragments
            continue
        
        # Skip non-actionable sentences (definitions, cross-refs, boilerplate)
        if not _is_actionable_sentence(sent_text):
            continue

        # Filter entities/relations to this sentence's scope
        sent_entities = _entities_in_sentence(entities, sent_text)
        sent_relations = _relations_in_sentence(relations, sent_text)
        sent_entity_summary = _entity_summary_for_sentence(entity_summary, sent_text)

        # Run obligation classification on THIS sentence only
        obligations = classify_obligation(
            clause_id=clause_id,
            body_text=sent_text,
            relations=sent_relations,
            entity_summary=sent_entity_summary,
            clause_type=clause_type,
        )

        if not obligations:
            continue

        # Run date normalization on THIS sentence's entities only
        deadlines = normalize_dates_for_clause(
            clause_id=clause_id,
            entities=sent_entities,
            body_text=sent_text,
            anchor_date=anchor_date,
            clause_type=clause_type,
        )

        # Generate tasks — pair obligations with deadlines from SAME sentence
        if deadlines:
            for obligation in obligations:
                for deadline in deadlines:
                    task = _create_task(
                        clause_id=clause_id,
                        clause_type=clause_type,
                        obligation=obligation,
                        deadline=deadline,
                        contract_name=contract_name,
                        body_text=sent_text,  # Use sentence text, not full body
                    )
                    all_tasks.append(task)
        else:
            # No deadlines in this sentence — generate task without date
            for obligation in obligations:
                task = _create_task(
                    clause_id=clause_id,
                    clause_type=clause_type,
                    obligation=obligation,
                    deadline=None,
                    contract_name=contract_name,
                    body_text=sent_text,
                )
                all_tasks.append(task)

    return _dedupe_tasks(all_tasks)


def _create_task(
    clause_id: str,
    clause_type: str,
    obligation: ObligationRecord,
    deadline: DeadlineRecord | None,
    contract_name: str,
    body_text: str,
) -> TaskRecord:
    """Create a single TaskRecord from an obligation + deadline pair."""
    due_date = deadline.normalized_date if deadline else None
    date_type = deadline.date_type if deadline else "NONE"
    requires_review = deadline.requires_review if deadline else False

    priority = _compute_priority(clause_type, obligation.obligation_type, due_date, date_type)
    reminders = _compute_reminders(due_date)
    title = _format_title(clause_type, obligation, deadline)
    description = _build_description(obligation, deadline, body_text)

    return TaskRecord(
        task_id=str(uuid.uuid4())[:8],
        contract_name=contract_name,
        clause_id=clause_id,
        clause_type=clause_type,
        title=title,
        description=description,
        obligated_party=obligation.obligated_party,
        beneficiary=obligation.beneficiary,
        obligation_type=obligation.obligation_type,
        due_date=due_date,
        date_type=date_type,
        priority=priority,
        requires_review=requires_review,
        reminder_dates=reminders,
        source_text=body_text[:500] if body_text else "",
    )


def _dedupe_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
    """
    Remove duplicate tasks (Fix E: enhanced dedup).
    
    Dedup key now includes a source_text hash so that two tasks from the
    same sentence with the same verb but different obligation types
    (e.g., OBLIGATION + PERMISSION from same sentence) are caught.
    """
    seen: set[tuple] = set()
    deduped = []
    for task in tasks:
        # Primary key: party + obligation_type + verb (from title)
        key = (
            task.obligated_party.lower(),
            task.obligation_type,
            task.due_date,
            task.date_type,
        )
        # Secondary key: source text hash (catch same-sentence dups)
        source_key = (
            task.source_text[:100] if task.source_text else "",
            task.obligated_party.lower(),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(task)
        elif source_key not in seen:
            # Different obligation type from same sentence — allow if
            # it's a genuinely different modality (e.g., OBLIGATION vs CONDITIONAL)
            seen.add(source_key)
            deduped.append(task)
    return deduped


# ─── Contract-Level Generation ──────────────────────────────────────────────
def generate_tasks_for_contract(
    clauses_data: list[dict],
    contract_name: str,
    anchor_date: date | None = None,
) -> list[TaskRecord]:
    """
    Generate all tasks for an entire contract.

    Parameters
    ----------
    clauses_data : list[dict]
        Each dict must have:
          - clause_id: str
          - body_text: str
          - clause_type: str (from classifier)
          - entities: list[dict] (from NER)
          - relations: list[dict] (from NER)
          - entity_summary: dict (from NER)

    contract_name : str
        Name of the contract file.

    anchor_date : date | None
        Contract signing/effective date. Auto-detected if None.

    Returns
    -------
    list[TaskRecord]
        All tasks for the contract, sorted by priority.
    """
    # Auto-detect anchor date if not provided
    if anchor_date is None:
        anchor_date = extract_anchor_date(clauses_data)

    all_tasks: list[TaskRecord] = []

    for i, cd in enumerate(clauses_data):
        try:
            tasks = generate_tasks_for_clause(
                clause_id=cd.get("clause_id", f"clause_{i}"),
                body_text=cd.get("body_text", ""),
                clause_type=cd.get("clause_type", ""),
                entities=cd.get("entities", []),
                relations=cd.get("relations", []),
                entity_summary=cd.get("entity_summary", {}),
                contract_name=contract_name,
                anchor_date=anchor_date,
            )
            all_tasks.extend(tasks)
        except Exception as exc:
            logger.error("Task generation failed for clause %s: %s",
                         cd.get("clause_id", f"clause_{i}"), exc)

    # Sort by priority: CRITICAL > HIGH > MEDIUM > LOW
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    all_tasks.sort(key=lambda t: priority_order.get(t.priority, 99))

    return all_tasks
