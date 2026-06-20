"""
ClauseOps — BERT-Based Modality Classifier
Loads the custom fine-tuned Legal-BERT model for Modality extraction.
"""

import os
import logging
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

logger = logging.getLogger(__name__)

_MOD_MODEL_DIR = Path(__file__).parent / "models" / "clauseops-modality-classifier"

_tokenizer = None
_mod_model = None
_device = None

def _lazy_load_models():
    """Load models into memory only when needed."""
    global _tokenizer, _mod_model, _device
    if _mod_model is not None:
        return

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Loading custom ClauseOps BERT Modality model on {_device}...")

    if not _MOD_MODEL_DIR.exists():
        raise FileNotFoundError(
            f"Model not found. Ensure trained model is extracted to:\n"
            f"  {_MOD_MODEL_DIR}"
        )

    try:
        # Modality Model
        _tokenizer = AutoTokenizer.from_pretrained(_MOD_MODEL_DIR)
        _mod_model = AutoModelForSequenceClassification.from_pretrained(_MOD_MODEL_DIR)
        _mod_model.to(_device)
        _mod_model.eval()

        logger.info("Custom BERT Modality model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load custom BERT Modality model: {e}")
        raise RuntimeError(f"Could not load custom BERT Modality model: {e}")

def is_bert_available() -> bool:
    try:
        _lazy_load_models()
        return True
    except Exception:
        return False

def extract_clause_bert(clause_text: str) -> dict:
    """
    Run the text through the Modality model.
    
    Returns
    -------
    dict: {
        "modality": str (OBLIGATION, PROHIBITION, PERMISSION, DECLARATIVE),
        "confidence": float
    }
    """
    if not clause_text or len(clause_text.strip()) < 20:
        return {"modality": "DECLARATIVE", "confidence": 0.0}

    _lazy_load_models()

    inputs = _tokenizer(clause_text, return_tensors="pt", truncation=True, max_length=512, padding=False)
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    # 1. Modality Prediction
    with torch.no_grad():
        mod_logits = _mod_model(**inputs).logits
    mod_probs = torch.softmax(mod_logits, dim=-1)[0]
    mod_pred_id = mod_probs.argmax().item()
    
    modality = _mod_model.config.id2label[mod_pred_id]
    
    _LABEL_MAP = {
        "LABEL_0": "OBLIGATION",
        "LABEL_1": "PROHIBITION",
        "LABEL_2": "PERMISSION",
        "LABEL_3": "DECLARATIVE"
    }
    modality = _LABEL_MAP.get(modality, modality)
    mod_conf = mod_probs[mod_pred_id].item()

    return {
        "modality": modality,
        "confidence": mod_conf
    }
