"""
ClauseOps — Phase 4 QA Distillation: Train the Offline Extractive QA Extractor
================================================================================
Fine-tunes an OFFLINE extractive span-reader to extract (agent, action) from a
legal clause, conditioned on the modality-specific question. Distilled from the
teacher-LLM SQuAD data produced by convert_to_squad.py.

  Base model:  deepset/roberta-base-squad2
               (already knows span extraction + "no answer" from SQuAD2)
  Task:        extractive QA — predict start/end pointers into the clause
  Output:      clauseops-qa-extractor/  (drop into
               clauseops/obligation_detection/models/clauseops-qa-extractor/)

WHY QA (not BIO NER):
  BIO scored entity-F1 ~0.465 — long fuzzy "action" spans aren't learnable as
  exact per-token tags. QA points into text, tolerates fuzzy boundaries
  (overlap-F1), abstains via the SQuAD2 no-answer head, and CANNOT hallucinate
  (every answer is a substring of the clause).

SUCCESS TARGETS (from the plan §2):
  - Agent  token-overlap F1 >= 0.85
  - Action token-overlap F1 >= 0.70
  - No-answer accuracy       >= 0.90

HOW TO USE ON KAGGLE:
  1. Run convert_to_squad.py locally → qa_train/val/test.jsonl
  2. Upload the training_data/ folder as a Kaggle Dataset
  3. New Kaggle Notebook with GPU (P100/T4)
  4. Copy each "=== CELL N ===" block into its own cell, run in order
  5. Download clauseops-qa-extractor/ from /kaggle/working/

Data format (SQuAD v2 style, from convert_to_squad.py):
  {"id","context","question","answers":{"text":[...],"answer_start":[...]},
   "is_impossible": bool, "field": "agent"|"action", "modality": str}
"""

# =============================================================================
# === CELL 1: Install Dependencies ===
# =============================================================================

# !pip install -q transformers datasets evaluate

# =============================================================================
# === CELL 2: Imports & GPU Check ===
# =============================================================================

import os
import json
import time
import collections
from pathlib import Path

import numpy as np
import torch
from transformers import (
    AutoTokenizer,
    AutoModelForQuestionAnswering,
    TrainingArguments,
    Trainer,
    default_data_collator,
    EarlyStoppingCallback,
)
from datasets import Dataset

print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")


# =============================================================================
# === CELL 3: Load SQuAD-style QA Data ===
# =============================================================================

DATA_DIR = Path("/kaggle/input/clauseops-training-data/training_data")
if not DATA_DIR.exists():
    DATA_DIR = Path("./training_data")
if not DATA_DIR.exists():
    DATA_DIR = Path(__file__).parent / "training_data"

if not (DATA_DIR / "qa_train.jsonl").exists():
    raise FileNotFoundError(
        f"qa_train.jsonl not found under {DATA_DIR}. "
        "Run convert_to_squad.py first."
    )


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


qa_train = load_jsonl(DATA_DIR / "qa_train.jsonl")
qa_val = load_jsonl(DATA_DIR / "qa_val.jsonl")
qa_test = load_jsonl(DATA_DIR / "qa_test.jsonl")

print(f"Train: {len(qa_train)}  Val: {len(qa_val)}  Test: {len(qa_test)}")
print(f"Train answerable: {sum(1 for r in qa_train if not r['is_impossible'])}")
print(f"Train no-answer:  {sum(1 for r in qa_train if r['is_impossible'])}")


# =============================================================================
# === CELL 4: Tokenizer + Base Model ===
# =============================================================================

MODEL_NAME = "deepset/roberta-base-squad2"   # SQuAD2 reader → span + no-answer prior
MAX_LENGTH = 384
DOC_STRIDE = 128

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
pad_on_right = tokenizer.padding_side == "right"
print(f"Loaded tokenizer for {MODEL_NAME} (pad_on_right={pad_on_right})")


# =============================================================================
# === CELL 5: Preprocess — TRAIN features (start/end positions) ===
# =============================================================================
# Standard HF SQuAD2 preprocessing. For no-answer examples, the answer points
# to the [CLS] token (index 0) so the model learns to abstain.

def prepare_train_features(examples):
    questions = [q.lstrip() for q in examples["question"]]
    tokenized = tokenizer(
        questions if pad_on_right else examples["context"],
        examples["context"] if pad_on_right else questions,
        truncation="only_second" if pad_on_right else "only_first",
        max_length=MAX_LENGTH,
        stride=DOC_STRIDE,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        padding="max_length",
    )

    sample_mapping = tokenized.pop("overflow_to_sample_mapping")
    offset_mapping = tokenized.pop("offset_mapping")

    tokenized["start_positions"] = []
    tokenized["end_positions"] = []

    for i, offsets in enumerate(offset_mapping):
        input_ids = tokenized["input_ids"][i]
        cls_index = input_ids.index(tokenizer.cls_token_id)
        sequence_ids = tokenized.sequence_ids(i)

        sample_index = sample_mapping[i]
        answers = examples["answers"][sample_index]

        if len(answers["answer_start"]) == 0:
            tokenized["start_positions"].append(cls_index)
            tokenized["end_positions"].append(cls_index)
            continue

        start_char = answers["answer_start"][0]
        end_char = start_char + len(answers["text"][0])

        context_idx = 1 if pad_on_right else 0
        token_start = 0
        while sequence_ids[token_start] != context_idx:
            token_start += 1
        token_end = len(input_ids) - 1
        while sequence_ids[token_end] != context_idx:
            token_end -= 1

        if not (offsets[token_start][0] <= start_char and offsets[token_end][1] >= end_char):
            # Answer is out of this span → abstain (CLS).
            tokenized["start_positions"].append(cls_index)
            tokenized["end_positions"].append(cls_index)
        else:
            while token_start < len(offsets) and offsets[token_start][0] <= start_char:
                token_start += 1
            tokenized["start_positions"].append(token_start - 1)
            while offsets[token_end][1] >= end_char:
                token_end -= 1
            tokenized["end_positions"].append(token_end + 1)

    return tokenized


def prepare_validation_features(examples):
    questions = [q.lstrip() for q in examples["question"]]
    tokenized = tokenizer(
        questions if pad_on_right else examples["context"],
        examples["context"] if pad_on_right else questions,
        truncation="only_second" if pad_on_right else "only_first",
        max_length=MAX_LENGTH,
        stride=DOC_STRIDE,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        padding="max_length",
    )
    sample_mapping = tokenized.pop("overflow_to_sample_mapping")
    tokenized["example_id"] = []
    for i in range(len(tokenized["input_ids"])):
        sequence_ids = tokenized.sequence_ids(i)
        context_idx = 1 if pad_on_right else 0
        sample_index = sample_mapping[i]
        tokenized["example_id"].append(examples["id"][sample_index])
        tokenized["offset_mapping"][i] = [
            o if sequence_ids[k] == context_idx else None
            for k, o in enumerate(tokenized["offset_mapping"][i])
        ]
    return tokenized


def to_hf(rows):
    return Dataset.from_dict({
        "id": [r["id"] for r in rows],
        "context": [r["context"] for r in rows],
        "question": [r["question"] for r in rows],
        "answers": [r["answers"] for r in rows],
    })


train_ds = to_hf(qa_train)
val_ds = to_hf(qa_val)
test_ds = to_hf(qa_test)

train_feats = train_ds.map(prepare_train_features, batched=True, remove_columns=train_ds.column_names)
val_feats = val_ds.map(prepare_validation_features, batched=True, remove_columns=val_ds.column_names)
test_feats = test_ds.map(prepare_validation_features, batched=True, remove_columns=test_ds.column_names)

# Label-bearing val features (start/end positions) — so the Trainer can compute
# eval_loss during training. `val_feats` (validation features w/ offset_mapping)
# is reserved for span postprocessing + metrics in CELL 8.
val_loss_feats = val_ds.map(prepare_train_features, batched=True, remove_columns=val_ds.column_names)

print(f"Train features: {len(train_feats)}  Val features: {len(val_feats)}")


# =============================================================================
# === CELL 6: Postprocessing + Metrics (overlap-F1, EM, no-answer acc) ===
# =============================================================================

def postprocess_qa_predictions(examples, features, raw_predictions,
                                n_best=20, max_answer_length=80,
                                null_score_diff_threshold=0.0):
    all_start_logits, all_end_logits = raw_predictions

    example_id_to_index = {k: i for i, k in enumerate(examples["id"])}
    features_per_example = collections.defaultdict(list)
    for i, feat_id in enumerate(features["example_id"]):
        features_per_example[example_id_to_index[feat_id]].append(i)

    predictions = {}
    for example_index in range(len(examples["id"])):
        feature_indices = features_per_example[example_index]
        context = examples["context"][example_index]

        min_null_score = None
        valid_answers = []
        for feature_index in feature_indices:
            start_logits = all_start_logits[feature_index]
            end_logits = all_end_logits[feature_index]
            offset_mapping = features["offset_mapping"][feature_index]

            cls_score = start_logits[0] + end_logits[0]
            if min_null_score is None or cls_score < min_null_score:
                min_null_score = cls_score

            start_indexes = np.argsort(start_logits)[-1:-n_best - 1:-1].tolist()
            end_indexes = np.argsort(end_logits)[-1:-n_best - 1:-1].tolist()
            for s in start_indexes:
                for e in end_indexes:
                    if s >= len(offset_mapping) or e >= len(offset_mapping):
                        continue
                    if offset_mapping[s] is None or offset_mapping[e] is None:
                        continue
                    if e < s or e - s + 1 > max_answer_length:
                        continue
                    start_char = offset_mapping[s][0]
                    end_char = offset_mapping[e][1]
                    valid_answers.append({
                        "score": start_logits[s] + end_logits[e],
                        "text": context[start_char:end_char],
                    })

        best = max(valid_answers, key=lambda x: x["score"]) if valid_answers else {"text": "", "score": 0.0}
        # SQuAD2 no-answer decision.
        if min_null_score is not None and min_null_score - best["score"] > null_score_diff_threshold:
            predictions[examples["id"][example_index]] = ""
        else:
            predictions[examples["id"][example_index]] = best["text"]
    return predictions


def _tokens(s):
    return s.lower().split()


def overlap_f1(pred, gold):
    pt, gt = _tokens(pred), _tokens(gold)
    if not pt and not gt:
        return 1.0
    if not pt or not gt:
        return 0.0
    common = collections.Counter(pt) & collections.Counter(gt)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pt)
    recall = num_same / len(gt)
    return 2 * precision * recall / (precision + recall)


def evaluate_split(trainer, features, examples, rows, tag=""):
    raw = trainer.predict(features)
    preds = postprocess_qa_predictions(
        {"id": examples["id"], "context": examples["context"]},
        features, raw.predictions,
    )
    by_id = {r["id"]: r for r in rows}

    agent_f1, action_f1 = [], []
    noans_correct = noans_total = 0
    em_agent = em_action = 0
    n_agent = n_action = 0

    for ex_id, pred_text in preds.items():
        row = by_id[ex_id]
        gold = row["answers"]["text"][0] if row["answers"]["text"] else ""
        is_imp = row["is_impossible"]
        field = row.get("field", "")

        if is_imp:
            noans_total += 1
            if pred_text.strip() == "":
                noans_correct += 1
            continue

        f1 = overlap_f1(pred_text, gold)
        em = 1 if pred_text.strip().lower() == gold.strip().lower() else 0
        if field == "agent":
            agent_f1.append(f1); em_agent += em; n_agent += 1
        elif field == "action":
            action_f1.append(f1); em_action += em; n_action += 1

    res = {
        "agent_overlap_f1": float(np.mean(agent_f1)) if agent_f1 else 0.0,
        "action_overlap_f1": float(np.mean(action_f1)) if action_f1 else 0.0,
        "agent_em": em_agent / n_agent if n_agent else 0.0,
        "action_em": em_action / n_action if n_action else 0.0,
        "no_answer_acc": noans_correct / noans_total if noans_total else 0.0,
    }
    print(f"\n===== {tag} =====")
    for k, v in res.items():
        print(f"  {k:20s}: {v:.4f}")
    print("  TARGETS: agent_f1>=0.85  action_f1>=0.70  no_answer_acc>=0.90")
    return res


# =============================================================================
# === CELL 7: Train ===
# =============================================================================

OUTPUT_DIR = "/kaggle/working/clauseops-qa-extractor"
if not os.path.exists("/kaggle/working"):
    OUTPUT_DIR = "./clauseops-qa-extractor"

model = AutoModelForQuestionAnswering.from_pretrained(MODEL_NAME)

args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=4,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    gradient_accumulation_steps=2,
    learning_rate=3e-5,
    warmup_ratio=0.1,
    weight_decay=0.01,
    fp16=torch.cuda.is_available(),
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    greater_is_better=False,
    save_total_limit=2,
    logging_steps=50,
    report_to="none",
    seed=42,
)

trainer = Trainer(
    model=model,
    args=args,
    train_dataset=train_feats,
    eval_dataset=val_loss_feats,
    data_collator=default_data_collator,
    processing_class=tokenizer,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
)

print("Training QA extractor...")
trainer.train()


# =============================================================================
# === CELL 8: Evaluate vs targets ===
# =============================================================================

evaluate_split(trainer, val_feats, val_ds, qa_val, tag="VALIDATION")
test_res = evaluate_split(trainer, test_feats, test_ds, qa_test, tag="TEST")


# =============================================================================
# === CELL 9: Save model ===
# =============================================================================

save_path = os.path.join(OUTPUT_DIR, "final")
trainer.save_model(save_path)
tokenizer.save_pretrained(save_path)

with open(os.path.join(save_path, "qa_metadata.json"), "w") as f:
    json.dump({
        "base_model": MODEL_NAME,
        "task": "extractive_qa_agent_action",
        "max_length": MAX_LENGTH,
        "doc_stride": DOC_STRIDE,
        "test_metrics": test_res,
        "training_date": time.strftime("%Y-%m-%d %H:%M"),
    }, f, indent=2)

print(f"Saved QA extractor to {save_path}")
print("Place it at: clauseops/obligation_detection/models/clauseops-qa-extractor/final")


# =============================================================================
# === CELL 10: Quick inference smoke test ===
# =============================================================================

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.eval().to(device)

samples = [
    ("Who is required to act?",
     "ESSI shall feature the following disclaimer in close proximity to said endorsement."),
    ("What must they do?",
     "ESSI shall feature the following disclaimer in close proximity to said endorsement."),
    ("Who is restricted?",
     "Neither Party may transfer or assign any of its rights without prior written consent."),
    ("Who is required to act?",
     "This Agreement shall be governed by the laws of the State of New York."),  # expect no-answer
]
for q, c in samples:
    inp = tokenizer(q, c, return_tensors="pt", truncation=True, max_length=MAX_LENGTH).to(device)
    with torch.no_grad():
        out = model(**inp)
    s = out.start_logits.argmax().item()
    e = out.end_logits.argmax().item()
    ids = inp["input_ids"][0]
    ans = "" if (s == 0 or e < s) else tokenizer.decode(ids[s:e + 1], skip_special_tokens=True)
    print(f"Q: {q}\n  → {ans!r}\n")
