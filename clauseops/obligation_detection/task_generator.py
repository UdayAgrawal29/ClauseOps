"""
ClauseOps — Task Generator (v4 — Clause-Level Architecture)

ARCHITECTURE CHANGE: v2→v4
  v2 split each clause into SENTENCES and ran obligation detection per-sentence,
  creating N×M task explosion (N sentences × M obligations). A single IP clause
  produced 12 tasks.

  v4 treats the CLAUSE as the atomic unit:
    1. Run obligation detection on the FULL clause body → 0 or 1 obligations
    2. For multi-obligation clauses with (a),(b),(c) sub-sections, detect
       sub-obligations with DISTINCT subjects/verbs → max 3 tasks per clause
    3. Run date normalization on the full clause's entities
    4. Generate tasks: 1 obligation × best deadline = 1 task

  What was DELETED:
    - Sentence splitting loop
    - _is_actionable_sentence()
    - _BOILERPLATE_SENTENCE_PATTERNS
    - _MODAL_INDICATORS
    - _entities_in_sentence(), _relations_in_sentence(), _entity_summary_for_sentence()

  What was KEPT:
    - TaskRecord dataclass
    - _TEMPLATES, _FALLBACK_TEMPLATES
    - _compute_priority(), _compute_reminders()
    - _format_title(), _build_description()
    - _create_task()
    - generate_tasks_for_contract()
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
from clauseops.obligation_detection.config import (
    TaskGenerationConfig,
    DEFAULT_CONFIG,
)

logger = logging.getLogger(__name__)


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
    source_text: str = ""       # original clause text (audit trail / grounding)
    confidence: float = 0.0     # modality classifier confidence (M2)
    agent_score: float = 0.0    # QA agent-span score (M2)
    action: str = ""            # verbatim action span for THIS task (grounds in source_text)


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

_FALLBACK_TEMPLATES = {
    "OBLIGATION": "Obligation: {party} shall {verb} {amount} within {duration}",
    "PROHIBITION": "Prohibition: {party} must NOT {verb}",
    "PERMISSION": "Right: {party} may {verb}",
    "CONDITIONAL": "Review needed: Conditional obligation — {party} may need to {verb}",
    "RECURRING": "Recurring obligation: {party} shall {verb} ({recurrence})",
}

# ─── Clause types to skip ───────────────────────────────────────────────────
# Phase B: classification is ADVISORY. Skip only structural / never-actionable
# boilerplate. Removed (now processed; modality classifier decides):
#   ENTIRE_AGREEMENT (catch-all holding Grant/Delivery/Survival/Further-Assurances),
#   RELATIONSHIP_OF_PARTIES, INDEPENDENT_CONTRACTORS, THIRD_PARTY_BENEFICIARIES,
#   NO_THIRD_PARTY_BENEFICIARIES, AMENDMENT, MODIFICATION, WAIVER.
_SKIP_CLAUSE_TYPES = {
    "PREAMBLE", "DEFINITIONS", "DEFINITION_GROUP", "SIGNATURE_BLOCK",
    "GOVERNING_LAW", "SEVERABILITY", "COUNTERPARTS", "NO_WAIVER",
    "CONSTRUCTION", "CAPTIONS", "HEADINGS", "INTERPRETATION",
    "CHOICE_OF_LAW", "APPLICABLE_LAW", "JURISDICTION",
}

# ─── Sub-section detection pattern ──────────────────────────────────────────
# Matches: (a), (b), (c), (i), (ii), (iii), (1), (2), etc.
_SUBSECTION_RE = re.compile(
    r"(?:^|\n)\s*\(([a-z]|[ivx]+|\d+)\)\s",
    re.MULTILINE,
)

# Hard cap: max tasks per clause (prevents explosion)
_MAX_TASKS_PER_CLAUSE = 3


# ═══════════════════════════════════════════════════════════════════════════════
# PRIORITY, REMINDERS, FORMATTING (kept from v2 — these work fine)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_priority(
    clause_type: str,
    obligation_type: str,
    due_date: date | None,
    date_type: str,
) -> str:
    """Compute task priority based on clause type + obligation type + urgency."""
    ct = clause_type.upper()

    if obligation_type == "PROHIBITION":
        return "HIGH"
    if obligation_type == "PERMISSION":
        return "LOW"
    if obligation_type == "CONDITIONAL":
        return "MEDIUM"

    if ct == "PAYMENT" and obligation_type == "OBLIGATION" and due_date:
        days_until = (due_date - date.today()).days
        if days_until < 30:
            return "CRITICAL"
        return "HIGH"

    if ct in {"TERMINATION", "RENEWAL"} and obligation_type == "OBLIGATION":
        return "HIGH"

    if ct in {"DELIVERY_OBLIGATIONS", "REPORTING_AUDIT"} and obligation_type == "OBLIGATION":
        return "MEDIUM"

    if date_type == "RECURRING":
        return "MEDIUM"

    if obligation_type == "OBLIGATION":
        return "MEDIUM"

    return "LOW"


def _compute_reminders(due_date: date | None) -> list[date]:
    """Compute cascading reminder dates (90/30/7/1 days before)."""
    if not due_date:
        return []
    offsets = [90, 30, 7, 1]
    today = date.today()
    return [due_date - timedelta(days=o) for o in offsets if due_date - timedelta(days=o) >= today]


# ═══════════════════════════════════════════════════════════════════════════════
# M2 HELPERS — party hygiene, deadline association, action/title cleanup
# ═══════════════════════════════════════════════════════════════════════════════

_DANGLING_TAIL_RE = re.compile(r"\s+(?:by|within|for|to|on|of|with|until|before)\s*$", re.I)


def _clean_party(party: str) -> str:
    """Light hygiene on an extracted party string (strip bracket/quote/punct
    artifacts from segmentation/OCR). Does not attempt deep entity repair."""
    if not party:
        return party
    p = re.sub(r"\s+", " ", party).strip()
    p = p.strip(" \t\r\n.,;:\"'[](){}")
    return p or party.strip()


def _clamp_action(action: str, max_words: int = 12) -> str:
    """Clamp a long action span to a readable title length (full text stays in
    the description). Cuts at a word boundary and appends an ellipsis."""
    if not action:
        return action
    words = action.split()
    if len(words) <= max_words:
        return action
    return " ".join(words[:max_words]) + "…"


def _sentence_window(text: str, idx: int, radius: int = 400) -> str:
    """Return the sentence-ish window of `text` surrounding character `idx`."""
    if idx < 0:
        return text[:radius]
    start = text.rfind(". ", 0, idx)
    start = 0 if start < 0 else start + 2
    end = text.find(". ", idx)
    end = len(text) if end < 0 else end + 1
    lo = max(start, idx - radius)
    hi = min(end, idx + radius)
    return text[lo:hi]


def _associate_deadline(
    obligation: ObligationRecord,
    deadlines: list[DeadlineRecord],
    body_text: str,
):
    """Attach the deadline that semantically belongs to THIS obligation.

    Strategy (Property: deadline locality):
      1. Prefer a deadline whose raw text appears in the same sentence as the
         obligation's action span.
      2. Else the nearest deadline within the same unit (<= 350 chars).
      3. Else None — never borrow a deadline from a different obligation.
    """
    if not deadlines:
        return None

    action = (obligation.action or obligation.action_verb or "").strip()
    a_idx = body_text.find(action) if action and len(action) > 3 else -1
    if a_idx < 0 and obligation.obligated_party:
        a_idx = body_text.find(obligation.obligated_party)

    if a_idx < 0:
        # Can't locate the obligation; only attach when unambiguous.
        return deadlines[0] if len(deadlines) == 1 else None

    sent = _sentence_window(body_text, a_idx)
    same_sentence = [d for d in deadlines if d.raw_text and d.raw_text in sent]
    if same_sentence:
        return _pick_best_deadline(same_sentence)

    best, best_dist = None, 10 ** 9
    for d in deadlines:
        if not d.raw_text:
            continue
        di = body_text.find(d.raw_text)
        if di < 0:
            continue
        dist = abs(di - a_idx)
        if dist < best_dist:
            best, best_dist = d, dist
    if best is not None and best_dist <= 350:
        return best
    return None


def _is_action_abstained(obligation: ObligationRecord) -> bool:
    """True when the QA extractor produced no real action span (placeholder)."""
    action = (obligation.action or "").strip()
    verb = (obligation.action_verb or "").strip().lower()
    return action == "" or verb in ("", "perform")


# Tokens that signal an extracted "party" span is actually a clause fragment,
# not a noun-phrase party (QA span errors on awkward sentences).
_NON_PARTY_TOKENS = re.compile(
    r"\b(?:if|when|unless|because|whereas|accesses|provided|shall|will|may)\b",
    re.I,
)


def _is_plausible_party(party: str) -> bool:
    """Heuristic NP-shape check: a real party is a short noun phrase, not a
    clause fragment. Used to route low-quality agents to review (precision),
    NOT to fabricate or silently drop."""
    if not party:
        return False
    p = party.strip()
    words = p.split()
    if len(words) > 8:
        return False
    if _NON_PARTY_TOKENS.search(p):
        return False
    return True


def _format_title(
    clause_type: str,
    obligation: ObligationRecord,
    deadline: DeadlineRecord | None,
) -> str:
    """Format a human-readable, modality-driven task title.

    M2: titles are driven by the (reliable) modality + grounded action span,
    NOT by the clause-type templates — classification is imperfect and was
    producing misleading prefixes (e.g. "Payment due" on a reporting duty).
    The clause_type is still retained on the TaskRecord for priority/filtering.
    """
    party = _clean_party(obligation.obligated_party) or "the party"

    # Action abstained → explicit review title (never emit "shall perform").
    if _is_action_abstained(obligation):
        return f"Review: {party} — obligation present, action unclear"

    action = _clamp_action((obligation.action or obligation.action_verb or "").strip())
    ot = obligation.obligation_type

    if ot == "PROHIBITION":
        core = f"{party} must not {action}"
    elif ot == "PERMISSION":
        core = f"{party} may {action}"
    elif ot == "CONDITIONAL":
        core = f"Conditional — {party} {action}"
    else:  # OBLIGATION
        core = f"{party} shall {action}"

    # Deadline suffix (only when we actually have one).
    suffix = ""
    if deadline:
        if deadline.date_type == "RECURRING" and deadline.recurrence_description:
            suffix = f" ({deadline.recurrence_description})"
        elif deadline.normalized_date:
            suffix = f" — by {deadline.normalized_date.isoformat()}"
        elif deadline.requires_review and deadline.raw_text:
            suffix = f" — within {deadline.raw_text} (review)"
        elif deadline.raw_text:
            suffix = f" — within {deadline.raw_text}"

    title = re.sub(r"\s+", " ", (core + suffix)).strip()
    # Defensive: never end on a dangling connector.
    title = _DANGLING_TAIL_RE.sub("", title).strip()
    return title


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
        parts.append("---")
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
        parts.append("⚠️ REQUIRES HUMAN REVIEW — date could not be resolved")

    if body_text:
        truncated = body_text[:300] + "..." if len(body_text) > 300 else body_text
        parts.append("---")
        parts.append(f"Source: {truncated}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# v4 CORE: CLAUSE-LEVEL TASK GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_sub_sections(body_text: str) -> list[str]:
    """
    Detect (a), (b), (c) style sub-sections in a clause body.
    Returns list of sub-section texts. If no sub-sections found,
    returns empty list (meaning treat the whole clause as one unit).
    """
    matches = list(_SUBSECTION_RE.finditer(body_text))

    # Need at least 2 sub-sections to justify splitting
    if len(matches) < 2:
        return []

    sections = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body_text)
        section_text = body_text[start:end].strip()
        # Only keep sections with substantial text (> 30 chars)
        if len(section_text) > 30:
            sections.append(section_text)

    return sections


def generate_tasks_for_clause(
    clause_id: str,
    body_text: str,
    clause_type: str,
    entities: list[dict],
    relations: list[dict],
    entity_summary: dict,
    contract_name: str,
    anchor_date: date | None,
    config: TaskGenerationConfig | None = None,
    heading: str = "",
) -> list[TaskRecord]:
    """
    Generate tasks for a single clause (M2: obligation-centric).

    One task per DISTINCT obligation the classifier finds (deduped), with each
    deadline associated to the obligation it actually modifies (same-sentence /
    nearest-within-unit). No longer collapses a multi-obligation clause to a
    single task, and no longer borrows a deadline across obligations.
    """
    if config is None:
        config = DEFAULT_CONFIG

    if clause_type.upper() in _SKIP_CLAUSE_TYPES:
        return []
    if not body_text or not body_text.strip():
        return []

    obligations = classify_obligation(
        clause_id=clause_id,
        body_text=body_text,
        relations=relations,
        entity_summary=entity_summary,
        clause_type=clause_type,
        heading=heading,
    )
    if not obligations:
        return []

    obligations = _dedupe_obligations(obligations)

    deadlines = normalize_dates_for_clause(
        clause_id=clause_id,
        entities=entities,
        body_text=body_text,
        anchor_date=anchor_date,
        clause_type=clause_type,
    )

    tasks: list[TaskRecord] = []
    for obl in obligations[:config.max_tasks_per_clause]:
        deadline = _associate_deadline(obl, deadlines, body_text)
        tasks.append(_create_task(
            clause_id=clause_id,
            clause_type=clause_type,
            obligation=obl,
            deadline=deadline,
            contract_name=contract_name,
            body_text=body_text,
        ))

    return _dedupe_tasks(tasks)


def _pick_best_deadline(deadlines: list[DeadlineRecord]) -> DeadlineRecord | None:
    """
    Pick the most specific/relevant deadline from a list.
    Priority: ABSOLUTE > RELATIVE > RECURRING > CONDITIONAL
    """
    if not deadlines:
        return None

    priority = {"ABSOLUTE": 0, "RELATIVE": 1, "RECURRING": 2, "CONDITIONAL": 3}
    sorted_deadlines = sorted(deadlines, key=lambda d: priority.get(d.date_type, 99))
    return sorted_deadlines[0]


def _dedupe_obligations(obligations: list[ObligationRecord]) -> list[ObligationRecord]:
    """Deduplicate obligations by party + action-prefix + type.

    M2: keys on the action span (first 60 chars), not just the verb, so two
    genuinely distinct duties that share a verb (e.g. "reimburse … printing
    costs" vs "reimburse … customer-service costs") are both kept.
    """
    seen = set()
    deduped = []
    for obl in obligations:
        action = (obl.action or obl.action_verb or "").lower().strip()
        key = (obl.obligated_party.lower().strip(), action[:60], obl.obligation_type)
        if key not in seen:
            seen.add(key)
            deduped.append(obl)
    return deduped


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
    deadline_review = deadline.requires_review if deadline else False
    # M2: an obligation whose action the QA model abstained on is kept but
    # flagged for human review rather than emitting a vague "perform" task.
    # M4 (B3): obligations with an INFERRED (passive) agent are also flagged.
    requires_review = (
        deadline_review
        or _is_action_abstained(obligation)
        or getattr(obligation, "requires_review", False)
    )

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
        obligated_party=_clean_party(obligation.obligated_party),
        beneficiary=obligation.beneficiary,
        obligation_type=obligation.obligation_type,
        due_date=due_date,
        date_type=date_type,
        priority=priority,
        requires_review=requires_review,
        reminder_dates=reminders,
        # Store the FULL clause body so each task's own action/party grounds (highlights)
        # correctly. Truncating here broke grounding for long, multi-obligation clauses.
        source_text=body_text or "",
        confidence=obligation.confidence,
        agent_score=obligation.agent_score,
        # Carry THIS obligation's verbatim action so persistence doesn't have to guess which
        # obligation a task came from (the guess collapsed multi-obligation clauses to a
        # single shared action span).
        action=(obligation.action or obligation.action_verb or ""),
    )


def _dedupe_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
    """Remove duplicate tasks without collapsing genuinely distinct obligations.

    Keys on the obligation's identity: party + type + the FULL action span +
    due_date. The action is no longer truncated for the key (the human title is
    clamped to ~12 words, so keying on the title silently merged two distinct
    duties that shared a 12-word prefix but differed later — e.g. same action
    with different amounts or deadlines). Using the full action and the due_date
    keeps real duplicates collapsing while preserving distinct duties.
    """
    seen = set()
    deduped = []
    for task in tasks:
        action = (getattr(task, "action", "") or "").strip().lower()
        # Fall back to the title only when no action span is available.
        identity = action or task.title.strip().lower()
        due = task.due_date.isoformat() if task.due_date else ""
        key = (
            task.obligated_party.lower().strip(),
            task.obligation_type,
            identity,
            due,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(task)
    return deduped


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT-LEVEL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_tasks_for_contract(
    clauses_data: list[dict],
    contract_name: str,
    anchor_date: date | None = None,
    config: TaskGenerationConfig | None = None,
) -> list[TaskRecord]:
    """
    Generate all tasks for an entire contract.

    Parameters
    ----------
    clauses_data : list[dict]
        Each dict must have: clause_id, body_text, clause_type,
        entities, relations, entity_summary.

    contract_name : str
        Name of the contract file.

    anchor_date : date | None
        Contract signing/effective date. Auto-detected if None.
    
    config : TaskGenerationConfig | None
        Configuration for task generation. Uses DEFAULT_CONFIG if None.
        
    Returns
    -------
    list[TaskRecord]
        Filtered and deduplicated list of tasks.
        
    v4.1 IMPROVEMENTS:
    - Configurable PERMISSION filtering (default: exclude)
    - Confidence thresholding
    - Priority filtering
    """
    if config is None:
        config = DEFAULT_CONFIG
    
    if anchor_date is None:
        anchor_date = extract_anchor_date(clauses_data)

    all_tasks: list[TaskRecord] = []

    for idx, clause in enumerate(clauses_data):
        clause_id = clause.get("clause_id", "")
        logger.info(f"Processing clause {idx+1}/{len(clauses_data)}: {clause_id}")
        try:
            tasks = generate_tasks_for_clause(
                clause_id=clause_id,
                body_text=clause.get("body_text", ""),
                clause_type=clause.get("clause_type", ""),
                entities=clause.get("entities", []),
                relations=clause.get("relations", []),
                entity_summary=clause.get("entity_summary", {}),
                contract_name=contract_name,
                anchor_date=anchor_date,
                config=config,
                heading=clause.get("heading", ""),
            )
            all_tasks.extend(tasks)
        except Exception as exc:
            logger.error("Task generation failed for clause %s: %s",
                         clause.get("clause_id", f"clause_{idx}"), exc)

    # Sort by priority: CRITICAL > HIGH > MEDIUM > LOW
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    all_tasks.sort(key=lambda t: priority_order.get(t.priority, 99))

    # ─── v4.1 FILTERING ───────────────────────────────────────────────────────
    filtered_tasks = all_tasks

    # ─── M2 Filter 0: QUALITY GATE (precision — "should this task exist?") ──────
    # Primary precision control. Modality confidence below threshold => drop
    # (borderline/misclassified clauses). Low agent-span score => the party is
    # likely a passive/agent-less artifact; drop unless the modal duty is strong
    # (then keep for review). This is what stops M1's recall gains from turning
    # into noise.
    quality_kept = []
    dropped_lowconf = dropped_lowagent = routed_review = 0
    for t in filtered_tasks:
        if t.confidence and t.confidence < config.min_confidence:
            dropped_lowconf += 1
            continue
        if (config.min_agent_score > 0 and t.agent_score
                and t.agent_score < config.min_agent_score):
            if config.review_low_quality and t.confidence >= 0.75:
                t.requires_review = True
                routed_review += 1
                quality_kept.append(t)
            else:
                dropped_lowagent += 1
            continue
        # Non-NP-shaped agent (clause fragment) → keep but flag for review.
        if not _is_plausible_party(t.obligated_party):
            t.requires_review = True
            routed_review += 1
        quality_kept.append(t)
    if config.verbose_logging and (dropped_lowconf or dropped_lowagent or routed_review):
        logger.info(
            "Quality gate: dropped %d low-confidence, %d low-agent-score; routed %d to review",
            dropped_lowconf, dropped_lowagent, routed_review,
        )
    filtered_tasks = quality_kept

    # Filter 1: Exclude PERMISSION tasks (default)
    if config.exclude_permissions:
        before_count = len(filtered_tasks)
        filtered_tasks = [t for t in filtered_tasks if t.obligation_type != "PERMISSION"]
        after_count = len(filtered_tasks)
        if config.verbose_logging and before_count > after_count:
            logger.info("Filtered %d PERMISSION tasks (%d remaining)",
                       before_count - after_count, after_count)
    
    # Filter 2: Confidence threshold (future: add confidence to TaskRecord)
    # TODO: Add confidence score to TaskRecord in next iteration
    
    # Filter 3: Exclude LOW priority (optional)
    if config.exclude_low_priority:
        before_count = len(filtered_tasks)
        filtered_tasks = [t for t in filtered_tasks if t.priority != "LOW"]
        after_count = len(filtered_tasks)
        if config.verbose_logging and before_count > after_count:
            logger.info("Filtered %d LOW priority tasks (%d remaining)",
                       before_count - after_count, after_count)
    
    # Filter 4: Total task limit (optional)
    if config.max_total_tasks and len(filtered_tasks) > config.max_total_tasks:
        logger.warning("Contract generated %d tasks, truncating to %d (keeping highest priority)",
                      len(filtered_tasks), config.max_total_tasks)
        filtered_tasks = filtered_tasks[:config.max_total_tasks]
    
    return filtered_tasks
