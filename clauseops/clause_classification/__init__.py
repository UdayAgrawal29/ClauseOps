"""
ClauseOps Clause Classification Module

Provides `classify_clauses()` — classifies segmented ClauseChunk objects
into 20 legal categories using a fine-tuned Contracts-BERT model.

Usage:
    from clauseops.clause_classification import classify_clauses, is_model_available

    if is_model_available():
        results = classify_clauses(clauses)
        for r in results:
            print(r["clause_type"], r["confidence"])
    else:
        print("Model not trained yet — run scripts/train_classifier.py on Kaggle")
"""

from clauseops.clause_classification.classifier import (
    classify_clause,
    classify_clauses,
    is_model_available,
)

from clauseops.clause_classification.label_mapping import (
    CATEGORIES,
    CATEGORY_TO_ID,
    ID_TO_CATEGORY,
    DISPLAY_LABELS,
    NUM_LABELS,
    format_input,
)

__all__ = [
    "classify_clause",
    "classify_clauses",
    "is_model_available",
    "CATEGORIES",
    "CATEGORY_TO_ID",
    "ID_TO_CATEGORY",
    "DISPLAY_LABELS",
    "NUM_LABELS",
    "format_input",
]
