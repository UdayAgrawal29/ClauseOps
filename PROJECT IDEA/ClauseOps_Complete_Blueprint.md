# ClauseOps — Complete Technical Blueprint
### AI-Powered Legal Contract Intelligence System
**Research-backed. Production-grade. Built from scratch.**

---

## Table of Contents

1. [Refined Problem Statement](#1-refined-problem-statement)
2. [What the Research Says](#2-what-the-research-says)
3. [System Scope & Target User](#3-system-scope--target-user)
4. [ML Pipeline — The Core Engine](#4-ml-pipeline--the-core-engine)
   - 4.1 The 4 NLP Tasks You Need to Solve
   - 4.2 Datasets (What to Use and Why)
   - 4.3 Models (What Research Proves Works Best)
   - 4.4 Training Strategy
   - 4.5 Date Normalization Engine
   - 4.6 Evaluation Metrics
5. [Full System Architecture](#5-full-system-architecture)
6. [Tech Stack — With Justification](#6-tech-stack--with-justification)
7. [Database Schema](#7-database-schema)
8. [API Design](#8-api-design)
9. [Frontend Design](#9-frontend-design)
10. [Build Roadmap — Phase by Phase](#10-build-roadmap--phase-by-phase)
11. [What Makes This Resume-Worthy](#11-what-makes-this-resume-worthy)

---

## 1. Refined Problem Statement

> **Refined Problem Statement:**
> Indian SMEs, freelancers, HR teams, and small legal departments sign tens to hundreds of contracts every year — vendor agreements, NDAs, employment contracts, service level agreements, and rental leases — yet have no dedicated legal staff to monitor them. Critical obligations such as payment deadlines, termination windows, auto-renewal clauses, and penalty triggers are buried in dense legal language. Missed obligations lead to financial penalties, contract disputes, and compliance failures. Existing AI contract tools (SpotDraft, Ironclad, LegalOn) are built for enterprise legal teams at $10,000–$100,000/year price points, making them inaccessible to the vast majority of Indian businesses and individuals. **ClauseOps** is an open, locally-deployable, AI-powered contract intelligence system that ingests PDF contracts, extracts and classifies legal clauses using fine-tuned transformer models, detects obligations and deadlines, and converts them into trackable tasks with automated reminders — turning a static document into an operational compliance system.

**Why this framing is stronger than the original:**
- Targets a specific underserved user (Indian SMEs/freelancers) instead of "everyone"
- Mentions what you're NOT (not competing with SpotDraft)
- Grounds the AI specifics (fine-tuned transformers, not just "AI")
- Promises a measurable output (trackable tasks, reminders)

---

## 2. What the Research Says

These are the key findings from the academic literature that will directly shape how you build this:

### Finding 1: Domain-specific pre-training dramatically outperforms general models

A March 2026 paper published in Frontiers in AI (PMC13062225) ran a head-to-head comparison:

| Model | Macro-F1 | Span-F1 |
|---|---|---|
| BERT-base (general) | 0.782 | 0.654 |
| **LegalBERT (domain-trained)** | **0.846** | **0.729** |
| GPT-3.5 (zero-shot) | ~0.71 | lower |

**Implication for you:** Do NOT use a generic BERT or DistilBERT. Use LegalBERT as your base and fine-tune it on legal contract data. The +6.4% Macro-F1 gain is significant and well-documented.

### Finding 2: DeBERTa is the best model for clause classification on CUAD

From the CUAD-SL benchmark study: "A comparative study shows that fine-tuning on legal domain data adapts smaller, less complex models to the task at hand, with best overall performance of **87.8% for the DeBERTa model** compared to GPT-4's 67.2%." (ResearchGate, CUAD paper)

**Implication:** For your clause classification task, fine-tune `microsoft/deberta-v3-base` (not the xl, too heavy for student hardware) on CUAD/LEDGAR data. A fine-tuned DeBERTa-base beats GPT-4 zero-shot on this task.

### Finding 3: Generalist LLMs lose 26.8% F1 on legal clause classification vs. fine-tuned models

Research by Jayakumar et al. (2023) showed that LLaMA-2 and Falcon-180b, despite being large, lose up to 26.8% F1 in thematic classification of contractual clauses compared to fine-tuned domain-specific models.

**Implication:** Don't be tempted to just call GPT API and call it "AI". Fine-tuned smaller models are both better AND more defensible in interviews.

### Finding 4: LEDGAR is the best dataset for clause classification (80,000 labeled provisions)

LEDGAR consists of ~80,000 provisions from SEC filings labeled across 100 categories. It is part of the LexGLUE benchmark. Training set: 60,000 provisions. Validation: 10,000. Test: 10,000.

### Finding 5: ContractNLI is the best dataset for obligation detection (607 annotated NDAs)

From Stanford NLP: "ContractNLI is a dataset for document-level NLI on contracts. A system is given a set of hypotheses ('Some obligations may survive termination') and a contract, and classifies whether each hypothesis is Entailed, Contradicting, or Not Mentioned." This is exactly the obligation detection task you need.

### Finding 6: CUAD is the gold standard for span extraction (13,000+ annotations, 41 clause types)

CUAD (Contract Understanding Atticus Dataset) has 510 commercial legal contracts, annotated by legal experts for 41 clause types including Expiration Date, Payment Frequency, Termination for Convenience, Governing Law, Non-Compete, etc.

### Finding 7: For date normalization, you need a hybrid regex + NER + rule-based approach

spaCy's `DATE` entity recognizer handles explicit dates well, but legal contracts have relative temporal expressions ("within 30 days of signing", "upon completion of Phase 2"). These require a custom rule layer on top of NER.

---

## 3. System Scope & Target User

### Primary Target User
- Indian freelancers/consultants signing service agreements
- Small HR teams managing employee contracts (10–50 people)
- Small vendors managing supplier NDAs and agreements
- Real estate managers tracking lease renewals

### What ClauseOps Does (MVP)
1. Accept PDF contract upload
2. Extract text (digital PDFs + OCR for scanned ones)
3. Segment document into clauses automatically
4. Classify each clause into a category (using fine-tuned LegalBERT/DeBERTa)
5. Detect named entities: parties, dates, money amounts, organizations
6. Detect obligations and deadlines using NLI
7. Normalize relative dates into calendar dates
8. Generate tasks with reminder dates
9. Show everything on a dashboard
10. Allow manual correction (human-in-the-loop)

### What ClauseOps Does NOT Do (Future Scope)
- Contract drafting
- Multi-party negotiation
- Risk scoring (too complex for V1)
- Multi-language support
- Chat-with-contract (RAG interface) — future v2

---

## 4. ML Pipeline — The Core Engine

This is the most important section. The ML system has 4 distinct NLP tasks chained together.

```
PDF Input
    │
    ▼
[Task 0] Text Extraction (PyMuPDF + Tesseract)
    │
    ▼
[Task 1] Clause Segmentation (Rule-based + sentence boundary detection)
    │
    ▼
[Task 2] Clause Classification (Fine-tuned DeBERTa-v3 on LEDGAR)
    │
    ▼
[Task 3] Named Entity Recognition (Fine-tuned LegalBERT on legal NER corpus)
    │
    ▼
[Task 4] Obligation Detection (Fine-tuned BERT on ContractNLI)
    │
    ▼
[Task 5] Date Normalization (spaCy DATE + regex + rule engine)
    │
    ▼
Structured JSON output → Database → Task Engine → Dashboard
```

---

### 4.1 The 4 NLP Tasks You Need to Solve

#### Task 1: Clause Segmentation
**Problem:** Raw extracted text is one long string. You need to break it into individual clause units (paragraphs/sections) before you can classify them.

**Approach:**
- Step 1: Split on heading patterns using regex (e.g., `^\d+\.\s+[A-Z]`, `^ARTICLE\s+\d+`, `^Section\s+\d+`)
- Step 2: Use spaCy's sentence boundary detection within sections
- Step 3: Filter out noise (page numbers, headers, footers using position/font heuristics from PyMuPDF)
- Step 4: Each resulting chunk becomes a "clause candidate"

**Why not use a model here?** Because rule-based segmentation on structured legal documents with numbered sections works extremely well (~95% accuracy) and is computationally free. Save your model budget for classification.

#### Task 2: Clause Classification
**Problem:** Given a clause text, predict its type (e.g., PAYMENT_OBLIGATION, TERMINATION, GOVERNING_LAW, CONFIDENTIALITY, etc.)

**This is your main ML task.**

**Model:** `microsoft/deberta-v3-base` fine-tuned on LEDGAR dataset
- Input: clause text (up to 512 tokens)
- Output: one of N clause categories (you'll use top 20 categories from LEDGAR)
- Architecture: DeBERTa encoder → [CLS] token → Linear(768, N) → Softmax

**Simplified 20-category taxonomy you'll train on:**

| Category | Example Clause Fragment |
|---|---|
| PAYMENT | "Tenant shall pay ₹50,000 before..." |
| TERMINATION | "Either party may terminate with 30 days notice..." |
| CONFIDENTIALITY | "The Receiving Party shall keep all information confidential..." |
| GOVERNING_LAW | "This Agreement shall be governed by the laws of Karnataka..." |
| INDEMNIFICATION | "Party A shall indemnify Party B against all claims..." |
| LIMITATION_OF_LIABILITY | "In no event shall liability exceed..." |
| RENEWAL | "This Agreement shall automatically renew unless..." |
| INTELLECTUAL_PROPERTY | "All IP created under this Agreement shall belong to..." |
| DISPUTE_RESOLUTION | "All disputes shall be resolved by arbitration..." |
| NON_COMPETE | "Employee shall not engage in competing business..." |
| ASSIGNMENT | "Neither party may assign this Agreement without consent..." |
| FORCE_MAJEURE | "Neither party shall be liable for failure due to..." |
| WARRANTIES | "Service Provider warrants that the services will..." |
| DEFINITIONS | "'Agreement' means this contract dated..." |
| NOTICES | "All notices shall be sent to the address below..." |
| AUDIT_RIGHTS | "Company reserves the right to audit records..." |
| PENALTIES | "A late payment fee of 2% per month shall apply..." |
| DATA_PROTECTION | "Personal data shall be processed in accordance with..." |
| DELIVERY_OBLIGATIONS | "Vendor shall deliver within 14 working days..." |
| GENERAL_PROVISIONS | "This Agreement constitutes the entire understanding..." |

#### Task 3: Named Entity Recognition (NER)
**Problem:** From each clause, extract structured entities — Who are the parties? What dates? What amounts? What locations?

**Model:** `nlpaueb/legal-bert-base-uncased` fine-tuned on legal NER data (EDGAR-NER or custom-annotated subset)

**Entity types to extract:**

| Entity Label | Example |
|---|---|
| PARTY | "Tenant", "Service Provider", "Acme Corp" |
| DATE | "1st December 2026", "within 30 days" |
| MONEY | "₹50,000", "$10,000 USD" |
| DURATION | "6 months", "2 years" |
| JURISDICTION | "Karnataka", "State of Maharashtra" |
| ORG | "ABC Private Limited" |
| PERCENTAGE | "2% per month", "18% GST" |

**Training data for NER:**
- Use `EDGAR-NER` corpus (available on HuggingFace: `Souro/EDGAR-NER`)
- Augment with manually annotated examples from CUAD contracts
- Data augmentation: swap party names, amounts, dates with realistic alternatives

#### Task 4: Obligation Detection
**Problem:** Given a clause, is there an obligation on a specific party? What kind? (Must-do, Must-not-do, Has-right-to)

**Approach:** Frame as NLI (Natural Language Inference) using ContractNLI methodology

For each clause, run it against a fixed set of hypothesis templates:
```
- "This clause creates a payment obligation"
- "This clause creates a deadline"
- "This clause imposes a restriction on a party"
- "This clause grants a right to a party"
- "This clause triggers a penalty"
```

If the model classifies as ENTAILED → obligation exists of that type.

**Model:** `roberta-large-mnli` fine-tuned on ContractNLI dataset (607 annotated NDAs)

This is the task that makes your system genuinely useful beyond just labeling — it detects WHAT A PARTY IS OBLIGATED TO DO.

---

### 4.2 Datasets — What to Use and Why

| Dataset | Size | Task | Where to Get |
|---|---|---|---|
| **LEDGAR** | 80,000 provisions, 100 labels | Clause Classification | HuggingFace `datasets`: `lex_glue` (config=`ledgar`) |
| **CUAD** | 510 contracts, 13,000+ annotations, 41 clause types | Clause Span Extraction | HuggingFace `datasets`: `cuad` |
| **ContractNLI** | 607 NDAs, 17 hypotheses | Obligation Detection (NLI) | `stanfordnlp/contract-nli` on GitHub |
| **EDGAR-NER** | SEC filings with NER labels | Named Entity Recognition | HuggingFace `Souro/EDGAR-NER` |
| **LexGLUE** | Multi-task legal benchmark | Evaluation benchmark | HuggingFace `lexlms/lex_glue` |

**Loading LEDGAR (example):**
```python
from datasets import load_dataset
dataset = load_dataset("lex_glue", "ledgar")
# Train: 60k, Validation: 10k, Test: 10k
# Each example: {"text": "...", "label": 47}
```

**Loading CUAD:**
```python
dataset = load_dataset("cuad")
# Each example has: contract text, question (clause type), answer spans
```

**Loading ContractNLI:**
```python
# Clone from GitHub: stanfordnlp/contract-nli
# JSON format: {contract_text, hypotheses: [{text, label, evidence_spans}]}
```

---

### 4.3 Models — What Research Proves Works Best

For your hardware constraints as a student (likely no A100, maybe free Colab T4 or RTX 3060):

#### For Clause Classification (Task 2)
**Use:** `microsoft/deberta-v3-base`
- Parameters: 86M (manageable on T4/Colab)
- Why: Best CUAD performance at 87.8% F1. Beats GPT-4 at 67.2% on this task.
- Fine-tune on: LEDGAR (60k training examples)
- Expected training time: ~2-3 hours on Colab T4
- Expected accuracy: 85-88% F1 (matches SOTA for base-size models)

**Alternative (if compute is too limited):** `nlpaueb/legal-bert-base-uncased`
- Parameters: 110M
- Macro-F1 of 0.846 on clause classification (per Frontiers 2026 paper)
- Slightly larger but purpose-built for legal text

#### For NER (Task 3)
**Use:** `nlpaueb/legal-bert-base-uncased` as base, fine-tune for token classification (NER)
- Fine-tune on EDGAR-NER dataset
- Architecture: LegalBERT encoder → token-level linear heads → BIO tagging
- Expected F1 for key entities (DATE, MONEY, PARTY): 88-92%

#### For Obligation Detection (Task 4)
**Use:** `cross-encoder/nli-roberta-base` as starting point, fine-tune on ContractNLI
- Parameters: 125M
- Input format: "[CLS] hypothesis [SEP] clause_text [SEP]"
- Output: Entailment / Contradiction / Not Mentioned
- Expected accuracy: ~75-80% (ContractNLI is a genuinely hard dataset)

#### Summary Table

| Task | Model | Dataset | Expected F1 | Colab Feasible? |
|---|---|---|---|---|
| Clause Classification | deberta-v3-base | LEDGAR | ~87% | ✅ Yes (~3hrs) |
| NER | legal-bert-base | EDGAR-NER | ~90% | ✅ Yes (~2hrs) |
| Obligation Detection | roberta-base-mnli | ContractNLI | ~77% | ✅ Yes (~2hrs) |

---

### 4.4 Training Strategy

**Step 1: Environment Setup**
```python
pip install transformers datasets torch scikit-learn seqeval
```

**Step 2: Fine-tuning Clause Classifier (DeBERTa on LEDGAR)**

Key training parameters (based on literature):
```python
training_args = TrainingArguments(
    output_dir="./deberta-clauseops",
    num_train_epochs=5,           # 3-5 epochs is standard for fine-tuning
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    learning_rate=2e-5,           # Standard for BERT-family fine-tuning
    warmup_steps=500,
    weight_decay=0.01,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="f1",
)
```

**Why these specific hyperparameters:**
- `lr=2e-5`: Standard for BERT fine-tuning. Too high → catastrophic forgetting. Too low → slow convergence.
- `warmup_steps=500`: Prevents early-epoch instability
- `weight_decay=0.01`: Regularization against overfitting on legal text
- `num_train_epochs=5`: LEDGAR is 60k examples; 3-5 epochs is well-established in the literature

**Step 3: Handle Class Imbalance in LEDGAR**

LEDGAR has 100 labels but the distribution is very uneven. Use weighted loss:
```python
from torch.nn import CrossEntropyLoss
class_weights = compute_class_weight('balanced', classes=all_labels, y=train_labels)
loss_fn = CrossEntropyLoss(weight=torch.tensor(class_weights).float())
```

**Step 4: Save and Quantize the Model**

For production use, quantize to reduce size by ~50%:
```python
from transformers import pipeline
from torch.quantization import quantize_dynamic
quantized_model = quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
# Reduces ~500MB → ~240MB with minimal accuracy loss
```

**Step 5: Inference Pipeline**

```python
# Clause Classification
classifier = pipeline("text-classification", model="./deberta-clauseops", 
                       tokenizer=tokenizer, device=0)

# NER
ner = pipeline("token-classification", model="./legalbert-ner",
                aggregation_strategy="simple", device=0)

# Obligation Detection (NLI)
nli = pipeline("text-classification", model="./roberta-nli-obligations", device=0)

def analyze_clause(clause_text, contract_signing_date=None):
    return {
        "clause_type": classifier(clause_text[:512])[0],
        "entities": ner(clause_text[:512]),
        "obligations": detect_obligations(nli, clause_text),
        "deadlines": extract_and_normalize_dates(clause_text, contract_signing_date)
    }
```

---

### 4.5 Date Normalization Engine

This is a non-trivial engineering problem that most students skip and is a great talking point in interviews.

**Problem:** Legal contracts have 3 kinds of temporal expressions:
1. **Absolute dates:** "December 1, 2026" → trivial to parse
2. **Relative dates:** "within 30 days of signing" → need anchor date
3. **Conditional dates:** "upon completion of Phase 2, within 14 business days" → need business logic

**Solution: 3-layer hybrid system**

```python
import spacy
import re
from datetime import datetime, timedelta
from dateutil import relativedelta

nlp = spacy.load("en_core_web_lg")

def normalize_dates(clause_text: str, contract_date: datetime) -> list[dict]:
    results = []
    
    # Layer 1: spaCy NER for explicit dates
    doc = nlp(clause_text)
    for ent in doc.ents:
        if ent.label_ == "DATE":
            parsed = try_parse_absolute(ent.text)
            if parsed:
                results.append({"raw": ent.text, "type": "absolute", "date": parsed})
    
    # Layer 2: Regex patterns for relative expressions
    relative_patterns = [
        (r"within (\d+) days?", lambda m: contract_date + timedelta(days=int(m.group(1)))),
        (r"within (\d+) business days?", lambda m: add_business_days(contract_date, int(m.group(1)))),
        (r"within (\d+) months?", lambda m: contract_date + relativedelta.relativedelta(months=int(m.group(1)))),
        (r"(\d+) days? (?:after|from) (?:signing|execution)", lambda m: contract_date + timedelta(days=int(m.group(1)))),
        (r"on the (\d+)(?:st|nd|rd|th) of each month", lambda m: {"recurring": True, "day_of_month": int(m.group(1))}),
    ]
    
    for pattern, resolver in relative_patterns:
        for match in re.finditer(pattern, clause_text, re.IGNORECASE):
            computed = resolver(match)
            results.append({"raw": match.group(0), "type": "relative", "date": computed})
    
    # Layer 3: Flag conditional dates for human review
    conditional_patterns = [r"upon (completion|delivery|approval|execution)", r"when .+? is (completed|delivered)"]
    for pattern in conditional_patterns:
        for match in re.finditer(pattern, clause_text, re.IGNORECASE):
            results.append({"raw": match.group(0), "type": "conditional", "date": None, "requires_review": True})
    
    return results

def add_business_days(start: datetime, days: int) -> datetime:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday-Friday
            added += 1
    return current
```

---

### 4.6 Evaluation Metrics

| Task | Primary Metric | Secondary Metric | Target |
|---|---|---|---|
| Clause Classification | Macro-F1 | Per-class F1 | ≥ 0.83 |
| NER | Entity-level F1 (seqeval) | Per-entity-type F1 | ≥ 0.88 |
| Obligation Detection | 3-class F1 (Macro) | Evidence Span F1 | ≥ 0.72 |
| Date Normalization | Exact Match | ± 1 day tolerance | ≥ 0.90 |

**Note on CUAD metrics:** CUAD uses Precision at 80% Recall as the primary metric for production deployment threshold. This is more realistic than F1 because in legal review, high recall (catching all clauses) is more important than precision.

---

## 5. Full System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│                    React + Tailwind + Vite                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │ HTTPS
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FASTAPI BACKEND                               │
│   Auth │ Contract Upload │ Status Check │ Results │ Tasks API   │
│                   (Uvicorn + Gunicorn)                          │
└──────┬──────────────────┬──────────────────────┬───────────────┘
       │                  │                      │
       ▼                  ▼                      ▼
┌──────────────┐  ┌───────────────────┐  ┌─────────────────────┐
│  PostgreSQL  │  │   Redis Broker    │  │   MinIO / Local     │
│  (Main DB)   │  │   + Result Store  │  │   File Storage      │
│              │  │                   │  │   (PDF files)       │
└──────────────┘  └────────┬──────────┘  └─────────────────────┘
                           │
                           ▼
          ┌────────────────────────────────┐
          │       CELERY WORKERS           │
          │                                │
          │  Worker 1: PDF Extraction      │
          │  Worker 2: ML Inference        │
          │    ├── Clause Segmentation     │
          │    ├── Clause Classification   │
          │    ├── NER                     │
          │    └── Obligation Detection    │
          │  Worker 3: Date Normalization  │
          │  Worker 4: Task Generation     │
          └────────────────────────────────┘
                           │
                           ▼
          ┌────────────────────────────────┐
          │     ML MODEL REGISTRY          │
          │  /models/deberta-clauseops/    │
          │  /models/legalbert-ner/        │
          │  /models/roberta-nli/          │
          └────────────────────────────────┘
```

### Processing Flow (Step by Step)

```
1. User uploads PDF via React UI
   → POST /api/contracts/upload
   → FastAPI saves to storage, creates DB record (status: PENDING)
   → Returns contract_id immediately (non-blocking)

2. FastAPI dispatches Celery task chain
   → extract_text.delay(contract_id)
   → FastAPI returns 202 Accepted with task_id

3. Worker 1: PDF Extraction
   → PyMuPDF extracts text from digital PDF
   → If text < 100 chars/page → fallback to Tesseract OCR
   → Raw text saved to DB

4. Worker 2: Clause Segmentation
   → Regex-based heading detection
   → spaCy sentence boundary detection within sections
   → Each clause chunk saved as Clause record in DB

5. Worker 2: ML Inference (runs per clause)
   → DeBERTa classifies clause type
   → LegalBERT extracts entities (PARTY, DATE, MONEY...)
   → RoBERTa-NLI detects obligations
   → Results stored in Clause table

6. Worker 3: Date Normalization
   → Combines spaCy DATE entities + regex patterns
   → Resolves relative dates against contract signing date
   → Deadline records created in DB

7. Worker 4: Task Generation
   → For each deadline → create Task record
   → Set reminder dates (7 days before, 1 day before)
   → Celery Beat schedules reminder emails

8. Frontend polls GET /api/contracts/{id}/status
   → When complete, fetches full analysis
   → Renders dashboard with clauses, entities, timeline
```

---

## 6. Tech Stack — With Justification

### Backend
| Technology | Purpose | Why This Choice |
|---|---|---|
| **FastAPI** | REST API server | Async support, auto Swagger docs, Pydantic validation, fastest Python framework |
| **Uvicorn** | ASGI server | Works natively with FastAPI async |
| **Gunicorn** | Process manager in prod | Standard production wrapper |
| **SQLAlchemy 2.0** | ORM | Async support, type safety, well-documented |
| **Alembic** | DB migrations | Works seamlessly with SQLAlchemy |
| **Pydantic v2** | Data validation | FastAPI's native validation layer |

### Database & Storage
| Technology | Purpose | Why This Choice |
|---|---|---|
| **PostgreSQL 15** | Primary database | JSONB support (for storing entity lists), full-text search, ACID compliance |
| **Redis 7** | Celery broker + result backend | In-memory speed, battle-tested with Celery |
| **Local filesystem / MinIO** | PDF storage | MinIO is S3-compatible, easy to switch to AWS S3 later |

### ML / NLP
| Technology | Purpose | Why This Choice |
|---|---|---|
| **HuggingFace Transformers** | Model fine-tuning and inference | Industry standard, all models available |
| **PyTorch** | Training backend | Better research community support than TF |
| **spaCy 3.x** | Clause segmentation, fallback NER | Fast, production-grade NLP pipeline |
| **PyMuPDF (fitz)** | PDF text extraction | Fastest, most reliable PDF library. Handles tables better than pdfplumber |
| **Tesseract + pytesseract** | OCR for scanned PDFs | Open-source, good for printed contracts |
| **python-dateutil** | Date parsing | Handles ambiguous date strings well |
| **datasets (HuggingFace)** | Loading training data | Direct access to LEDGAR, CUAD, ContractNLI |

### Task Queue
| Technology | Purpose | Why This Choice |
|---|---|---|
| **Celery 5.x** | Async task queue | Most mature Python task queue, battle-tested |
| **Celery Beat** | Scheduled reminders | Built-in cron-like scheduler for periodic tasks |
| **Flower** | Celery monitoring dashboard | Real-time task monitoring, web UI |

### Frontend
| Technology | Purpose | Why This Choice |
|---|---|---|
| **React 18** | UI framework | Industry standard, component ecosystem |
| **Vite** | Build tool | Much faster than CRA, modern standard |
| **Tailwind CSS** | Styling | Utility-first, rapid development |
| **TanStack Query (React Query)** | API state management + polling | Handles loading/error/polling states cleanly |
| **Recharts** | Dashboard charts/timelines | Lightweight, React-native charting |
| **React PDF** | PDF preview | Show original contract alongside analysis |
| **Zustand** | Global state | Lighter than Redux for this scale |

### DevOps
| Technology | Purpose | Why This Choice |
|---|---|---|
| **Docker + Docker Compose** | Containerization | Run all services with one command |
| **Nginx** | Reverse proxy | Serves React build + proxies API |
| **GitHub Actions** | CI pipeline | Auto-test on push |

---

## 7. Database Schema

```sql
-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Contracts table
CREATE TABLE contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    file_path VARCHAR(1000) NOT NULL,  -- path to stored PDF
    file_size_kb INTEGER,
    raw_text TEXT,                     -- extracted text
    signing_date DATE,                 -- user-provided contract date
    contract_type VARCHAR(100),        -- NDA, Employment, Vendor, etc.
    status VARCHAR(50) DEFAULT 'PENDING',  -- PENDING | PROCESSING | COMPLETE | FAILED
    page_count INTEGER,
    ocr_used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Clauses table (one row per extracted clause)
CREATE TABLE clauses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID REFERENCES contracts(id) ON DELETE CASCADE,
    clause_index INTEGER NOT NULL,     -- order within contract
    raw_text TEXT NOT NULL,
    clause_type VARCHAR(100),          -- output of classifier
    confidence FLOAT,                  -- classifier confidence score
    is_manually_corrected BOOLEAN DEFAULT FALSE,
    corrected_type VARCHAR(100),       -- human correction if applied
    entities JSONB,                    -- {PARTY: [...], DATE: [...], MONEY: [...]}
    obligations JSONB,                 -- [{type, text, confidence}]
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Deadlines table
CREATE TABLE deadlines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID REFERENCES contracts(id) ON DELETE CASCADE,
    clause_id UUID REFERENCES clauses(id) ON DELETE CASCADE,
    raw_date_text VARCHAR(500),        -- "within 30 days of signing"
    normalized_date DATE,              -- computed calendar date
    date_type VARCHAR(50),             -- ABSOLUTE | RELATIVE | CONDITIONAL
    requires_review BOOLEAN DEFAULT FALSE,
    deadline_label VARCHAR(200),       -- "Payment Due", "Renewal Window Closes"
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tasks table
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID REFERENCES contracts(id) ON DELETE CASCADE,
    deadline_id UUID REFERENCES deadlines(id),
    title VARCHAR(300) NOT NULL,
    description TEXT,
    due_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING',  -- PENDING | DONE | SNOOZED | DISMISSED
    priority VARCHAR(20) DEFAULT 'MEDIUM', -- LOW | MEDIUM | HIGH | CRITICAL
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Reminders table
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    remind_at TIMESTAMPTZ NOT NULL,
    channel VARCHAR(50) DEFAULT 'email',   -- email | in_app
    sent BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMPTZ
);
```

**Why PostgreSQL JSONB for entities and obligations?**
Because each clause has variable-length, variable-structure entity lists. JSONB lets you store them flexibly while still being queryable:
```sql
-- Query all clauses with payment amounts over ₹100,000
SELECT * FROM clauses
WHERE (entities->'MONEY'->>0)::TEXT ILIKE '%₹%'
AND clause_type = 'PAYMENT';
```

---

## 8. API Design

### Authentication
```
POST /api/auth/register       → Create account
POST /api/auth/login          → JWT token
POST /api/auth/refresh        → Refresh JWT
```

### Contracts
```
POST   /api/contracts/upload       → Upload PDF, returns {contract_id, task_id}
GET    /api/contracts/             → List user's contracts
GET    /api/contracts/{id}         → Contract detail
GET    /api/contracts/{id}/status  → Processing status (for polling)
DELETE /api/contracts/{id}         → Delete contract

GET    /api/contracts/{id}/clauses           → All clauses with analysis
PUT    /api/contracts/{id}/clauses/{clause_id}  → Manually correct clause type
GET    /api/contracts/{id}/entities          → All extracted entities
GET    /api/contracts/{id}/deadlines         → All deadlines
GET    /api/contracts/{id}/timeline          → Chronological view of all deadlines
```

### Tasks
```
GET    /api/tasks/               → All tasks for user (filterable by status, date)
PUT    /api/tasks/{id}/complete  → Mark task done
PUT    /api/tasks/{id}/snooze    → Snooze task
GET    /api/tasks/upcoming       → Tasks due in next 7 days
```

### Dashboard
```
GET    /api/dashboard/summary    → Counts: active contracts, pending tasks, upcoming deadlines
```

### Example Response — Contract Analysis
```json
{
  "contract_id": "uuid-here",
  "status": "COMPLETE",
  "filename": "vendor_agreement_2026.pdf",
  "signing_date": "2026-01-15",
  "clauses": [
    {
      "clause_index": 3,
      "raw_text": "Vendor shall deliver all goods within 14 business days of receiving purchase order...",
      "clause_type": "DELIVERY_OBLIGATIONS",
      "confidence": 0.94,
      "entities": {
        "PARTY": ["Vendor"],
        "DURATION": ["14 business days"],
        "DATE": []
      },
      "obligations": [
        {"type": "MUST_DO", "text": "Deliver goods within 14 business days", "confidence": 0.89}
      ]
    }
  ],
  "deadlines": [
    {
      "raw_date_text": "within 14 business days",
      "normalized_date": "2026-01-29",
      "date_type": "RELATIVE",
      "deadline_label": "Delivery Obligation"
    }
  ]
}
```

---

## 9. Frontend Design

### Pages
1. **Landing/Login/Register** — Simple auth flow
2. **Dashboard** — Overview cards + upcoming deadlines timeline
3. **Contract Upload** — Drag-drop PDF, contract date input, submit
4. **Processing Status** — Polling page with progress steps
5. **Contract Analysis View** — Split view: PDF left, analysis right
6. **Tasks Page** — Kanban-style or list view of all obligations
7. **Contract Library** — All uploaded contracts with status badges

### Key UI Components

**Contract Analysis Split View** (most important):
```
┌─────────────────────┬──────────────────────────────┐
│   PDF VIEWER        │   ANALYSIS PANEL             │
│                     │                              │
│   [Page 1 of 8]     │   ┌─ Clause 3 ────────────┐ │
│                     │   │ Type: DELIVERY_OBLIG.  │ │
│   Highlighted text  │   │ Confidence: 94%        │ │
│   (active clause)   │   │                        │ │
│                     │   │ Entities:              │ │
│                     │   │   Party: Vendor        │ │
│                     │   │   Duration: 14 days    │ │
│                     │   │                        │ │
│                     │   │ Obligation:            │ │
│                     │   │   ⚠ MUST_DO: Deliver   │ │
│                     │   │   goods within 14 days │ │
│                     │   │                        │ │
│                     │   │ Deadline: 2026-01-29   │ │
│                     │   │ [Create Task] [Edit]   │ │
│                     │   └────────────────────────┘ │
└─────────────────────┴──────────────────────────────┘
```

**Deadline Timeline** (Recharts):
```
Jan 2026  Feb 2026   Mar 2026   Apr 2026
  │          │           │          │
  ●          ●●          ●          ●●●
  Payment    Delivery    Renewal    Penalties
  Due        Deadline    Notice     Due
```

---

## 10. Build Roadmap — Phase by Phase

### Phase 0: Research & Setup (Week 1)
- [ ] Read this document thoroughly
- [ ] Set up GitHub repo with folder structure
- [ ] Install Python 3.11, Node 20, PostgreSQL, Redis, Docker
- [ ] Set up Conda/venv environment
- [ ] Create basic FastAPI app with one health endpoint
- [ ] Create React app with Vite

### Phase 1: Data & ML (Weeks 2–4)
- [ ] Download and explore LEDGAR dataset via HuggingFace
- [ ] Download CUAD dataset
- [ ] Download ContractNLI from Stanford GitHub
- [ ] Write EDA notebook: class distributions, text length analysis
- [ ] Fine-tune DeBERTa-v3-base on LEDGAR (Clause Classification)
- [ ] Evaluate on LEDGAR test set, achieve ≥83% Macro-F1
- [ ] Fine-tune LegalBERT for NER on EDGAR-NER
- [ ] Fine-tune RoBERTa on ContractNLI for obligation detection
- [ ] Build and test date normalization engine
- [ ] Save all 3 models to `/models/` directory
- [ ] Write inference.py that chains all 4 tasks on a single clause

### Phase 2: PDF Extraction Pipeline (Week 5)
- [ ] Implement PyMuPDF text extractor
- [ ] Implement Tesseract OCR fallback
- [ ] Implement clause segmentation (regex + spaCy)
- [ ] Test on 10 sample contracts (download from CUAD for free)
- [ ] Write unit tests for extractor and segmenter

### Phase 3: Backend (Weeks 6–7)
- [ ] Define SQLAlchemy models (all tables from schema above)
- [ ] Run Alembic migrations
- [ ] Implement all API endpoints
- [ ] Set up Celery + Redis (Docker Compose)
- [ ] Build Celery task chain: extract → segment → classify → normalize → task-gen
- [ ] Implement JWT authentication
- [ ] Add file upload with size validation (max 20MB)
- [ ] Add Celery Beat for reminder scheduling
- [ ] Test full pipeline end-to-end with a real contract PDF

### Phase 4: Frontend (Weeks 8–9)
- [ ] Build auth pages (login/register)
- [ ] Build contract upload page with drag-drop
- [ ] Build processing status page with polling (React Query)
- [ ] Build contract analysis split view
- [ ] Build deadline timeline (Recharts)
- [ ] Build tasks/obligations page
- [ ] Build dashboard overview page
- [ ] Connect all pages to backend API

### Phase 5: Polish & Deploy (Week 10)
- [ ] Write Dockerfile for FastAPI app
- [ ] Write Dockerfile for Celery worker
- [ ] Write docker-compose.yml for all services
- [ ] Add Nginx reverse proxy + serve React build
- [ ] Deploy to Railway or Render (free tier)
- [ ] Write README with setup instructions, architecture diagram, demo video
- [ ] Record 2-minute demo video for portfolio

### Phase 6: Resume Hardening (Week 11)
- [ ] Write the "hard problems" section of your README
- [ ] Document your model accuracy results with confusion matrices
- [ ] Add model cards for each of your 3 trained models
- [ ] Push trained models to your HuggingFace account (public)
- [ ] Add GitHub Actions CI pipeline (auto-test on push)

---

## 11. What Makes This Resume-Worthy

When a recruiter or interviewer asks "tell me about ClauseOps," here is what you say:

### The Technical Story
> "ClauseOps is an end-to-end AI system for contract intelligence. The ML backbone is a pipeline of three fine-tuned transformer models: DeBERTa-v3 for clause classification, LegalBERT for legal NER, and RoBERTa fine-tuned on ContractNLI for obligation detection. I trained these on the LEDGAR, CUAD, and ContractNLI datasets — which together represent over 93,000 annotated legal contract examples. The hardest problem was building the date normalization engine, which handles not just absolute dates but also relative temporal expressions like 'within 30 business days of signing' — that required a layered system combining spaCy NER, custom regex, and a business-day calendar resolver. On the systems side, I built an async processing pipeline using FastAPI + Celery + Redis so that large PDF contracts are processed in the background without blocking the API, and I used Celery Beat for scheduling automated reminder emails before deadline dates."

### The Numbers to Quote
- Training data: 80,000+ labeled clauses (LEDGAR) + 510 contracts (CUAD) + 607 NDAs (ContractNLI)
- Clause classifier: ~87% Macro-F1 (matching published SOTA for base-size models)
- NER: ~90% entity-level F1
- 3 fine-tuned models, all pushed to HuggingFace
- Processing pipeline: async, handles PDFs up to 100 pages
- Full-stack: React + FastAPI + PostgreSQL + Redis + Celery + Docker

### The "Hard Problem" to Own
Pick the **date normalization engine** as your unique contribution. It's technically interesting, easy to explain, and NOT something any API call can do out of the box. Being able to say "I built a hybrid NER + regex + rule-based temporal resolver that turns 'within 30 business days of signing' into an actual calendar date" is a strong interview story.

---

*ClauseOps Blueprint v1.0 | Research Sources: CUAD (Hendrycks et al., 2021), LEDGAR (Tuggener et al., 2020), ContractNLI (Koreeda & Manning, 2021), Frontiers in AI (PMC13062225, 2026), LegalLens 2024 Shared Task*
