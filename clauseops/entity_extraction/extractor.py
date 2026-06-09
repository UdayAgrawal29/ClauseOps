"""
ClauseOps - Entity Extraction Engine

Hybrid extraction using spaCy NER + rule-based durations + alias resolution.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from clauseops.entity_extraction.alias_resolver import (
    build_dynamic_party_ruler,
    extract_alias_map_from_clauses,
    resolve_aliases,
)
from clauseops.entity_extraction.duration_patterns import find_durations

logger = logging.getLogger(__name__)

_NLP = None

# Governing law context signals for GPE -> JURISDICTION mapping
_GOV_LAW_SIGNALS = [
    "governed by",
    "laws of",
    "jurisdiction of",
    "courts of",
    "applicable law",
    "construed in accordance",
    "exclusive jurisdiction",
    "venue",
    "subject to the laws of",
]


def is_ner_available() -> bool:
    """Check if the spaCy transformer model is installed."""
    try:
        from spacy.util import is_package
        return is_package("en_core_web_trf")
    except Exception:
        return False


def _load_nlp():
    """Load spaCy model once (singleton)."""
    global _NLP
    if _NLP is not None:
        return _NLP

    try:
        import spacy
        _NLP = spacy.load("en_core_web_trf")
        _NLP.max_length = max(_NLP.max_length, 200000)
    except OSError as exc:
        raise RuntimeError(
            "spaCy model 'en_core_web_trf' not found. "
            "Install it with: python -m spacy download en_core_web_trf"
        ) from exc

    return _NLP


def _span_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _map_spacy_label(ent_label: str, full_text: str, start: int, end: int) -> Optional[str]:
    label = ent_label.upper()

    if label == "PERSON":
        return "PARTY"
    if label == "ORG":
        return "ORG"
    if label == "MONEY":
        return "MONEY"
    if label == "DATE":
        return "DATE"
    if label == "PERCENT":
        return "PERCENTAGE"
    if label == "PARTY":
        return "PARTY"

    if label == "GPE":
        # Map to JURISDICTION only when context signals governing law
        left = max(0, start - 60)
        right = min(len(full_text), end + 60)
        context = full_text[left:right].lower()
        if any(signal in context for signal in _GOV_LAW_SIGNALS):
            return "JURISDICTION"
        return None

    return None


_DATE_NOISE = {
    'monthly', 'quarterly', 'annual', 'annually', 'weekly', 'daily',
    'consecutive', 'subsequent', 'previous', 'prior', 'current', 'fiscal'
}

def _extract_spacy_entities(doc, full_text: str) -> list[dict]:
    entities = []
    for ent in doc.ents:
        mapped = _map_spacy_label(ent.label_, full_text, ent.start_char, ent.end_char)
        if not mapped:
            continue
            
        text_stripped = ent.text.strip()
        text_lower = text_stripped.lower()
        
        # DATE noise filter
        if mapped == "DATE":
            if text_lower in _DATE_NOISE:
                continue
            if len(ent.text.split()) == 1 and not any(char.isdigit() for char in ent.text):
                continue
                
        # PARTY noise filter
        if mapped == "PARTY":
            if "agreement" in text_lower or "between" in text_lower:
                continue
                
        # MONEY noise filter
        if mapped == "MONEY":
            if not any(char.isdigit() for char in ent.text):
                continue

        entities.append({
            "text": ent.text,
            "label": mapped,
            "start": ent.start_char,
            "end": ent.end_char,
            "source": "spacy",
        })
    return entities


def _extract_inr_money(text: str) -> list[dict]:
    """Extract INR amounts using regex (covers rupee symbol and INR/Rs)."""
    patterns = [
        re.compile(r"\u20B9\s*[\d,]+(?:\.\d{1,2})?"),
        re.compile(r"\b(?:Rs\.?|INR)\s*[\d,]+(?:\.\d{1,2})?", re.IGNORECASE),
    ]
    results = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            results.append({
                "text": match.group(0),
                "label": "MONEY",
                "start": match.start(),
                "end": match.end(),
                "source": "rule",
            })
    return results


def _extract_relations(doc, final_entities: list[dict], context_subject: str = None) -> list[dict]:
    """
    Extract relation triplets (Obligated Party -> Action Verb -> Beneficiary/Amount/Date)
    using spaCy syntactic dependency parsing. 2026 Standard for Event Extraction.
    """
    relations = []
    
    # Helper to find which final entity contains a given token index
    def get_ent_at_index(idx: int) -> dict | None:
        for e in final_entities:
            # Note: final_entities uses character offsets, doc uses token indices.
            # We map token char offsets to check overlap.
            token = doc[idx]
            if _span_overlaps(token.idx, token.idx + len(token), e["start"], e["end"]):
                return e
        return None

    # Trace nsubj (subject) -> head (verb) -> dobj/pobj/dative (object)
    for token in doc:
        # A verb is the root action
        if token.pos_ == "VERB":
            verb = token
            subject_name = None
            
            # Find explicit subject if it exists
            explicit_subj = next((child for child in verb.children if child.dep_ in {"nsubj", "nsubjpass"}), None)
            
            subject_ent = None
            if explicit_subj:
                subject_ent = get_ent_at_index(explicit_subj.i)
                if subject_ent and subject_ent.get("label") in {"PARTY", "ORG"}:
                    subject_name = subject_ent.get("resolved_name") or subject_ent.get("text")
            
            # 2026 Zero-Anaphora Resolution: If no explicit subject, and the verb is infinitive/root,
            # inherit the context_subject from the heading.
            if not subject_name and context_subject:
                # E.g., "To assist the company" -> verb "assist" is often xcomp or ROOT, with no nsubj
                if verb.dep_ in {"xcomp", "ROOT", "conj"} or verb.morph.get("VerbForm") == ["Inf"]:
                    subject_name = context_subject
            
            if subject_name:
                # Find objects of this verb (using subtree to catch complex noun phrases)
                objects = []
                for child in verb.children:
                    if child.dep_ in {"dobj", "pobj", "nummod", "prep", "dative", "npadvmod", "oprd"}:
                        for sub_token in child.subtree:
                            obj_ent = get_ent_at_index(sub_token.i)
                            if obj_ent and obj_ent != subject_ent:
                                obj_name = obj_ent.get("resolved_name") or obj_ent.get("text")
                                obj_tuple = (obj_name, obj_ent.get("label"))
                                if obj_tuple not in objects:
                                    objects.append(obj_tuple)
                
                for obj_name, obj_label in objects:
                    relations.append({
                        "subject": subject_name,
                        "verb": verb.lemma_.lower(),
                        "object": obj_name,
                        "object_label": obj_label
                    })
    
    # Deduplicate
    seen = set()
    deduped = []
    for rel in relations:
        key = (rel["subject"], rel["verb"], rel["object"])
        if key not in seen:
            seen.add(key)
            deduped.append(rel)

    # Post-filter: remove noise relations
    # 2026 Obligation Extraction: stative verbs carry no obligation semantics.
    # Only deontic verbs (shall, must, agree, provide, deliver, pay, grant,
    # notify, terminate) produce actionable tasks for Phase 4.
    _STATIVE_VERBS = {"have", "be", "include", "contain", "mean", "define", "refer"}

    filtered = []
    for rel in deduped:
        # Drop self-relations (subject == object)
        if rel["subject"].strip().lower() == rel["object"].strip().lower():
            continue
        # Drop stative/trivial verbs
        if rel["verb"].strip() in _STATIVE_VERBS:
            continue
        filtered.append(rel)

    return filtered


def _apply_semantic_filtering(doc, entities: list[dict], duration_entities: list[dict]) -> list[dict]:
    """
    2026 Approach: Pattern-based DATE→DURATION reclassification.

    If a spaCy DATE entity overlaps with a rule-based DURATION entity,
    ALWAYS prefer the DURATION. Rationale: "thirty (30) days" is never
    an absolute calendar date — it's always a relative time span.
    The old verb-dependent approach missed most cases because verbs like
    "notify", "pay", "deliver" weren't in the duration-verb set.
    """
    if not duration_entities:
        return entities

    cleaned = []
    for ent in entities:
        if ent["label"] == "DATE":
            # If a DATE overlaps with a DURATION, always prefer DURATION
            overlapping = [d for d in duration_entities if _span_overlaps(ent["start"], ent["end"], d["start"], d["end"])]
            if overlapping:
                continue  # Drop the DATE — the DURATION will be added below
        cleaned.append(ent)

    # Merge non-overlapping durations
    for dur in duration_entities:
        if not any(_span_overlaps(dur["start"], dur["end"], c["start"], c["end"]) for c in cleaned):
            cleaned.append(dur)
            
    return cleaned


def _merge_rule_entities(entities: list[dict], rule_entities: list[dict]) -> list[dict]:
    if not rule_entities:
        return entities

    merged = list(entities)
    for rule_ent in rule_entities:
        if any(
            _span_overlaps(rule_ent["start"], rule_ent["end"], e["start"], e["end"])
            and rule_ent["label"] == e["label"]
            for e in merged
        ):
            continue
        merged.append(rule_ent)
    return merged


def _dedupe_entities(entities: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for ent in entities:
        key = (
            ent.get("label"),
            ent.get("start"),
            ent.get("end"),
            (ent.get("text") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ent)
    return deduped


def _build_entity_summary(entities: list[dict]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for ent in entities:
        label = ent.get("label")
        if not label:
            continue
        display = ent.get("resolved_name") or ent.get("text")
        if not display:
            continue
        summary.setdefault(label, [])
        if display not in summary[label]:
            summary[label].append(display)
    return summary


def _extract_entities(text: str, nlp, dynamic_ruler, context_subject: str = None) -> tuple[list[dict], list[dict]]:
    doc = nlp(text)
    if dynamic_ruler is not None:
        doc = dynamic_ruler(doc)

    entities = _extract_spacy_entities(doc, text)

    # Rule-based durations (override with semantic filtering)
    duration_entities = find_durations(text)
    entities = _apply_semantic_filtering(doc, entities, duration_entities)

    # INR money overrides
    money_entities = _extract_inr_money(text)
    entities = _merge_rule_entities(entities, money_entities)

    final_entities = _dedupe_entities(entities)
    relations = _extract_relations(doc, final_entities, context_subject)
    
    return final_entities, relations


def extract_entities_from_clause(chunk, alias_map: dict[str, str], dynamic_ruler, nlp) -> dict:
    """
    Extract entities for a single ClauseChunk.
    Returns dict with entities + summary and optional definition_entities.
    """
    # 2026 Generalized Context Propagation: Extract subject from the heading
    heading_doc = nlp(chunk.heading or "")
    if dynamic_ruler:
        heading_doc = dynamic_ruler(heading_doc)
    
    heading_ents = _extract_spacy_entities(heading_doc, chunk.heading or "")
    heading_ents = resolve_aliases(heading_ents, alias_map)
    context_subject = None
    for ent in heading_ents:
        if ent.get("label") in {"PARTY", "ORG"}:
            context_subject = ent.get("resolved_name") or ent.get("text")
            break
            
    if chunk.chunk_type == "DEFINITION_GROUP":
        definition_entities = []
        flat_entities: list[dict] = []
        all_relations = []
        for item in chunk.definitions:
            ents, rels = _extract_entities(item.raw_text or "", nlp, dynamic_ruler, context_subject)
            ents = resolve_aliases(ents, alias_map)
            definition_entities.append({
                "term": item.term,
                "entities": ents,
                "relations": rels
            })
            flat_entities.extend(ents)
            all_relations.extend(rels)

        flat_entities = _dedupe_entities(flat_entities)
        return {
            "clause_id": chunk.clause_id,
            "chunk_type": chunk.chunk_type,
            "entities": flat_entities,
            "entity_summary": _build_entity_summary(flat_entities),
            "relations": all_relations,
            "definition_entities": definition_entities,
        }

    if chunk.chunk_type == "TABLE":
        text = chunk.table_markdown or ""
    else:
        text = chunk.body_text or ""

    entities, relations = _extract_entities(text, nlp, dynamic_ruler, context_subject)
    entities = resolve_aliases(entities, alias_map)

    return {
        "clause_id": chunk.clause_id,
        "chunk_type": chunk.chunk_type,
        "entities": entities,
        "entity_summary": _build_entity_summary(entities),
        "relations": relations,
    }


def extract_entities_from_contract(clauses: list) -> list[dict]:
    """
    Extract entities for a full contract (list of ClauseChunk).
    Returns a list aligned to the clause order.
    """
    nlp = _load_nlp()
    alias_map = extract_alias_map_from_clauses(clauses, nlp)
    dynamic_ruler = build_dynamic_party_ruler(nlp, alias_map)

    results = []
    for chunk in clauses:
        try:
            results.append(extract_entities_from_clause(chunk, alias_map, dynamic_ruler, nlp))
        except Exception as exc:
            logger.error("NER failed for chunk %s: %s", chunk.clause_id, exc)
            results.append({
                "clause_id": chunk.clause_id,
                "chunk_type": chunk.chunk_type,
                "entities": [],
                "entity_summary": {},
                "error": str(exc),
            })

    return results
