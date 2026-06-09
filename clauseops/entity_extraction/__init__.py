"""
ClauseOps Entity Extraction Module

Hybrid NER using spaCy + rule-based duration + alias resolution.
"""

from clauseops.entity_extraction.extractor import (
    extract_entities_from_clause,
    extract_entities_from_contract,
    is_ner_available,
)
from clauseops.entity_extraction.alias_resolver import (
    extract_alias_map,
    extract_alias_map_from_clauses,
    resolve_aliases,
    build_dynamic_party_ruler,
)

__all__ = [
    "extract_entities_from_clause",
    "extract_entities_from_contract",
    "is_ner_available",
    "extract_alias_map",
    "extract_alias_map_from_clauses",
    "resolve_aliases",
    "build_dynamic_party_ruler",
]
