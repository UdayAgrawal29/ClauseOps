"""
ClauseOps — Phase 4 QA Distillation: SQuAD Converter (Phase A)
================================================================
Converts the existing teacher-LLM annotations (raw_annotations.jsonl) into a
SQuAD v2-style extractive-QA dataset for fine-tuning an OFFLINE span-extraction
model (e.g. deepset/roberta-base-squad2).

Why QA instead of BIO NER?
  BIO token classification scored entity-F1 ~0.465 because "action" spans are
  long (10-40 words) with fuzzy boundaries — not learnable as exact per-token
  tags from ~1.5k examples. Extractive QA points into the text (start/end
  pointers), tolerates fuzzy boundaries (overlap-F1), and gives "no-answer"
  (abstention) for free from SQuAD2 pretraining.

Anti-hallucination guarantee:
  Every emitted answer is, by construction, an exact substring of the context.
  This is asserted at conversion time (the grounding invariant). A legal
  compliance tool must never fabricate a party or action.

Input:
  scripts/obligation_detection/training_data/raw_annotations.jsonl
  Each row: {clause_text, modality, agent, action, reasoning, source, category}

Output (scripts/obligation_detection/training_data/):
  qa_train.jsonl, qa_val.jsonl, qa_test.jsonl   (SQuAD v2 style)
  qa_metadata.json                              (stats + question templates)

Each emitted example:
  {
    "id": str,
    "context": str,
    "question": str,
    "answers": {"text": [str], "answer_start": [int]},   # empty lists if impossible
    "is_impossible": bool,
    "field": "agent" | "action",
    "modality": str,
    "source": str,
  }

Usage:
  python scripts/obligation_detection/convert_to_squad.py
  python scripts/obligation_detection/convert_to_squad.py --seed 42
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "training_data"
RAW_PATH = DATA_DIR / "raw_annotations.jsonl"

VALID_MODALITIES = ("OBLIGATION", "PROHIBITION", "PERMISSION", "DECLARATIVE")
ACTIONABLE_MODALITIES = ("OBLIGATION", "PROHIBITION", "PERMISSION")

# ─── Modality-conditioned question templates ────────────────────────────────
# The framing teaches the model the right "lens" for each deontic modality.
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
}

# Deterministic ordering for cycling no-answer (DECLARATIVE) examples across
# all three modality framings so the model learns to abstain under each lens.
_FRAMING_ORDER = ["OBLIGATION", "PROHIBITION", "PERMISSION"]

# ─── Action boundary markers (trim carve-outs / provisos for concision) ─────
# An action is cut at the FIRST occurrence of any of these (case-insensitive).
# Because we only cut from the end, the trimmed action stays a prefix of the
# located span → grounding/idempotence are preserved.
_ACTION_BOUNDARY_MARKERS = [
    ", except",
    ", unless",
    ", provided that",
    ", provided,",
    "; provided",
    " provided that",
    ", subject to",
    "; subject to",
    ", but only",
    ", to the extent that",
    ", in the event that",
    "; provided,",
]

# Soft word cap for "concise" actions (cut at a word boundary → still a prefix).
MAX_ACTION_WORDS = 30

# Trailing characters to strip from a trimmed action (tail-only → safe).
_TRAILING_STRIP = " \t\r\n,;:.-"


# ═══════════════════════════════════════════════════════════════════════════
# PURE CORE (unit / property-test friendly — no I/O)
# ═══════════════════════════════════════════════════════════════════════════

def trim_action(action: str) -> str:
    """
    Shorten an over-long action to its core verb phrase.

    Cuts at the first clause-boundary marker, then applies a soft word cap.
    Only ever removes characters from the END, so the result is always a
    PREFIX of the input. This guarantees that if `action` was grounded at some
    offset in a context, the trimmed action is still grounded at the same offset.
    """
    if not action:
        return action

    cut = len(action)
    lower = action.lower()
    for marker in _ACTION_BOUNDARY_MARKERS:
        idx = lower.find(marker)
        if 0 <= idx < cut:
            cut = idx
    trimmed = action[:cut]

    # Soft word cap (cut at a word boundary — still a prefix).
    words = trimmed.split()
    if len(words) > MAX_ACTION_WORDS:
        # Rebuild a prefix containing the first MAX_ACTION_WORDS words by
        # locating the end offset of that word in the original string.
        count = 0
        end = 0
        for m in re.finditer(r"\S+", trimmed):
            count += 1
            end = m.end()
            if count >= MAX_ACTION_WORDS:
                break
        trimmed = trimmed[:end]

    # Tail-only strip (never touches the leading edge → offset stays valid).
    trimmed = trimmed.rstrip(_TRAILING_STRIP)
    return trimmed


def locate_span(context: str, answer: str) -> tuple[int, str] | None:
    """
    Find `answer` verbatim in `context`.

    Returns (answer_start, exact_text) where exact_text == context slice, or
    None if the answer cannot be grounded.

    Strategy:
      1. Exact (case-sensitive) match.
      2. Case-insensitive fallback — but return the EXACT-CASE substring from
         the context so the emitted answer is character-for-character a slice
         of the context (preserves the grounding invariant).
    """
    if not answer:
        return None

    idx = context.find(answer)
    if idx >= 0:
        return idx, context[idx:idx + len(answer)]

    idx = context.lower().find(answer.lower())
    if idx >= 0:
        return idx, context[idx:idx + len(answer)]

    return None


def _make_example(
    example_id: str,
    context: str,
    question: str,
    field: str,
    modality: str,
    source: str,
    answer_start: int | None,
    answer_text: str | None,
) -> dict:
    """Assemble one SQuAD v2-style example and assert the grounding invariant."""
    is_impossible = answer_start is None
    if is_impossible:
        answers = {"text": [], "answer_start": []}
    else:
        # GROUNDING INVARIANT (Property 1 + 6): answer must be an exact slice.
        assert context[answer_start:answer_start + len(answer_text)] == answer_text, (
            f"Grounding violation for {example_id}: "
            f"{answer_text!r} != context slice at {answer_start}"
        )
        answers = {"text": [answer_text], "answer_start": [answer_start]}

    return {
        "id": example_id,
        "context": context,
        "question": question,
        "answers": answers,
        "is_impossible": is_impossible,
        "field": field,
        "modality": modality,
        "source": source,
    }


def build_qa_examples(record: dict, index: int = 0) -> list[dict] | None:
    """
    Convert one raw annotation into up to two SQuAD examples (agent + action).

    Returns:
      - list of 2 examples on success
      - None if the record is invalid or an actionable span cannot be grounded
        (the whole record is dropped to avoid noisy supervision).
    """
    context = (record.get("clause_text") or "").strip()
    modality = record.get("modality")
    source = record.get("source", "unknown")

    if not context or modality not in VALID_MODALITIES:
        return None

    base_id = record.get("id") or f"row{index}"

    # ── No-answer (abstention) examples ─────────────────────────────────────
    if modality == "DECLARATIVE":
        framing = _FRAMING_ORDER[index % len(_FRAMING_ORDER)]
        tmpl = QUESTION_TEMPLATES[framing]
        return [
            _make_example(f"{base_id}-agent", context, tmpl["agent"], "agent",
                          modality, source, None, None),
            _make_example(f"{base_id}-action", context, tmpl["action"], "action",
                          modality, source, None, None),
        ]

    # ── Actionable examples (must ground both agent and action) ─────────────
    agent = (record.get("agent") or "").strip()
    action = (record.get("action") or "").strip()
    if not agent or not action:
        return None

    agent_hit = locate_span(context, agent)
    if agent_hit is None:
        return None
    agent_start, agent_text = agent_hit

    trimmed_action = trim_action(action)
    if not trimmed_action:
        return None
    action_hit = locate_span(context, trimmed_action)
    if action_hit is None:
        return None
    action_start, action_text = action_hit

    tmpl = QUESTION_TEMPLATES[modality]
    return [
        _make_example(f"{base_id}-agent", context, tmpl["agent"], "agent",
                      modality, source, agent_start, agent_text),
        _make_example(f"{base_id}-action", context, tmpl["action"], "action",
                      modality, source, action_start, action_text),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# I/O + DRIVER
# ═══════════════════════════════════════════════════════════════════════════

def load_raw(path: Path) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON line")
    return rows


def dedup_rows(rows: list[dict]) -> list[dict]:
    """Dedup by clause text (already done upstream, but be safe)."""
    seen: set[str] = set()
    out = []
    for r in rows:
        key = (r.get("clause_text") or "").strip()[:200].lower()
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def split_examples(
    examples: list[dict], seed: int
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Split into 75/12.5/12.5 train/val/test, grouped by source record id so the
    agent+action pair for one clause never straddles a split (no leakage).
    """
    groups: dict[str, list[dict]] = {}
    for ex in examples:
        gid = ex["id"].rsplit("-", 1)[0]
        groups.setdefault(gid, []).append(ex)

    gids = list(groups.keys())
    rng = random.Random(seed)
    rng.shuffle(gids)

    n = len(gids)
    n_train = int(n * 0.75)
    n_val = int(n * 0.125)

    train_g = gids[:n_train]
    val_g = gids[n_train:n_train + n_val]
    test_g = gids[n_train + n_val:]

    def collect(group_ids: list[str]) -> list[dict]:
        out = []
        for gid in group_ids:
            out.extend(groups[gid])
        return out

    return collect(train_g), collect(val_g), collect(test_g)


def write_jsonl(path: Path, examples: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert annotations to SQuAD v2 QA format.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--raw", type=Path, default=RAW_PATH)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    if not args.raw.exists():
        raise FileNotFoundError(f"Raw annotations not found: {args.raw}")

    rows = load_raw(args.raw)
    logger.info("Loaded %d raw annotations", len(rows))
    rows = dedup_rows(rows)
    logger.info("After dedup: %d", len(rows))

    examples: list[dict] = []
    dropped = 0
    modality_counts: Counter = Counter()
    for i, row in enumerate(rows):
        built = build_qa_examples(row, index=i)
        if built is None:
            dropped += 1
            continue
        examples.extend(built)
        modality_counts[row.get("modality")] += 1

    logger.info("Built %d QA examples from %d records (dropped %d ungroundable/invalid)",
                len(examples), len(rows) - dropped, dropped)

    impossible = sum(1 for e in examples if e["is_impossible"])
    logger.info("Answerable: %d | No-answer (impossible): %d",
                len(examples) - impossible, impossible)

    train, val, test = split_examples(examples, args.seed)
    logger.info("Split → train=%d val=%d test=%d", len(train), len(val), len(test))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "qa_train.jsonl", train)
    write_jsonl(args.out_dir / "qa_val.jsonl", val)
    write_jsonl(args.out_dir / "qa_test.jsonl", test)

    metadata = {
        "format": "squad_v2",
        "question_templates": QUESTION_TEMPLATES,
        "max_action_words": MAX_ACTION_WORDS,
        "action_boundary_markers": _ACTION_BOUNDARY_MARKERS,
        "total_examples": len(examples),
        "answerable": len(examples) - impossible,
        "no_answer": impossible,
        "records_used": len(rows) - dropped,
        "records_dropped": dropped,
        "modality_record_counts": dict(modality_counts),
        "splits": {"train": len(train), "val": len(val), "test": len(test)},
        "seed": args.seed,
    }
    with open(args.out_dir / "qa_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Wrote qa_train/val/test.jsonl + qa_metadata.json to %s", args.out_dir)


if __name__ == "__main__":
    main()
