"""
ClauseOps — Clause Classification Inference Module

Loads a fine-tuned Contracts-BERT model and classifies ClauseChunk objects
into one of 20 legal categories with confidence scores.

The model is loaded once (singleton pattern) and reused for all subsequent
classification calls — same pattern as the Docling converter in segmentation.

Usage:
    from clauseops.clause_classification import classify_clauses

    # clauses = list of ClauseChunk from segmentation
    results = classify_clauses(clauses)
    for r in results:
        print(r["clause_id"], r["clause_type"], r["confidence"])
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import torch
import numpy as np

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

# Default model path — override with environment variable CLAUSEOPS_MODEL_DIR
DEFAULT_MODEL_DIR = Path(__file__).parent.parent.parent / "models" / "clauseops-classifier"

# Confidence thresholds for the 3-zone system
CONFIDENCE_HIGH = 0.75    # ≥ this → display directly, no review needed
CONFIDENCE_MEDIUM = 0.45  # ≥ this → show top-3 alternatives, flag for review
# < MEDIUM → show as "Unclassified", requires human review

MAX_LENGTH = 512  # Must match training tokenizer max_length


# ============================================================================
# Singleton Model Loader
# ============================================================================

_model = None
_tokenizer = None
_metadata = None
_device = None


def _get_model_dir() -> Path:
    """Resolve the model directory from env var or default."""
    import os
    custom = os.environ.get("CLAUSEOPS_MODEL_DIR")
    if custom:
        return Path(custom)
    return DEFAULT_MODEL_DIR


def _load_model():
    """Load model, tokenizer, and metadata. Called once on first use."""
    global _model, _tokenizer, _metadata, _device

    if _model is not None:
        return  # Already loaded

    model_dir = _get_model_dir()

    if not model_dir.exists():
        raise FileNotFoundError(
            f"Classification model not found at: {model_dir}\n"
            f"Train the model using scripts/train_classifier.py on Kaggle,\n"
            f"then download it to: {model_dir}\n"
            f"Or set CLAUSEOPS_MODEL_DIR environment variable."
        )

    logger.info("Loading clause classification model from: %s", model_dir)

    # Load metadata
    metadata_path = model_dir / "clauseops_metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            _metadata = json.load(f)
        logger.info(
            "Model metadata: %d labels, Macro-F1=%.3f, trained %s",
            _metadata.get("num_labels", "?"),
            _metadata.get("eval_macro_f1", 0),
            _metadata.get("training_date", "unknown"),
        )
    else:
        logger.warning("No clauseops_metadata.json found — using defaults")
        _metadata = {}

    # Load tokenizer and model
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    _tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    _model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    _model.eval()

    # Use CPU for local inference (GPU optional)
    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _model.to(_device)

    logger.info(
        "Model loaded on %s (%d parameters)",
        _device,
        sum(p.numel() for p in _model.parameters()),
    )


def _get_id_to_category() -> dict[int, str]:
    """Get the id-to-category mapping from metadata or label_mapping module."""
    if _metadata and "id_to_category" in _metadata:
        # Metadata stores string keys — convert to int
        return {int(k): v for k, v in _metadata["id_to_category"].items()}

    # Fallback: use the label_mapping module
    from clauseops.clause_classification.label_mapping import ID_TO_CATEGORY
    return ID_TO_CATEGORY


# ============================================================================
# Input Formatting
# ============================================================================

def _format_input(heading: Optional[str], body_text: str) -> str:
    """
    Format heading + body into classifier input text.
    Same logic as training — strip section numbers, prepend heading.
    """
    if heading:
        clean = re.sub(r'^\d+(\.\d+)*\.?\s*', '', heading).strip().rstrip('.')
        if clean:
            return f"{clean}: {body_text}"
    return body_text


# ============================================================================
# Pre-classification Filters
# ============================================================================

# Legal verbs that signal real clause content (shared by filters)
_LEGAL_VERBS = {"shall", "must", "agree", "covenant", "warrant", "may",
                "will", "obligat", "terminat", "indemni", "pay", "deliver"}

# Keywords that indicate *genuine* renewal/extension mechanics.
# These distinguish "this Agreement shall renew for a subsequent term"
# from temporal language like "for a period of ninety (90) consecutive days."
_GENUINE_RENEWAL_KEYWORDS = [
    "automatically renew", "auto-renew",
    "shall renew", "will renew",
    "option to renew", "option to extend",
    "right of renewal", "right to renew",
    "renewal term", "renewal period", "renewal notice",
    "successive term", "successive one-year", "successive annual",
    "notice of non-renewal",
    "elect to extend", "shall be extended",
    "renew for", "renewed for", "renew automatically",
]


def _is_signature_block(heading: str, body: str, token_count: int) -> bool:
    """
    Detect signature blocks that should be filtered before classification.

    Covers two patterns:
      a) Very short (≤20 tokens) with no legal verbs — person name + title
      b) Medium-length (≤100 tokens) execution pages with Per:/By: + Name:/Title:
         patterns (Canadian/UK/US corporate signing blocks)
    """
    body_lower = body.lower()

    # Rule 1: very short with no legal verbs
    if token_count <= 20:
        if not any(v in body_lower for v in _LEGAL_VERBS):
            return True

    # Rule 2: medium-length blocks with signature execution patterns
    if token_count <= 100:
        # "Per: Name:" or "Per: ___" (Canadian/UK style)
        if re.search(r'\bPer:\s*(?:Name:|_{2,})', body, re.IGNORECASE):
            return True
        # "By: Name:" or "By: ___" (US style)
        if re.search(r'\bBy:\s*(?:Name:|_{2,})', body, re.IGNORECASE):
            return True
        # 3+ Name:/Title:/Signature: occurrences without any clause verbs
        name_title_hits = len(re.findall(
            r'\b(?:Name|Title|Signature):\s*',
            body,
        ))
        if name_title_hits >= 3:
            has_clause_signal = any(
                kw in body_lower for kw in
                ["shall", "hereby", "pursuant", "whereas", "including", "provided"]
            )
            if not has_clause_signal:
                return True

    return False


def _has_genuine_renewal_signal(body_text: str) -> bool:
    """
    Returns True only if the body contains language specifically about
    renewal mechanics — not just temporal or duration language.

    Correctly preserves:
        "this Agreement shall renew for a subsequent term of two years"
    Correctly flags:
        "for a period of ninety (90) consecutive days"  (availability window)
        "Upon execution... on each anniversary date"     (stock options)
    """
    # Normalize non-breaking spaces (U+00A0) and other Unicode whitespace
    # to regular spaces — Docling PDF extraction uses these throughout.
    body_normalized = re.sub(r'[\u00a0\u2002\u2003\u2009\u200a]', ' ', body_text.lower())
    return any(kw in body_normalized for kw in _GENUINE_RENEWAL_KEYWORDS)


# ============================================================================
# Classification Functions
# ============================================================================

def _classify_single_text(text: str) -> dict:
    """
    Classify a single text string. Returns raw probabilities.

    Returns:
        {"probs": tensor of shape (num_labels,), "predicted_id": int}
    """
    _load_model()

    inputs = _tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_LENGTH,
        padding=True,
    ).to(_device)

    with torch.no_grad():
        logits = _model(**inputs).logits

    probs = torch.softmax(logits, dim=-1)[0].cpu()
    predicted_id = probs.argmax().item()

    return {"probs": probs, "predicted_id": predicted_id}


def classify_clause(chunk) -> dict:
    """
    Classify a single ClauseChunk into one of 20 legal categories.

    Args:
        chunk: A ClauseChunk object from the segmentation pipeline.

    Returns:
        dict with keys:
            clause_id:    str — UUID of the chunk
            clause_type:  str — predicted category (e.g., "PAYMENT")
            confidence:   float — softmax probability of top prediction
            needs_review: bool — True if confidence < CONFIDENCE_HIGH
            alternatives: list — top-3 [(category, prob)] if uncertain
            source:       str — "direct", "sub_chunk_average", or "filtered"
    """
    id_to_cat = _get_id_to_category()

    # Skip non-CLAUSE types — they're already labeled
    if chunk.chunk_type != "CLAUSE":
        return {
            "clause_id": chunk.clause_id,
            "clause_type": chunk.chunk_type,  # TABLE or DEFINITION_GROUP
            "confidence": 1.0,
            "needs_review": False,
            "alternatives": [],
            "source": "pre_labeled",
        }

    # ── Fix 1: Preamble / Recitals filter ─────────────────────────────
    # These are introductory/contextual text, not classifiable provisions.
    # Sending them through the classifier produces meaningless predictions
    # that would poison downstream NER/obligation extraction.
    heading_upper = (chunk.heading or "").upper()
    body_stripped = (chunk.body_text or "").strip()

    _PREAMBLE_HEADINGS = {"PREAMBLE", "RECITALS", "RECITAL", "WITNESSETH", "BACKGROUND"}
    # Check if heading itself is a preamble indicator
    heading_clean = re.sub(r'^(ARTICLE\s+\d+\s*[-—:]*\s*)', '', heading_upper).strip()
    is_preamble = (
        heading_clean in _PREAMBLE_HEADINGS
        or body_stripped.upper().startswith("WHEREAS")
        or (body_stripped.upper().startswith("RECITALS") and chunk.token_count < 200)
    )

    if is_preamble:
        return {
            "clause_id": chunk.clause_id,
            "clause_type": "PREAMBLE",
            "confidence": 1.0,
            "needs_review": False,
            "alternatives": [],
            "source": "filtered",
        }

    # ── Fix 2: Signature block filter ─────────────────────────────────
    # Catches two categories of non-classifiable segments:
    #   a) Very short (≤20 tokens) with no legal verbs → person names, titles
    #   b) Medium-length (≤100 tokens) execution pages with Per:/By:/Name:/Title:
    # Both produce high-confidence wrong predictions (IP_OWNERSHIP, ENTIRE_AGREEMENT).
    if _is_signature_block(chunk.heading or "", body_stripped, chunk.token_count):
        return {
            "clause_id": chunk.clause_id,
            "clause_type": "SIGNATURE_BLOCK",
            "confidence": 1.0,
            "needs_review": False,
            "alternatives": [],
            "source": "filtered",
        }

    # For oversized clauses: classify each sub-chunk, average probabilities
    if chunk.is_oversized and chunk.sub_chunks:
        return _classify_oversized(chunk, id_to_cat)

    # Normal classification
    text = _format_input(chunk.heading, chunk.body_text or "")
    result = _classify_single_text(text)

    probs = result["probs"]
    top_prob = probs.max().item()
    top_id = result["predicted_id"]
    predicted_label = id_to_cat.get(top_id, "UNKNOWN")

    # ── Fix 3: RENEWAL overfit post-processing ────────────────────────
    # The model predicts RENEWAL on any temporal language ("period of X days",
    # "anniversary date", "effective on the date"). If the body doesn't
    # contain genuine renewal mechanics, force it to needs_review.
    needs_review = top_prob < CONFIDENCE_HIGH
    if predicted_label == "RENEWAL" and top_prob >= CONFIDENCE_HIGH:
        if not _has_genuine_renewal_signal(body_stripped):
            needs_review = True
            logger.debug(
                "RENEWAL downgraded to review: %s (%.1f%%)",
                chunk.heading, top_prob * 100,
            )

    # Build top-3 alternatives for uncertain predictions
    alternatives = []
    if needs_review:
        top3_indices = probs.argsort(descending=True)[:3]
        alternatives = [
            (id_to_cat.get(i.item(), "UNKNOWN"), probs[i].item())
            for i in top3_indices
        ]

    return {
        "clause_id": chunk.clause_id,
        "clause_type": predicted_label,
        "confidence": top_prob,
        "needs_review": needs_review,
        "alternatives": alternatives,
        "source": "direct",
    }


def _classify_oversized(chunk, id_to_cat: dict[int, str]) -> dict:
    """
    Classify an oversized clause by averaging sub-chunk probabilities.

    For clauses >480 tokens that were split into overlapping windows,
    we classify each window independently and average the softmax
    probabilities. This gives a more robust prediction than classifying
    only the first 512 tokens.
    """
    all_probs = []

    for sub_text in chunk.sub_chunks:
        result = _classify_single_text(sub_text)
        all_probs.append(result["probs"])

    # Average probabilities across sub-chunks
    avg_probs = torch.stack(all_probs).mean(dim=0)
    top_prob = avg_probs.max().item()
    top_id = avg_probs.argmax().item()
    predicted_label = id_to_cat.get(top_id, "UNKNOWN")

    alternatives = []
    needs_review = top_prob < CONFIDENCE_HIGH

    # Apply same RENEWAL overfit correction as direct classification
    if predicted_label == "RENEWAL" and top_prob >= CONFIDENCE_HIGH:
        body_stripped = (chunk.body_text or "").strip()
        if not _has_genuine_renewal_signal(body_stripped):
            needs_review = True
            logger.debug(
                "RENEWAL downgraded (oversized): %s (%.1f%%)",
                chunk.heading, top_prob * 100,
            )

    if needs_review:
        top3_indices = avg_probs.argsort(descending=True)[:3]
        alternatives = [
            (id_to_cat.get(i.item(), "UNKNOWN"), avg_probs[i].item())
            for i in top3_indices
        ]

    return {
        "clause_id": chunk.clause_id,
        "clause_type": predicted_label,
        "confidence": top_prob,
        "needs_review": needs_review,
        "alternatives": alternatives,
        "source": f"sub_chunk_average({len(chunk.sub_chunks)})",
    }


def classify_clauses(chunks: list) -> list[dict]:
    """
    Classify a list of ClauseChunk objects.

    This is the main entry point for batch classification.
    Loads the model on first call (singleton).

    Args:
        chunks: List of ClauseChunk objects from segment_contract().

    Returns:
        List of classification result dicts, one per chunk.
    """
    _load_model()  # Ensure model is loaded before batch

    results = []
    for chunk in chunks:
        try:
            result = classify_clause(chunk)
            results.append(result)
        except Exception as e:
            logger.error("Failed to classify chunk %s: %s", chunk.clause_id, e)
            results.append({
                "clause_id": chunk.clause_id,
                "clause_type": "UNKNOWN",
                "confidence": 0.0,
                "needs_review": True,
                "alternatives": [],
                "source": "error",
                "error": str(e),
            })

    # Log summary
    classified = [r for r in results if r["source"] != "pre_labeled"]
    if classified:
        avg_conf = np.mean([r["confidence"] for r in classified])
        needs_review = sum(1 for r in classified if r["needs_review"])
        logger.info(
            "Classified %d clauses: avg confidence=%.3f, %d need review",
            len(classified), avg_conf, needs_review,
        )

    return results


def is_model_available() -> bool:
    """Check if the classification model is available without loading it."""
    model_dir = _get_model_dir()
    return model_dir.exists() and (model_dir / "config.json").exists()
