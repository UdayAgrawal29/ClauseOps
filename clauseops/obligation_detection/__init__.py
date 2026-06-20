"""
ClauseOps — Obligation Detection & Task Generation Module

Phase 4 of the ClauseOps pipeline:
  Clause Classification + NER → Deontic Obligation Detection
                              → Date Normalization
                              → Task Generation

Combines all three sub-modules into a single pipeline entry point.
"""

from clauseops.obligation_detection.deontic_classifier import (
    ObligationRecord,
    classify_obligation,
    classify_contract_obligations,
)
from clauseops.obligation_detection.date_normalizer import (
    DeadlineRecord,
    normalize_dates_for_clause,
    normalize_contract_dates,
    extract_anchor_date,
)
from clauseops.obligation_detection.task_generator import (
    TaskRecord,
    generate_tasks_for_clause,
    generate_tasks_for_contract,
)
from clauseops.obligation_detection.number_parser import (
    parse_number,
    parse_unit,
    parse_duration,
)
from clauseops.obligation_detection.bert_classifier import (
    extract_clause_bert,
    is_bert_available,
)
from clauseops.obligation_detection.qa_extractor import (
    extract_agent_action,
    is_qa_available,
)

__all__ = [
    # Dataclasses
    "ObligationRecord",
    "DeadlineRecord",
    "TaskRecord",
    # Clause-level functions
    "classify_obligation",
    "normalize_dates_for_clause",
    "generate_tasks_for_clause",
    # Contract-level functions
    "classify_contract_obligations",
    "normalize_contract_dates",
    "generate_tasks_for_contract",
    # BERT
    "extract_clause_bert",
    "is_bert_available",
    # QA extractor (offline agent/action)
    "extract_agent_action",
    "is_qa_available",
    # Utilities
    "extract_anchor_date",
    "parse_number",
    "parse_unit",
    "parse_duration",
]

