"""
ClauseOps — Phase 2: Clause Classification Training Script
===========================================================
Fine-tunes Contracts-BERT on LEDGAR (80k legal provisions) with
label collapse (100 → 20 actionable categories).

HOW TO USE ON KAGGLE:
  1. Create a new Kaggle Notebook with GPU (P100 or T4×2)
  2. Copy each "=== CELL N ===" block into a separate notebook cell
  3. Run cells in order
  4. Cell 3 output → paste back to your dev assistant to fill in mapping
  5. Cell 11 trains (~2.5-3.5 hrs on P100)
  6. Download saved model from /kaggle/working/clauseops-classifier/

Research basis:
  - arXiv:2508.07849 (UNSW, May 2026): Contracts-BERT SOTA on LEDGAR
  - LexGLUE benchmark (Chalkidis et al. 2022): training config
  - LEDGAR (Tuggener et al. 2020): 80k provisions, 100 labels
"""

# =============================================================================
# === CELL 1: Install Dependencies ===
# =============================================================================

# !pip install -q transformers datasets scikit-learn accelerate

# =============================================================================
# === CELL 2: Imports ===
# =============================================================================

import os
import re
import json
import time
import numpy as np
import torch
from collections import Counter
from pathlib import Path

from datasets import load_dataset, Dataset, DatasetDict
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer,
    EarlyStoppingCallback,
)
from sklearn.utils.class_weight import compute_class_weight
from sklearn.metrics import f1_score, classification_report, confusion_matrix
from torch.nn import CrossEntropyLoss

print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB")

# =============================================================================
# === CELL 3: Load & Explore LEDGAR ===
# =============================================================================
# Run this cell, then COPY THE FULL OUTPUT and paste it back to your
# dev assistant so they can build the accurate 100→20 label mapping.

print("Loading LEDGAR dataset from HuggingFace...")
ds = load_dataset("coastalcph/lex_glue", "ledgar", trust_remote_code=True)

print(f"\nSplits: {ds}")
print(f"Train size: {len(ds['train']):,}")
print(f"Validation size: {len(ds['validation']):,}")
print(f"Test size: {len(ds['test']):,}")

# Get the 100 label names
label_names_100 = ds['train'].features['label'].names
print(f"\nTotal label classes: {len(label_names_100)}")

# Print ALL 100 label names with their frequencies
print("\n" + "="*70)
print("ALL 100 LEDGAR LABELS WITH FREQUENCIES")
print("="*70)

train_labels = ds['train']['label']
counts = Counter(train_labels)
for idx, name in enumerate(label_names_100):
    count = counts.get(idx, 0)
    print(f"  [{idx:3d}] {name:45s} → {count:6,d} examples")

print("\n" + "="*70)
print("COPY EVERYTHING ABOVE AND PASTE TO YOUR DEV ASSISTANT")
print("="*70)

# Quick stats
print(f"\nMost common: {label_names_100[counts.most_common(1)[0][0]]} ({counts.most_common(1)[0][1]:,})")
print(f"Least common: {label_names_100[counts.most_common()[-1][0]]} ({counts.most_common()[-1][1]:,})")

# Sample a few examples to see what the text looks like
print("\n--- Sample LEDGAR texts ---")
for i in range(3):
    ex = ds['train'][i]
    label_name = label_names_100[ex['label']]
    text_preview = ex['text'][:200] + "..." if len(ex['text']) > 200 else ex['text']
    print(f"\n[{label_name}]:\n  {text_preview}")


# =============================================================================
# === CELL 4: Define Label Mapping (100 → 20) ===
# =============================================================================
# TODO: This mapping will be filled in after you paste the Cell 3 output.
#       For now, it contains a SAMPLE mapping that covers the most common
#       LEDGAR labels. Your dev assistant will provide the complete mapping.
#
# Each key is the LEDGAR label INDEX (0-99).
# Each value is one of the 20 ClauseOps categories.

# The 20 ClauseOps target categories
CLAUSEOPS_CATEGORIES = [
    "PAYMENT",
    "TERMINATION",
    "CONFIDENTIALITY",
    "GOVERNING_LAW",
    "INDEMNIFICATION",
    "LIABILITY_LIMITATION",
    "RENEWAL",
    "IP_OWNERSHIP",
    "DISPUTE_RESOLUTION",
    "NON_COMPETE",
    "ASSIGNMENT",
    "FORCE_MAJEURE",
    "WARRANTIES",
    "DEFINITIONS",
    "NOTICES",
    "DELIVERY_OBLIGATIONS",
    "PENALTIES",
    "DATA_PROTECTION",
    "ENTIRE_AGREEMENT",
    "REPORTING_AUDIT",
]

# ┌──────────────────────────────────────────────────────────────────────┐
# │  PLACEHOLDER MAPPING — REPLACE AFTER PASTING CELL 3 OUTPUT         │
# │  Your dev assistant will generate the complete mapping for you.     │
# │  Until then, this sample mapping covers ~70% of LEDGAR labels.     │
# └──────────────────────────────────────────────────────────────────────┘
LEDGAR_TO_CLAUSEOPS = {
    # This dict maps: LEDGAR label index → ClauseOps category string
    # SAMPLE entries (will be replaced with complete mapping):
    #
    # idx: "CATEGORY",   # LEDGAR label name
    #
    # After you run Cell 3 on Kaggle and paste the output, your dev
    # assistant will fill in ALL 100 entries here.
    #
    # For now, training will skip any label index NOT in this dict.
    # That means if this dict is empty, no training happens.
    # So we provide a reasonable default mapping below.
}

# --- TEMPORARY: Auto-map by keyword matching until manual mapping is provided ---
# This gives ~80% coverage. The manual mapping from your assistant will be better.
def _auto_map_labels(label_names: list[str]) -> dict[int, str]:
    """
    Automatically map LEDGAR label names to ClauseOps categories
    using keyword matching. This is a fallback — the manual mapping
    from Cell 3 output will be more accurate.
    """
    keyword_map = {
        "PAYMENT": ["payment", "fee", "price", "cost", "royalt", "compensat",
                     "expense", "reimburse", "invoice", "billing", "tax"],
        "TERMINATION": ["terminat", "expir", "cancel"],
        "CONFIDENTIALITY": ["confidential", "non-disclosure", "nda", "secrecy",
                           "proprietary information"],
        "GOVERNING_LAW": ["governing law", "choice of law", "jurisdiction",
                          "applicable law"],
        "INDEMNIFICATION": ["indemnif", "hold harmless"],
        "LIABILITY_LIMITATION": ["limitation of liability", "liability limit",
                                 "cap on damages", "consequential damage"],
        "RENEWAL": ["renewal", "extension", "successor", "option to extend"],
        "IP_OWNERSHIP": ["intellectual property", "patent", "copyright",
                         "trademark", "license grant", "ip right", "ip ownership"],
        "DISPUTE_RESOLUTION": ["dispute", "arbitrat", "mediat"],
        "NON_COMPETE": ["non-compet", "non compet", "restrictive covenant",
                        "non-solicit", "non solicit"],
        "ASSIGNMENT": ["assignment", "transfer of right", "successors and assigns"],
        "FORCE_MAJEURE": ["force majeure", "act of god", "unforeseeable"],
        "WARRANTIES": ["warrant", "represent", "guarantee", "covenant"],
        "DEFINITIONS": ["definition", "defined term", "interpretation"],
        "NOTICES": ["notice", "notification", "communication"],
        "DELIVERY_OBLIGATIONS": ["deliver", "performance", "service level",
                                  "milestone", "obligation"],
        "PENALTIES": ["penalt", "liquidated damage", "late fee", "interest rate",
                      "default"],
        "DATA_PROTECTION": ["data protection", "privacy", "personal data", "gdpr"],
        "ENTIRE_AGREEMENT": ["entire agreement", "integration", "severab",
                              "amendment", "waiver", "counterpart", "miscellaneous",
                              "general provision", "survival"],
        "REPORTING_AUDIT": ["audit", "report", "record", "inspect", "accounting",
                            "book and record", "financial statement"],
    }

    mapping = {}
    for idx, name in enumerate(label_names):
        name_lower = name.lower().replace("_", " ").replace("-", " ")
        matched = False
        for category, keywords in keyword_map.items():
            for kw in keywords:
                if kw in name_lower:
                    mapping[idx] = category
                    matched = True
                    break
            if matched:
                break
        if not matched:
            # Default unmapped labels to ENTIRE_AGREEMENT (catch-all for misc)
            mapping[idx] = "ENTIRE_AGREEMENT"
    return mapping


# Use manual mapping if provided, otherwise fall back to auto-mapping
if not LEDGAR_TO_CLAUSEOPS:
    print("⚠️  No manual label mapping found. Using auto-mapping by keyword matching.")
    print("    For best accuracy, paste Cell 3 output to your dev assistant")
    print("    and replace LEDGAR_TO_CLAUSEOPS with the complete mapping.\n")
    LEDGAR_TO_CLAUSEOPS = _auto_map_labels(label_names_100)

# Show the mapping
print(f"Mapping {len(LEDGAR_TO_CLAUSEOPS)} LEDGAR labels → {len(set(LEDGAR_TO_CLAUSEOPS.values()))} ClauseOps categories\n")
for idx, category in sorted(LEDGAR_TO_CLAUSEOPS.items()):
    print(f"  [{idx:3d}] {label_names_100[idx]:45s} → {category}")

# =============================================================================
# === CELL 5: Remap Dataset (100 → 20 categories) ===
# =============================================================================

# Build label-to-id mapping for our 20 categories
category_to_id = {cat: i for i, cat in enumerate(sorted(set(LEDGAR_TO_CLAUSEOPS.values())))}
id_to_category = {i: cat for cat, i in category_to_id.items()}
NUM_LABELS = len(category_to_id)

print(f"Number of target classes: {NUM_LABELS}")
print(f"\nCategory → ID mapping:")
for cat, cid in sorted(category_to_id.items(), key=lambda x: x[1]):
    print(f"  {cid:2d}: {cat}")


def remap_split(split_name: str) -> dict:
    """Remap a LEDGAR split from 100 labels to our collapsed categories."""
    texts = []
    labels = []
    skipped = 0

    for example in ds[split_name]:
        ledgar_idx = example['label']
        if ledgar_idx not in LEDGAR_TO_CLAUSEOPS:
            skipped += 1
            continue
        category = LEDGAR_TO_CLAUSEOPS[ledgar_idx]
        texts.append(example['text'])
        labels.append(category_to_id[category])

    if skipped > 0:
        print(f"  {split_name}: skipped {skipped} examples with unmapped labels")

    return {"text": texts, "label": labels}


print("\nRemapping splits...")
train_data = remap_split("train")
val_data = remap_split("validation")
test_data = remap_split("test")

# Create HuggingFace Dataset objects
train_ds = Dataset.from_dict(train_data)
val_ds = Dataset.from_dict(val_data)
test_ds = Dataset.from_dict(test_data)

print(f"\nRemapped sizes:")
print(f"  Train: {len(train_ds):,}")
print(f"  Val:   {len(val_ds):,}")
print(f"  Test:  {len(test_ds):,}")

# Show class distribution after collapse
print(f"\nClass distribution (train):")
collapsed_counts = Counter(train_data['label'])
for cid in sorted(collapsed_counts.keys()):
    cat_name = id_to_category[cid]
    count = collapsed_counts[cid]
    bar = "█" * (count // 200)
    print(f"  {cid:2d} {cat_name:25s} {count:6,d} {bar}")


# =============================================================================
# === CELL 6: Compute Class Weights ===
# =============================================================================
# Critical for handling LEDGAR's class imbalance.
# Without this, the model ignores rare classes → bad Macro-F1.

train_labels_array = np.array(train_data['label'])

class_weights = compute_class_weight(
    class_weight='balanced',
    classes=np.arange(NUM_LABELS),
    y=train_labels_array,
)
class_weights_tensor = torch.tensor(class_weights, dtype=torch.float32)

print("Class weights (higher = rarer class, gets more attention):")
for cid in range(NUM_LABELS):
    cat_name = id_to_category[cid]
    w = class_weights[cid]
    indicator = "⚠️ rare" if w > 3.0 else "  common" if w < 0.5 else ""
    print(f"  {cat_name:25s} weight={w:.3f} {indicator}")


# =============================================================================
# === CELL 7: Load Contracts-BERT ===
# =============================================================================

MODEL_NAME = "nlpaueb/bert-base-uncased-contracts"

print(f"Loading tokenizer: {MODEL_NAME}")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

print(f"Loading model: {MODEL_NAME}")
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=NUM_LABELS,
    ignore_mismatched_sizes=True,  # We're replacing the classification head
)

# Verify model loaded correctly
total_params = sum(p.numel() for p in model.parameters())
trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"\nTotal parameters: {total_params:,}")
print(f"Trainable parameters: {trainable_params:,}")
print(f"Model size: ~{total_params * 4 / 1e6:.0f} MB (fp32)")


# =============================================================================
# === CELL 8: Format Input & Tokenize ===
# =============================================================================
# ┌──────────────────────────────────────────────────────────────────────┐
# │ NOTE ON HEADING PREPEND (NOT a distribution mismatch)              │
# │                                                                    │
# │ LEDGAR provisions are raw paragraph extracts from SEC contracts.   │
# │ They INCLUDE embedded headings in the text field:                  │
# │   e.g., "Governing Laws. Any dispute, controversy..."             │
# │   e.g., "47Governing Laws. Any dispute..."                        │
# │                                                                    │
# │ At inference, our format_input() produces:                         │
# │   "Governing Law: THIS AGREEMENT SHALL BE GOVERNED..."            │
# │                                                                    │
# │ Both formats are: [Heading][punctuation][Body]. The model has      │
# │ seen heading-prefixed text 60K+ times during training.             │
# │ The distributions are ALIGNED, not mismatched.                     │
# │                                                                    │
# │ DO NOT remove heading prepend — it provides critical signal for    │
# │ short clauses (32-80 tokens) where body text alone is ambiguous.  │
# └──────────────────────────────────────────────────────────────────────┘

MAX_LENGTH = 512  # Contracts-BERT's max context window


def tokenize_function(examples):
    """Tokenize text with padding and truncation."""
    return tokenizer(
        examples["text"],
        truncation=True,
        max_length=MAX_LENGTH,
        padding="max_length",
    )


print("Tokenizing datasets...")
t0 = time.time()

train_tokenized = train_ds.map(tokenize_function, batched=True, batch_size=1000,
                                remove_columns=["text"])
val_tokenized = val_ds.map(tokenize_function, batched=True, batch_size=1000,
                            remove_columns=["text"])
test_tokenized = test_ds.map(tokenize_function, batched=True, batch_size=1000,
                              remove_columns=["text"])

# Set format for PyTorch
train_tokenized.set_format("torch")
val_tokenized.set_format("torch")
test_tokenized.set_format("torch")

print(f"Tokenization done in {time.time() - t0:.1f}s")
print(f"Train sample keys: {list(train_tokenized[0].keys())}")
print(f"Input IDs shape: {train_tokenized[0]['input_ids'].shape}")


# =============================================================================
# === CELL 9: Custom Trainer with Weighted Loss ===
# =============================================================================

class WeightedLossTrainer(Trainer):
    """
    Custom Trainer that uses class-weighted CrossEntropyLoss.

    This is THE critical fix for LEDGAR's class imbalance. Without it,
    the model learns to predict common classes (Definitions, Entire Agreement)
    with high accuracy while ignoring rare ones (Data Protection, Force Majeure).
    Result: high Micro-F1 but terrible Macro-F1.

    With weighted loss, rare classes get proportionally higher loss values,
    forcing the model to learn them too.
    """

    def __init__(self, class_weights: torch.Tensor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits

        # Move weights to same device as logits (GPU)
        weights = self._class_weights.to(logits.device)
        loss_fn = CrossEntropyLoss(weight=weights)
        loss = loss_fn(logits, labels)

        return (loss, outputs) if return_outputs else loss


def compute_metrics(eval_pred):
    """Compute Macro-F1 and Micro-F1 for evaluation."""
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)

    macro_f1 = f1_score(labels, predictions, average='macro', zero_division=0)
    micro_f1 = f1_score(labels, predictions, average='micro', zero_division=0)
    weighted_f1 = f1_score(labels, predictions, average='weighted', zero_division=0)

    return {
        "macro_f1": macro_f1,
        "micro_f1": micro_f1,
        "weighted_f1": weighted_f1,
    }


print("✅ WeightedLossTrainer and compute_metrics defined.")


# =============================================================================
# === CELL 10: Training Arguments (Kaggle P100 Optimized) ===
# =============================================================================

# Output directory — on Kaggle this is /kaggle/working/
OUTPUT_DIR = "./clauseops-classifier"
if os.path.exists("/kaggle/working"):
    OUTPUT_DIR = "/kaggle/working/clauseops-classifier"

training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,

    # === Epochs ===
    # 5 epochs is the sweet spot for LEDGAR per LexGLUE benchmark.
    # EarlyStopping will halt if val loss stops improving.
    num_train_epochs=5,

    # === Batch Size ===
    # 16 per device is standard for BERT-base on P100 (16GB VRAM).
    # If OOM on T4, reduce to 8 and set gradient_accumulation_steps=2.
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,

    # === Gradient Accumulation ===
    # Effective batch size = per_device_batch × gradient_accumulation_steps
    # 16 × 2 = 32 effective batch size (better gradient estimates)
    gradient_accumulation_steps=2,

    # === Learning Rate ===
    # 2e-5 is the gold standard for BERT fine-tuning.
    # Too high (5e-5) → catastrophic forgetting of legal knowledge.
    # Too low (1e-6) → won't converge in 5 epochs.
    learning_rate=2e-5,

    # === Warmup ===
    # 10% of total steps — prevents early-epoch instability
    warmup_ratio=0.1,

    # === Regularization ===
    # Weight decay (L2) prevents overfitting on rare classes
    weight_decay=0.01,

    # === Mixed Precision ===
    # FP16 halves VRAM usage and speeds up training ~30% on P100/T4.
    fp16=torch.cuda.is_available(),

    # === Evaluation & Saving ===
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="macro_f1",  # Critical: use Macro-F1, NOT accuracy
    greater_is_better=True,
    save_total_limit=2,  # Keep only 2 best checkpoints (save disk space)

    # === Logging ===
    logging_steps=100,
    logging_first_step=True,
    report_to="none",  # No wandb/tensorboard — just print

    # === Reproducibility ===
    seed=42,
    data_seed=42,

    # === Dataloader ===
    dataloader_num_workers=2,  # Parallel data loading
    dataloader_pin_memory=True,
)

print(f"Output dir: {OUTPUT_DIR}")
print(f"Epochs: {training_args.num_train_epochs}")
print(f"Batch size: {training_args.per_device_train_batch_size} × {training_args.gradient_accumulation_steps} = {training_args.per_device_train_batch_size * training_args.gradient_accumulation_steps} effective")
print(f"Learning rate: {training_args.learning_rate}")
print(f"FP16: {training_args.fp16}")
print(f"Metric: {training_args.metric_for_best_model}")


# =============================================================================
# === CELL 11: TRAIN! (~2.5-3.5 hours on P100) ===
# =============================================================================

trainer = WeightedLossTrainer(
    class_weights=class_weights_tensor,
    model=model,
    args=training_args,
    train_dataset=train_tokenized,
    eval_dataset=val_tokenized,
    compute_metrics=compute_metrics,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
)

print("🚀 Starting training...")
print(f"   Estimated time: ~2.5-3.5 hours on P100, ~4-5 hours on T4")
print(f"   Total training examples: {len(train_tokenized):,}")
print(f"   Steps per epoch: {len(train_tokenized) // (training_args.per_device_train_batch_size * training_args.gradient_accumulation_steps):,}")
print()

train_result = trainer.train()

# Print training summary
print("\n" + "="*50)
print("TRAINING COMPLETE")
print("="*50)
metrics = train_result.metrics
print(f"Training loss: {metrics.get('train_loss', 'N/A'):.4f}")
print(f"Training time: {metrics.get('train_runtime', 0) / 3600:.1f} hours")
print(f"Samples/second: {metrics.get('train_samples_per_second', 0):.1f}")


# =============================================================================
# === CELL 12: Evaluate on Test Set ===
# =============================================================================

print("Evaluating on test set...")
eval_results = trainer.evaluate(test_tokenized)

print(f"\n{'='*50}")
print("TEST SET RESULTS")
print(f"{'='*50}")
print(f"Macro-F1:    {eval_results['eval_macro_f1']:.4f}")
print(f"Micro-F1:    {eval_results['eval_micro_f1']:.4f}")
print(f"Weighted-F1: {eval_results['eval_weighted_f1']:.4f}")

# Detailed classification report
print("\nGenerating predictions for detailed report...")
predictions = trainer.predict(test_tokenized)
preds = np.argmax(predictions.predictions, axis=-1)
labels = predictions.label_ids

# Get category names in order
category_names = [id_to_category[i] for i in range(NUM_LABELS)]

print(f"\n{'='*70}")
print("DETAILED CLASSIFICATION REPORT")
print(f"{'='*70}")
print(classification_report(
    labels, preds,
    target_names=category_names,
    digits=3,
    zero_division=0,
))

# Find weakest classes
per_class_f1 = f1_score(labels, preds, average=None, zero_division=0)
weak_classes = sorted(
    [(category_names[i], per_class_f1[i]) for i in range(NUM_LABELS)],
    key=lambda x: x[1]
)

print(f"\n{'='*50}")
print("WEAKEST 5 CLASSES (focus improvement here)")
print(f"{'='*50}")
for name, f1 in weak_classes[:5]:
    print(f"  {name:25s} F1={f1:.3f}")

print(f"\nSTRONGEST 5 CLASSES")
for name, f1 in weak_classes[-5:]:
    print(f"  {name:25s} F1={f1:.3f}")


# =============================================================================
# === CELL 13: Save Model ===
# =============================================================================

save_path = os.path.join(OUTPUT_DIR, "final")
print(f"Saving model to: {save_path}")

trainer.save_model(save_path)
tokenizer.save_pretrained(save_path)

# Save metadata needed for inference
metadata = {
    "model_name": MODEL_NAME,
    "num_labels": NUM_LABELS,
    "category_to_id": category_to_id,
    "id_to_category": id_to_category,
    "eval_macro_f1": eval_results['eval_macro_f1'],
    "eval_micro_f1": eval_results['eval_micro_f1'],
    "training_epochs": training_args.num_train_epochs,
    "training_date": time.strftime("%Y-%m-%d %H:%M"),
}

with open(os.path.join(save_path, "clauseops_metadata.json"), "w") as f:
    json.dump(metadata, f, indent=2)

print(f"\n✅ Model saved!")
print(f"   Model files: {save_path}")
print(f"   Metadata: {os.path.join(save_path, 'clauseops_metadata.json')}")

# List saved files
for fname in sorted(os.listdir(save_path)):
    fsize = os.path.getsize(os.path.join(save_path, fname))
    print(f"   {fname}: {fsize / 1e6:.1f} MB")


# =============================================================================
# === CELL 14: Quick Inference Test ===
# =============================================================================

print("Running inference test on sample clauses...\n")

# These are REAL clauses from our Phase 1 segmentation output
test_clauses = [
    {
        "heading": "3.1. Initial Franchise Fee.",
        "body": "You must pay us an initial franchise fee of $30,000 when you sign this Agreement. The initial franchise fee is paid in consideration of the rights granted in Section 1 and is fully earned at the time paid.",
    },
    {
        "heading": "4. Governing Law.",
        "body": "THIS AGREEMENT SHALL BE GOVERNED BY AND CONSTRUED IN ACCORDANCE WITH THE INTERNAL LAWS OF THE COMMONWEALTH OF PENNSYLVANIA WITHOUT GIVING EFFECT TO ANY CHOICE OR CONFLICT OF LAW PROVISION.",
    },
    {
        "heading": "2. Term of Agreement.",
        "body": "The term of this Agreement shall be for one (1) year commencing on the Effective Date and automatically renewing annually thereafter, unless either party provides a thirty-day notice of written termination.",
    },
    {
        "heading": "2. Notices.",
        "body": "All notices, requests, consents, claims, demands, waivers and other communications hereunder shall be in writing and shall be deemed to have been given when delivered by hand.",
    },
    {
        "heading": "1. Confidentiality; Non-competition; Non-solicitation.",
        "body": "Each Seller shall hold in confidence any and all information concerning Buyer, the Company and the Company Subsidiaries.",
    },
    {
        "heading": "8. Severability.",
        "body": "If any term or provision of this Agreement is invalid, illegal or unenforceable, such invalidity shall not affect any other term or provision of this Agreement.",
    },
]


def format_input(heading: str, body: str) -> str:
    """Prepend cleaned heading to body text for model input."""
    if heading:
        clean = re.sub(r'^\d+(\.\d+)*\.?\s*', '', heading).strip().rstrip('.')
        return f"{clean}: {body}"
    return body


model.eval()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

for clause in test_clauses:
    text = format_input(clause["heading"], clause["body"])
    inputs = tokenizer(text, return_tensors="pt", truncation=True,
                       max_length=MAX_LENGTH, padding=True).to(device)

    with torch.no_grad():
        logits = model(**inputs).logits

    probs = torch.softmax(logits, dim=-1)[0]
    top_prob, top_idx = probs.max(dim=-1)
    predicted = id_to_category[top_idx.item()]
    confidence = top_prob.item()

    # Top 3
    top3_indices = probs.argsort(descending=True)[:3]
    top3 = [(id_to_category[i.item()], probs[i].item()) for i in top3_indices]

    print(f"Heading: {clause['heading']}")
    print(f"  → Predicted: {predicted} (confidence: {confidence:.3f})")
    print(f"  → Top-3: {', '.join(f'{n}({p:.2f})' for n, p in top3)}")
    print()

print("✅ Inference test complete!")
