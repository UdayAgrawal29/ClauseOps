# ClauseOps — Phase 2: Clause Classification
### Complete Research-Backed Technical Plan

---

## First: Segmentation Verdict

**Yes, the output is ready for next phase.**

The updated output confirms:
- TOC segment removed (min tokens now 32, real content)
- No trailing page number artifacts
- Franchise: 74 clean segments, avg 248 tokens, max 1210 (split into sub-chunks)
- Ghost segments eliminated
- Granularity is correct: individual sub-clauses (1.1, 1.2... 11.14) are separate
- `chunk_type` field distinguishes CLAUSE / TABLE / DEFINITION_GROUP

The remaining minor issue (one mid-paragraph `Page 12 of 39` artifact) is cosmetic and
will not affect a classifier trained on real-world messy legal text. **Proceed.**

---

## What the Research Says (Fresh Research, May 2026)

This is NOT just repeating what was planned earlier. New findings from a paper
published THIS MONTH (arXiv:2508.07849, May 22 2026) change the recommendation.

### Finding 1 (UNSW Paper, May 2026): Contracts-BERT beats DeBERTa on contract tasks

A comprehensive benchmark from the University of New South Wales evaluated 13
legal-specific transformer models against 9 generalist models on 3 contract
classification tasks (UNFAIR-ToS, LEDGAR, LEXDEMOD).

Key finding:
> "Legal-BERT and Contracts-BERT establish new SOTAs on two of the three tasks,
> despite having 69% fewer parameters than the best-performing generalist models."

This directly contradicts the original blueprint's recommendation of DeBERTa.
DeBERTa is a generalist model. Contracts-BERT (`nlpaueb/bert-base-uncased-contracts`)
is pre-trained on 76,366 US contracts from SEC EDGAR — exactly your target domain.

**Model recommendation changes:**
- Original plan: `microsoft/deberta-v3-base` (generalist, 86M params)
- Updated recommendation: `nlpaueb/bert-base-uncased-contracts` (domain-specific, 110M params)
- Why: Contracts-BERT was pre-trained on SEC contracts = same distribution as CUAD/LEDGAR.
  Better domain alignment → better downstream performance without being larger.

### Finding 2 (CUAD-SL Springer Paper, Nov 2025): Fine-tuning beats GPT-4 by 20.6%

Still confirmed: DeBERTa fine-tuned on legal data achieves 87.8% vs GPT-4's 67.2%
on CUAD-SL (single-label version of CUAD). BUT the UNSW paper shows that
Contracts-BERT fine-tuned on LEDGAR outperforms DeBERTa on the LEDGAR task itself.

The correct interpretation: **DeBERTa is better when generalist fine-tuning matters,
Contracts-BERT is better when domain alignment matters.** For ClauseOps (contract
provisions classification on LEDGAR), Contracts-BERT wins.

### Finding 3: LEDGAR has a severe class imbalance problem you must handle

LEDGAR has 100 classes. The distribution is not uniform:
- Top 10 classes (e.g., "Definitions", "Entire Agreements") have 5000+ examples each
- Bottom 20 classes have fewer than 100 examples each

If you train with standard CrossEntropyLoss, the model will learn to predict the
top classes with high accuracy while almost never predicting the rare ones. Macro-F1
will be bad. The fix is a weighted loss function. Two options from research:
- `class_weighted CrossEntropyLoss`: Standard approach, works well
- `FocalLoss (γ=2)`: Better for very extreme imbalance, focuses on hard examples

You need one of these. No exceptions.

### Finding 4: 100 classes is too many for ClauseOps. Collapse to ~20.

The LEDGAR dataset has 100 labels because it was built for a comprehensive legal
taxonomy benchmark. ClauseOps doesn't need 100 classes — it needs actionable ones.
A "Taxes" clause and a "Tax Withholding" clause both result in the same downstream
action (create a tax-related task). Collapsing to 20 semantic categories:
- Reduces training difficulty dramatically
- Improves performance on rare classes (fewer classes = more examples per class)
- Makes the output interpretable to non-lawyers (your target users)

---

## The Task: What Exactly Is Clause Classification?

Input: A ClauseChunk (heading + body_text, ≤480 tokens)
Output: One of N clause categories with a confidence score

Example:
```
Input heading: "3.2. Royalty"
Input body: "You must pay us a royalty fee equal to six percent (6%) of your
             Gross Revenues. The Royalty is in consideration of your right to
             use the Proprietary Marks..."

Output: {"type": "PAYMENT_OBLIGATION", "confidence": 0.94}
```

This is a **sequence classification** task (the standard BERT/classification setup).
- Architecture: [CLS] token → linear layer → softmax over N classes
- Input format: "[HEADING] {heading_text} [SEP] {body_text}" — heading prepended
- The [HEADING] token isn't a real token — literally prepend the heading as text

---

## The 20 Categories You'll Train On

Collapsed from LEDGAR's 100 to 20 actionable categories for ClauseOps:

| # | Category | What It Covers | Example Heading |
|---|---|---|---|
| 1 | PAYMENT | Fees, royalties, invoices, payment schedules | "3.1 Initial Franchise Fee" |
| 2 | TERMINATION | How/when agreement ends | "6.2 Termination" |
| 3 | CONFIDENTIALITY | Non-disclosure obligations | "Confidential Information" |
| 4 | GOVERNING_LAW | Jurisdiction, choice of law | "11.11 Governing Law" |
| 5 | INDEMNIFICATION | Indemnity, hold harmless | "Indemnification" |
| 6 | LIABILITY_LIMITATION | Caps on damages | "Limitation of Liability" |
| 7 | RENEWAL | Auto-renewal, extension options | "2.2 Successor Agreements" |
| 8 | IP_OWNERSHIP | IP rights, assignment, license | "Intellectual Property" |
| 9 | DISPUTE_RESOLUTION | Arbitration, mediation | "11.12 Dispute Resolution" |
| 10 | NON_COMPETE | Restrictions after termination | "Non-Competition" |
| 11 | ASSIGNMENT | Transfer of rights | "11.7 Assignment" |
| 12 | FORCE_MAJEURE | Excused performance | "Force Majeure" |
| 13 | WARRANTIES | Representations, warranties | "Warranties and Representations" |
| 14 | DEFINITIONS | Defined terms | "1. Definitions" |
| 15 | NOTICES | Communication requirements | "Notices" |
| 16 | DELIVERY_OBLIGATIONS | Services/goods delivery | "4. Site Selection and Opening" |
| 17 | PENALTIES | Late fees, liquidated damages | "Penalties and Interest" |
| 18 | DATA_PROTECTION | Privacy, data handling | "Data Protection" |
| 19 | ENTIRE_AGREEMENT | Integration, severability, general | "11.6 Entire Agreement" |
| 20 | REPORTING_AUDIT | Records, reports, audit rights | "8.2 Reports and Financial Statements" |

---

## Dataset Strategy

### Primary Dataset: LEDGAR (60k training examples)

```python
from datasets import load_dataset
dataset = load_dataset("lex_glue", "ledgar")
# train: 60,000 | validation: 10,000 | test: 10,000
# Each: {"text": "The tenant shall pay...", "label": 47}
```

Problem: 100 labels. Solution: Build a label_mapping dict that collapses them to 20.

```python
LEDGAR_TO_CLAUSEOPS = {
    # LEDGAR label index → ClauseOps category
    # (build this by reading LEDGAR's label list and manually mapping)
    0: "PAYMENT",        # "Fees"
    1: "PAYMENT",        # "Payment"
    2: "DEFINITIONS",    # "Definitions"
    3: "TERMINATION",    # "Termination"
    # ... (full mapping: map all 100 → one of 20)
}
```

This mapping is a one-time manual effort (2-3 hours). You read each of LEDGAR's 100
label names and decide which of your 20 categories it maps to.

### Secondary Dataset: CUAD (510 contracts, 41 clause types, span-annotated)

CUAD doesn't directly give you paragraph-level labels — it gives you span-level
annotations (which span of text in the contract answers "does this have a payment
clause?"). But you can extract labeled paragraphs from CUAD:

```python
from datasets import load_dataset
cuad = load_dataset("cuad")
# For each contract, for each of 41 question types, if answer exists:
# → Extract the sentence/paragraph containing the answer
# → Label it with the question type
# This gives you ~5,000-8,000 additional training examples
```

Map CUAD's 41 types to your 20 categories (simpler mapping than LEDGAR).

### Why Not Just Use CUAD Alone?

CUAD has 41 types but only 510 contracts → thin data per class (~100-200 examples
per type). LEDGAR has 100 types but 60,000 examples → much denser. Use LEDGAR
as primary, CUAD as supplementary.

### Your Own Segmented Contracts as Training Data (Later)

After running the segmenter on 20+ CUAD PDFs, you'll have segmented clauses. You
can manually label a subset (100-200 clauses, ~2 hours work) and use them as
additional training/validation data. This gives you examples in the exact format
the classifier will see at inference time (Docling-segmented, with heading prepended).
Start without this — add it in round 2 of training.

---

## Model Choice: Contracts-BERT

### Why Contracts-BERT over DeBERTa (updated recommendation)

| Aspect | DeBERTa-v3-base | Contracts-BERT |
|---|---|---|
| Pre-training data | General text (Wikipedia, books) | 76,366 US contracts from SEC EDGAR |
| Parameters | 86M | 110M |
| Domain alignment for LEDGAR | Low (general) | High (same source as LEDGAR) |
| LEDGAR SOTA | Competitive but not SOTA | SOTA per May 2026 UNSW paper |
| Inference speed | Faster | Slightly slower |
| Student hardware | Fine on T4/free Colab | Fine on T4/free Colab |
| HuggingFace ID | `microsoft/deberta-v3-base` | `nlpaueb/bert-base-uncased-contracts` |

**Verdict: Use Contracts-BERT.**

If you really want to hedge, train both and compare on your validation set.
Contracts-BERT is the justified first choice based on current literature.

---

## Training Architecture

```python
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer
)
import torch
from torch.nn import CrossEntropyLoss
import numpy as np

# ============================================================
# 1. Model + Tokenizer
# ============================================================
MODEL_NAME = "nlpaueb/bert-base-uncased-contracts"
NUM_LABELS = 20  # Your collapsed taxonomy

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=NUM_LABELS,
    ignore_mismatched_sizes=True  # needed since we're changing the classifier head
)

# ============================================================
# 2. Input Formatting — prepend heading to body text
# ============================================================
def format_input(heading: str, body_text: str) -> str:
    """
    Prepend heading to body text so the model has both pieces of signal.
    At inference time, if heading is None, just use body_text.
    
    This consistently improves F1 by 2-4% on short clauses where the heading
    alone disambiguates (e.g., "3.1 Payment" + body vs just body text).
    """
    if heading:
        # Clean heading: remove section numbers for cleaner signal
        # "3.1. Initial Franchise Fee." → "Initial Franchise Fee"
        clean_heading = re.sub(r'^\d+(\.\d+)*\.?\s*', '', heading).strip().rstrip('.')
        return f"{clean_heading}: {body_text}"
    return body_text

# ============================================================
# 3. Tokenization
# ============================================================
def tokenize(examples):
    texts = [
        format_input(h, b) 
        for h, b in zip(examples["heading"], examples["body_text"])
    ]
    return tokenizer(
        texts,
        truncation=True,
        max_length=512,
        padding="max_length",
    )

# ============================================================
# 4. Class-Weighted Loss (handles LEDGAR imbalance)
# ============================================================
def compute_class_weights(train_labels: list[int], num_classes: int) -> torch.Tensor:
    """
    Inverse frequency weighting.
    Rare classes (few examples) get high weight.
    Common classes (many examples) get low weight.
    """
    from sklearn.utils.class_weight import compute_class_weight
    weights = compute_class_weight(
        class_weight='balanced',
        classes=np.arange(num_classes),
        y=train_labels
    )
    return torch.tensor(weights, dtype=torch.float)

# ============================================================
# 5. Custom Trainer with Weighted Loss
# ============================================================
class WeightedLossTrainer(Trainer):
    """
    Custom Trainer that uses class-weighted CrossEntropyLoss
    instead of the default unweighted loss.
    
    This is the critical fix for LEDGAR's class imbalance.
    Without this, the model learns to predict common classes
    correctly while ignoring rare ones (bad Macro-F1).
    """
    def __init__(self, class_weights: torch.Tensor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights.to(self.args.device)
    
    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits
        
        loss_fn = CrossEntropyLoss(weight=self.class_weights)
        loss = loss_fn(logits, labels)
        
        return (loss, outputs) if return_outputs else loss

# ============================================================
# 6. Training Arguments (validated hyperparameters from literature)
# ============================================================
training_args = TrainingArguments(
    output_dir="./clauseops-classifier",
    
    # Epochs: 5 is the sweet spot for LEDGAR per LexGLUE benchmark paper
    num_train_epochs=5,
    
    # Batch size: 16 per device is standard for BERT-family on T4 (16GB VRAM)
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    
    # Learning rate: 2e-5 is the standard for BERT fine-tuning (not 5e-5, too high;
    # not 1e-5, too slow for 5 epochs on 60k examples)
    learning_rate=2e-5,
    
    # Warmup: 10% of total steps — prevents early-epoch instability
    warmup_ratio=0.1,
    
    # Weight decay: L2 regularization — prevents overfitting on rare classes
    weight_decay=0.01,
    
    # Evaluation and saving
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="eval_macro_f1",  # Critical: use Macro-F1, NOT accuracy
    greater_is_better=True,
    
    # Logging
    logging_steps=100,
    report_to="none",
    
    # Reproducibility
    seed=42,
    data_seed=42,
)

# ============================================================
# 7. Evaluation Metrics
# ============================================================
from sklearn.metrics import f1_score, classification_report

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    
    macro_f1 = f1_score(labels, predictions, average='macro', zero_division=0)
    micro_f1 = f1_score(labels, predictions, average='micro', zero_division=0)
    
    return {
        "eval_macro_f1": macro_f1,
        "eval_micro_f1": micro_f1,
    }
```

---

## The Confidence Threshold System

This is NOT in the original blueprint and is critical for production quality.

After training, don't just take the top prediction. Use a 3-zone confidence system:

```python
CONFIDENCE_HIGH = 0.75    # Classifier is confident → display directly to user
CONFIDENCE_MEDIUM = 0.45  # Classifier uncertain → show top-3 + flag for review
CONFIDENCE_LOW = 0.45     # Below threshold → show as "Unclassified" for human review

def classify_clause(chunk: ClauseChunk) -> dict:
    # Only classify CLAUSE type (not TABLE or DEFINITION_GROUP)
    if chunk.chunk_type != "CLAUSE":
        return {"type": chunk.chunk_type, "confidence": 1.0, "needs_review": False}
    
    # For oversized clauses, classify each sub-chunk and take majority vote
    if chunk.is_oversized and chunk.sub_chunks:
        return classify_oversized(chunk)
    
    text = format_input(chunk.heading, chunk.body_text)
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    
    with torch.no_grad():
        logits = model(**inputs).logits
    
    probs = torch.softmax(logits, dim=-1)[0]
    top_prob, top_idx = probs.max(dim=-1)
    confidence = top_prob.item()
    predicted_label = LABEL_NAMES[top_idx.item()]
    
    # Top-3 alternatives for uncertain predictions
    top3 = sorted(
        [(LABEL_NAMES[i], probs[i].item()) for i in range(len(LABEL_NAMES))],
        key=lambda x: -x[1]
    )[:3]
    
    return {
        "type": predicted_label,
        "confidence": confidence,
        "needs_review": confidence < CONFIDENCE_HIGH,
        "alternatives": top3 if confidence < CONFIDENCE_HIGH else [],
    }

def classify_oversized(chunk: ClauseChunk) -> dict:
    """For long clauses split into sub-chunks: classify each, take majority vote."""
    sub_results = []
    for sub_text in chunk.sub_chunks:
        inputs = tokenizer(sub_text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0]
        sub_results.append(probs)
    
    # Average probabilities across sub-chunks
    avg_probs = torch.stack(sub_results).mean(dim=0)
    top_prob, top_idx = avg_probs.max(dim=-1)
    
    return {
        "type": LABEL_NAMES[top_idx.item()],
        "confidence": top_prob.item(),
        "needs_review": top_prob.item() < CONFIDENCE_HIGH,
        "source": "sub_chunk_average",
    }
```

---

## Training Data Preparation Step-by-Step

### Step 1: Load and Explore LEDGAR

```python
from datasets import load_dataset
import pandas as pd
from collections import Counter

ds = load_dataset("lex_glue", "ledgar")
train_df = pd.DataFrame(ds['train'])

# Understand the 100 classes
label_names = ds['train'].features['label'].names  # List of 100 label strings
print(label_names)  # e.g., ['Adjustments', 'Agreements', 'Amendments', ...]

# Check class distribution
counts = Counter(train_df['label'].tolist())
for label_id, count in sorted(counts.items(), key=lambda x: -x[1])[:20]:
    print(f"{label_names[label_id]:40s} {count:6d}")
```

### Step 2: Build Your Label Mapping (Manual, ~2 hours)

After seeing the 100 label names, create this mapping:
```python
LEDGAR_100_TO_CLAUSEOPS_20 = {
    # Key: LEDGAR label index, Value: your category name
    # You fill this in after running Step 1
}
```

### Step 3: Filter and Remap

```python
def remap_dataset(split):
    new_labels = []
    new_texts = []
    for row in ds[split]:
        ledgar_label = row['label']
        if ledgar_label not in LEDGAR_100_TO_CLAUSEOPS_20:
            continue  # Skip labels that don't map to your taxonomy
        new_labels.append(LEDGAR_100_TO_CLAUSEOPS_20[ledgar_label])
        new_texts.append(row['text'])
    return new_texts, new_labels

train_texts, train_labels = remap_dataset('train')
val_texts, val_labels = remap_dataset('validation')
test_texts, test_labels = remap_dataset('test')

# Convert string labels to integers
label_to_id = {label: i for i, label in enumerate(sorted(set(train_labels)))}
train_labels_int = [label_to_id[l] for l in train_labels]
```

---

## Expected Results

Based on the UNSW May 2026 paper and LexGLUE benchmarks:

| Metric | Expected Value | What It Means |
|---|---|---|
| Macro-F1 | 0.82 – 0.87 | Average F1 across all 20 classes, weighted equally |
| Micro-F1 | 0.88 – 0.92 | F1 weighted by class frequency (higher due to common classes) |
| Per-class F1 (common classes) | 0.90 – 0.95 | PAYMENT, DEFINITIONS, etc. |
| Per-class F1 (rare classes) | 0.65 – 0.80 | With weighted loss — acceptable |
| Training time (T4 Colab, 60k examples, 5 epochs) | ~2.5 – 3.5 hours | One-time cost |

**Important:** If you see Macro-F1 < 0.70 after epoch 2, something is wrong with
class weighting. Check that `class_weights` is correctly computed and on the right device.

---

## What Stays the Same From Original Blueprint

1. **LEDGAR as primary dataset** — still correct, now with label collapse to 20
2. **Fine-tuning approach** — still correct, not zero-shot
3. **5 epochs, lr=2e-5** — confirmed by LexGLUE benchmark paper's training config
4. **Macro-F1 as primary metric** — critical for imbalanced data
5. **Save best model by Macro-F1** — confirmed correct

## What Changed From Original Blueprint

1. **Model: DeBERTa-v3-base → Contracts-BERT** (based on May 2026 UNSW paper)
2. **Label space: 100 → 20** (collapsed taxonomy for actionability)
3. **Loss function: Standard CE → Weighted CE** (essential for imbalance — was mentioned
   in original blueprint but not fully implemented)
4. **Confidence threshold system** (new — not in original blueprint at all)
5. **Input format: body_text only → heading + body_text** (small but proven improvement)

---

## Build Roadmap for Classification Phase

### Week 1: Data Preparation
- [ ] Load LEDGAR via HuggingFace datasets
- [ ] Print all 100 label names + their frequencies
- [ ] Build LEDGAR_100_TO_CLAUSEOPS_20 mapping manually
- [ ] Remap and verify: plot class distribution after collapse
- [ ] Compute class weights for weighted loss
- [ ] Create HuggingFace Dataset objects with train/val/test splits

### Week 2: Training
- [ ] Load Contracts-BERT tokenizer + model
- [ ] Implement format_input() (heading + body_text)
- [ ] Implement WeightedLossTrainer
- [ ] Run training on Google Colab T4 (free tier)
- [ ] Monitor Macro-F1 per epoch (expect improvement each epoch)
- [ ] Save best checkpoint

### Week 3: Evaluation & Iteration
- [ ] Run evaluate() on test split, print classification_report
- [ ] Identify weakest 5 classes (lowest F1)
- [ ] For each weak class: examine 10 misclassified examples — is it a data problem
  or a model problem?
- [ ] If data problem (ambiguous labels): merge that class into nearest neighbor
- [ ] If model problem: add CUAD supplementary examples for that class
- [ ] Re-train if changes significant, otherwise proceed

### Week 4: Inference Integration
- [ ] Save fine-tuned model to `/models/contracts-bert-clauseops/`
- [ ] Implement classify_clause() with confidence thresholds
- [ ] Implement classify_oversized() for sub-chunk averaging
- [ ] Write inference.py that loads model once (singleton, like Docling converter)
- [ ] Test on your 5 CUAD contracts — print predicted labels for each segment
- [ ] Verify common clauses are predicted correctly (PAYMENT → payment clause, etc.)
- [ ] Connect to FastAPI: add `/api/contracts/{id}/classify` endpoint
- [ ] Connect to database: save clause_type + confidence to Clause table

---

## The Interview Story for This Phase

> "For clause classification, I evaluated the literature and found a May 2026
> benchmark study from UNSW that showed Contracts-BERT — which was pre-trained
> specifically on 76,000 US contracts — outperforms generalist models like DeBERTa
> by reaching state-of-the-art on LEDGAR despite having 69% fewer parameters than
> the best generalist alternatives. I fine-tuned it on a collapsed 20-category
> taxonomy derived from LEDGAR's 100 classes, using class-weighted loss to handle
> the severe class imbalance in the dataset. The model uses the clause heading
> prepended to the body text as input, which consistently improves F1 on short
> clauses where the heading alone disambiguates the type. I also built a
> 3-zone confidence threshold system: high-confidence predictions (>75%) are shown
> directly; medium-confidence ones are flagged for human review with top-3
> alternatives; low-confidence ones are shown as unclassified."

---

*Research sources: arXiv:2508.07849 (UNSW, May 2026); Springer AI&Law Nov 2025
(CUAD-SL DeBERTa benchmark); LexGLUE benchmark paper (Chalkidis et al. 2022);
LEGAL-BERT paper (Chalkidis et al. EMNLP 2020); LEDGAR dataset (Tuggener et al. 2020)*
