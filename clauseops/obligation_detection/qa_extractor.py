"""
ClauseOps — Offline Extractive QA Agent/Action Extractor (Phase 4)

Replaces the failed BIO-NER (entity-F1 ~0.465) and the spaCy dependency-parse
path. Given a clause sentence and its modality, this points into the clause to
extract the obligated party (agent) and the action — using a fine-tuned,
100% OFFLINE extractive QA model (deepset/roberta-base-squad2 fine-tuned on
teacher-distilled legal QA data).

KEY SAFETY PROPERTY (anti-hallucination / grounding invariant):
  Because the model is extractive, every returned agent/action is decoded as a
  span of the input via offset mapping. We additionally ASSERT each returned
  string is an exact substring of the clause. Any violation → discarded + logged.
  A legal compliance tool must never fabricate a party or an action.

Mirrors bert_classifier.py: lazy model loading, CPU-friendly, no network at
inference time.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_QA_MODEL_DIR = Path(__file__).parent / "models" / "clauseops-qa-extractor"
# Training writes to a `final/` subdir; accept either layout.
_QA_MODEL_FINAL = _QA_MODEL_DIR / "final"

_tokenizer = None
_model = None
_device = None

# ─── Modality-conditioned question templates ────────────────────────────────
# MUST match scripts/obligation_detection/convert_to_squad.py (training time).
QUESTION_TEMPLATES: dict[str, dict[str, str]] = {
    "OBLIGATION": {
        "agent": "Who is required to act?",
        "action": "What must they do?",
    },
    "PROHIBITION": {
        "agent": "Who is restricted?",
        "action": "What are they prohibited from doing?",
    },
    "PERMISSION": {
        "agent": "Who is granted a right?",
        "action": "What are they permitted to do?",
    },
    # CONDITIONAL is a sub-type of OBLIGATION/PROHIBITION downstream; reuse the
    # obligation framing for extraction.
    "CONDITIONAL": {
        "agent": "Who is required to act?",
        "action": "What must they do?",
    },
}

_MAX_LENGTH = 384
# If (CLS_null_score - best_span_score) exceeds this, the model abstains.
# Tunable on the validation set (plan §3.4).
_NULL_THRESHOLD = 0.0

_N_BEST = 20
_MAX_ANSWER_LEN = 80


def decode_answer(
    context: str,
    offset_mapping: list,
    sequence_ids: list,
    start_logits: list,
    end_logits: list,
    null_threshold: float = _NULL_THRESHOLD,
    n_best: int = _N_BEST,
    max_answer_len: int = _MAX_ANSWER_LEN,
) -> tuple[str | None, float]:
    """
    Pure span decoder for extractive QA. No torch dependency — operates on
    plain lists so it can be exhaustively property-tested.

    Guarantees (asserted by the runtime PBT suite):
      - Property 1 (grounding): a returned answer is ALWAYS an exact substring
        of `context`.
      - Property 2 (span validity): the chosen char span is within bounds and
        start <= end.

    Returns (answer_or_None, best_score). Returns None when the model abstains
    (no-answer/CLS wins by > null_threshold) or no valid span exists.
    """
    n = len(start_logits)
    if n == 0:
        return None, 0.0

    null_score = start_logits[0] + end_logits[0]

    def top_k(logits):
        return sorted(range(len(logits)), key=lambda i: logits[i], reverse=True)[:n_best]

    start_idx = top_k(start_logits)
    end_idx = top_k(end_logits)

    best = None  # (score, char_start, char_end)
    for s in start_idx:
        for e in end_idx:
            if s >= n or e >= n or e < s or (e - s + 1) > max_answer_len:
                continue
            if sequence_ids[s] != 1 or sequence_ids[e] != 1:
                continue
            off_s = offset_mapping[s]
            off_e = offset_mapping[e]
            if off_s is None or off_e is None:
                continue
            char_start, char_end = off_s[0], off_e[1]
            # Property 2: span validity within context bounds.
            if not (0 <= char_start <= char_end <= len(context)):
                continue
            score = start_logits[s] + end_logits[e]
            if best is None or score > best[0]:
                best = (score, char_start, char_end)

    if best is None:
        return None, null_score

    best_score, char_start, char_end = best
    if null_score - best_score > null_threshold:
        return None, best_score

    answer = context[char_start:char_end].strip()
    if not answer:
        return None, best_score

    # Property 1: grounding invariant — must be an exact substring.
    if answer not in context:
        logger.warning("QA grounding violation discarded: %r not in clause", answer)
        return None, best_score

    return answer, best_score


def _model_path() -> Path:
    """Resolve which model directory to load (prefer the `final/` subdir)."""
    if (_QA_MODEL_FINAL / "config.json").exists():
        return _QA_MODEL_FINAL
    return _QA_MODEL_DIR


def _lazy_load() -> None:
    """Load the QA model into memory only when first needed."""
    global _tokenizer, _model, _device
    if _model is not None:
        return

    import torch
    from transformers import AutoTokenizer, AutoModelForQuestionAnswering

    path = _model_path()
    if not (path / "config.json").exists():
        raise FileNotFoundError(
            "QA extractor model not found. Train it with "
            "scripts/obligation_detection/train_qa_extractor.py and place the "
            f"output at:\n  {_QA_MODEL_DIR}\n(or {_QA_MODEL_FINAL})"
        )

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Loading offline QA extractor on %s from %s", _device, path)
    _tokenizer = AutoTokenizer.from_pretrained(path)
    _model = AutoModelForQuestionAnswering.from_pretrained(path)
    _model.to(_device)
    _model.eval()
    logger.info("QA extractor loaded.")


def is_qa_available() -> bool:
    """True if the offline QA model can be loaded."""
    try:
        _lazy_load()
        return True
    except Exception:
        return False


def _answer_question(question: str, context: str) -> tuple[str | None, float]:
    """
    Run one extractive-QA query.

    Returns (answer_or_None, score). answer is None when the model abstains
    (no-answer wins) or when grounding fails. answer is guaranteed to be an
    exact substring of `context` when not None.
    """
    import torch

    enc = _tokenizer(
        question,
        context,
        return_tensors="pt",
        truncation="only_second",
        max_length=_MAX_LENGTH,
        return_offsets_mapping=True,
        padding=False,
    )
    offset_mapping = enc.pop("offset_mapping")[0].tolist()
    sequence_ids = enc.sequence_ids(0)
    inputs = {k: v.to(_device) for k, v in enc.items()}

    with torch.no_grad():
        out = _model(**inputs)
    start_logits = out.start_logits[0].cpu().tolist()
    end_logits = out.end_logits[0].cpu().tolist()

    return decode_answer(
        context=context,
        offset_mapping=offset_mapping,
        sequence_ids=sequence_ids,
        start_logits=start_logits,
        end_logits=end_logits,
    )


def extract_agent_action(sentence: str, modality: str) -> dict:
    """
    Extract the obligated party and action from a clause sentence.

    Parameters
    ----------
    sentence : str
        A single actionable clause/sentence (the modality gate should have
        already accepted it).
    modality : str
        OBLIGATION | PROHIBITION | PERMISSION | CONDITIONAL — selects the
        question framing.

    Returns
    -------
    dict with keys:
        agent        : str | None   (None = model abstained / not grounded)
        action       : str | None
        agent_score  : float
        action_score : float

    Every non-None agent/action is guaranteed to be an exact substring of
    `sentence` (grounding invariant).
    """
    result = {"agent": None, "action": None, "agent_score": 0.0, "action_score": 0.0}
    if not sentence or not sentence.strip():
        return result

    templates = QUESTION_TEMPLATES.get(modality, QUESTION_TEMPLATES["OBLIGATION"])
    _lazy_load()

    agent, agent_score = _answer_question(templates["agent"], sentence)
    action, action_score = _answer_question(templates["action"], sentence)

    result["agent"] = agent
    result["action"] = action
    result["agent_score"] = float(agent_score)
    result["action_score"] = float(action_score)
    return result
