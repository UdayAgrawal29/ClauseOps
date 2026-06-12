# Phase 2: Clause Classification — Implementation Plan

## Goal

Take the segmented `ClauseChunk` objects from Phase 1 and classify each one into one of ~20 legal categories (e.g., PAYMENT, TERMINATION, CONFIDENTIALITY) using a fine-tuned transformer model.

---

## My Independent Research Findings

I cross-checked every major claim in the blueprint and Phase 2 plan. Here's what holds up and what needs correction.

### ✅ VERIFIED: Contracts-BERT is a strong choice

The UNSW paper **arXiv:2508.07849** (May 2026, "Evaluating Customized vs. Generalist Transformer-based Models for Legal Contract Classification") is **real and verified**. It evaluates 13 legal-specific + 9 generalist models across 3 tasks (UNFAIR-ToS, LEDGAR, LEXDEMOD).

Key finding confirmed: **Contracts-BERT and Legal-BERT establish new SOTA on 2/3 tasks despite 69% fewer parameters than the best generalist models.**

The model ID `nlpaueb/bert-base-uncased-contracts` is confirmed available on HuggingFace. It was pre-trained on 76,366 US contracts from SEC EDGAR — the same source as LEDGAR data.

### ✅ VERIFIED: LEDGAR is the right primary dataset

- Available via: `load_dataset("coastalcph/lex_glue", "ledgar")`

> [!WARNING]
> The Phase 2 plan uses `load_dataset("lex_glue", "ledgar")` — this is **wrong**. The correct dataset ID is `"coastalcph/lex_glue"`, not just `"lex_glue"`. I will use the correct one in the script.

- Train: 60k, Validation: 10k, Test: 10k
- 100 label classes (provision types from SEC filings)
- Severe class imbalance confirmed — weighted loss is mandatory

### ✅ VERIFIED: Label collapse (100 → 20) is the right strategy

100 classes is too granular for ClauseOps. Many labels are near-duplicates (e.g., "Fees" and "Payment" both map to PAYMENT). Collapsing to 20 actionable categories:
- Reduces training difficulty
- Increases examples per class
- Produces user-friendly, actionable labels

### ⚠️ CORRECTION: DeBERTa-v3 vs Contracts-BERT — both are valid

The Phase 2 plan says "use Contracts-BERT, not DeBERTa." My research shows:
- **Contracts-BERT wins on LEDGAR** (domain alignment advantage)
- **DeBERTa wins on CUAD-SL** (better attention architecture for span extraction)
- For our task (LEDGAR provision classification), **Contracts-BERT is the justified first choice**

However, DeBERTa-v3-base is a perfectly valid fallback. If Contracts-BERT underperforms on our collapsed 20-category taxonomy, we can swap it in. The training script should support both.

### ✅ VERIFIED: Kaggle is feasible for training

Kaggle provides:
- 1× NVIDIA P100 (16GB VRAM) OR 2× NVIDIA T4 (16GB each)
- 30 hours/week free GPU quota
- 12-hour max session length

BERT-base fine-tuning on 60k examples for 5 epochs takes ~2.5-3.5 hours on T4. Well within limits.

Key Kaggle tips to incorporate:
- Use `fp16=True` for mixed precision (faster, less VRAM)
- Use `gradient_accumulation_steps` if batch size 16 causes OOM
- Save checkpoints to `/kaggle/working/`
- Use dynamic padding (not max_length) to save memory

---

## Segmentation → Classification Data Flow

This is the critical interface. Here's exactly what Phase 1 produces and what Phase 2 consumes:

### Phase 1 Output: `list[ClauseChunk]`

Each `ClauseChunk` has:
```python
clause_id: str          # UUID
heading: Optional[str]  # e.g., "3.2. Royalty" or None
heading_number: Optional[str]  # e.g., "3.2"
body_text: str          # The actual clause text
token_count: int        # Approximate tokens
is_oversized: bool      # True if >480 tokens
chunk_type: str         # "CLAUSE" | "TABLE" | "DEFINITION_GROUP"
sub_chunks: list[str]   # For oversized clauses: split windows
```

### Phase 2 Input: What the classifier needs

```python
# For normal clauses:
text = f"{heading_cleaned}: {body_text}"  # heading prepended

# For oversized clauses:
# Classify each sub_chunk independently, average probabilities

# Skip TABLE and DEFINITION_GROUP chunks (pre-labeled)
```

### Phase 2 Output: Classification result per chunk

```python
{
    "clause_id": "uuid",
    "clause_type": "PAYMENT",         # One of 20 categories
    "confidence": 0.94,               # Softmax probability
    "needs_review": False,            # True if confidence < 0.75
    "alternatives": [],               # Top-3 if uncertain
}
```

---

## Proposed Changes

### Deliverable 1: Kaggle Training Script

#### [NEW] [train_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/scripts/train_classifier.py)

A single Python file structured as cell-wise code (each section marked with `# === CELL N ===`). Designed to be pasted into a Kaggle notebook cell-by-cell.

**Contents (cell-by-cell):**

| Cell | Purpose |
|---|---|
| Cell 1 | Install dependencies (`pip install transformers datasets scikit-learn accelerate`) |
| Cell 2 | Load LEDGAR from HuggingFace (`coastalcph/lex_glue`, `ledgar`) |
| Cell 3 | Explore: print all 100 label names + their frequencies |
| Cell 4 | Define the `LEDGAR_100_TO_CLAUSEOPS_20` mapping dict |
| Cell 5 | Remap dataset: collapse labels, create train/val/test splits |
| Cell 6 | Compute class weights for weighted loss |
| Cell 7 | Load Contracts-BERT tokenizer + model |
| Cell 8 | Define `format_input()` (prepend cleaned heading to body) |
| Cell 9 | Tokenize dataset |
| Cell 10 | Define `WeightedLossTrainer` + `compute_metrics` |
| Cell 11 | Define `TrainingArguments` (Kaggle-optimized: fp16, gradient accumulation) |
| Cell 12 | Train! |
| Cell 13 | Evaluate on test set, print `classification_report` |
| Cell 14 | Save model + tokenizer to `/kaggle/working/clauseops-classifier` |
| Cell 15 | Quick inference test on a sample clause |

---

### Deliverable 2: Label Mapping

#### [NEW] [label_mapping.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/clause_classification/label_mapping.py)

The complete `LEDGAR_100_TO_CLAUSEOPS_20` dictionary. This is the manual mapping of LEDGAR's 100 labels to our 20 actionable categories.

> [!IMPORTANT]
> I need to first run a data exploration cell to see the actual 100 label names from HuggingFace before I can build this mapping accurately. The Phase 2 plan has a placeholder — I will build the real one.

---

### Deliverable 3: Local Inference Module

#### [NEW] [classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/clause_classification/classifier.py)

The inference module that loads the trained model and classifies `ClauseChunk` objects. Follows the same singleton pattern as the Docling converter (load model once, reuse).

**Key features:**
- `classify_clause(chunk: ClauseChunk) → dict` — main entry point
- `classify_oversized(chunk: ClauseChunk) → dict` — sub-chunk averaging
- 3-zone confidence system (HIGH ≥0.75, MEDIUM ≥0.45, LOW <0.45)
- Skips TABLE and DEFINITION_GROUP chunks (pre-labeled)

#### [NEW] [__init__.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/clause_classification/__init__.py)

Module entry point exposing `classify_clause`.

---

## Open Questions

> [!IMPORTANT]
> **Q1: Model choice — Contracts-BERT or train both?**
> The Phase 2 plan recommends Contracts-BERT. My research confirms this is justified. Should I also include a DeBERTa-v3-base variant in the training script so you can compare? (Adds ~30 lines of code, no downside except longer training time.)

> [!IMPORTANT]
> **Q2: Label mapping — do you want to review the 100→20 mapping before training?**
> I need to run Cell 3 (explore LEDGAR labels) first to see the actual 100 label names. Then I'll build the mapping. Do you want me to: (a) build the mapping myself based on the label names, or (b) show you the 100 labels first so you can help decide which map where?

> [!IMPORTANT]
> **Q3: Where will the trained model live?**
> After training on Kaggle, you'll download the model weights. Where should they go locally?
> - Option A: `clauseops/models/clauseops-classifier/` (inside project)
> - Option B: A separate path you specify

---

## Verification Plan

### Automated Tests
1. Run Cell 1-3 locally to verify LEDGAR loads correctly (no GPU needed)
2. Run Cell 4-9 locally to verify tokenization works (no GPU needed)
3. Run full training on Kaggle (GPU needed)
4. After training: run `scripts/test_classifier.py` on our 5 segmented CUAD contracts

### Manual Verification
- Compare predicted clause types against actual headings (e.g., does "11. Governing Law" get classified as GOVERNING_LAW?)
- Check that DEFINITION_GROUP and TABLE chunks are correctly skipped
- Verify confidence scores: common clauses should be >0.75, ambiguous ones should be flagged

---

## Execution Order

1. First: Create `train_classifier.py` with Cell 1-3 (exploration only — no GPU needed)
2. Run Cells 1-3 locally to get the 100 label names
3. Build the `LEDGAR_100_TO_CLAUSEOPS_20` mapping
4. Complete the remaining cells (4-15)
5. Verify the script runs without errors locally (CPU, tiny subset)
6. You copy it to Kaggle and run the full training
7. After training: create `classifier.py` inference module
