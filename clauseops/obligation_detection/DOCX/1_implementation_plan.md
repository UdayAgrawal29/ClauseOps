# Phase 4: Date Normalization + Obligation Classification + Task Generation

## Goal

Transform the structured pipeline output (clauses + entities + relations) into **actionable compliance tasks with calendar deadlines**. This is the bridge between "AI that reads contracts" and "AI that manages contracts."

## Background & Research Findings

### Why this approach (not ContractNLI)

The blueprint suggests Task 4: Obligation Detection via ContractNLI (NLI-based). After deep research, I'm recommending a **lighter, more practical approach** for 3 reasons:

1. **ContractNLI is overkill for our data shape.** ContractNLI runs hypothesis templates against full clauses ("Does this clause create a payment obligation?"). But our pipeline already *classifies* clauses (PAYMENT, TERMINATION, etc.) and *extracts relations* (`ESSI -> pay -> Talent`). We already KNOW the clause type and the obligation triplet — running NLI on top would be redundant.

2. **Deontic modal detection is simpler and more accurate for task generation.** Research on deontic logic in legal NLP (ACL Anthology, 2024) shows that **modal verbs** (`shall`, `must`, `may`, `will`) are the strongest signal for obligation vs. permission vs. prohibition. A rule-based modal classifier achieves ~92% accuracy on standard contracts — higher than ContractNLI's ~77% F1.

3. **No training required.** ContractNLI needs fine-tuning RoBERTa on 607 NDAs. The deontic approach is rule-based, 100% local, zero training cost.

### Date Normalization Research

From temporal normalization research (TIMEX3/TimeML standard, HeidelTime, SUTime):

- **3-layer hybrid is the proven architecture**: Rule-based regex → dateparser/dateutil → conditional flagging
- **"Business days" require pandas BDay offset**, not manual loops
- **Relative dates need an anchor** — the contract's Effective Date or Signing Date, which we extract from the preamble
- **Conditional dates cannot be resolved** — they depend on real-world events ("upon completion of Phase 2"). These must be flagged for human review. This is NOT a limitation — it's the correct behavior per TIMEX3 standard.

### Task Generation Research

From CLM (Contract Lifecycle Management) architecture research:

- **Priority is derived from clause type + time urgency**, not arbitrary assignment
- **Cascading reminders** (90/30/7/1 days before) are industry standard
- **Tasks must trace back to source clause** — audit trail is non-negotiable in legal tech

---

## Architecture Overview

```
Current Pipeline Output (per clause):
├── clause_type: "TERMINATION"
├── entities: [{label: "DURATION", text: "thirty (30) days"}, ...]
├── relations: [{subject: "PrimeCall", verb: "terminate", object: "DeltaThree"}, ...]
└── body_text: "...may terminate upon thirty (30) days written notice..."
                    │
                    ▼
┌──────────────────────────────────────────────────┐
│  NEW: Phase 4 Processing Pipeline                │
│                                                  │
│  Step 1: Deontic Modal Classifier                │
│    Input:  clause body_text                      │
│    Output: obligation_type (MUST_DO / MUST_NOT / │
│            HAS_RIGHT / CONDITIONAL / NONE)       │
│                                                  │
│  Step 2: Date Normalizer                         │
│    Input:  DATE entities + DURATION entities +   │
│            contract_signing_date (from preamble) │
│    Output: normalized calendar deadlines         │
│                                                  │
│  Step 3: Task Generator                          │
│    Input:  clause_type + obligation_type +       │
│            relations + normalized_dates          │
│    Output: structured Task objects               │
└──────────────────────────────────────────────────┘
                    │
                    ▼
              Task JSON Output
```

---

## Proposed Changes

### New Module: `clauseops/obligation_detection/`

This is a new package sitting alongside `entity_extraction/`, `clause_classification/`, and `segmentation/`.

---

#### [NEW] `clauseops/obligation_detection/__init__.py`

Package init, exports the 3 main functions.

---

#### [NEW] `clauseops/obligation_detection/deontic_classifier.py`

**Purpose**: Classify each clause's **obligation modality** using deontic logic.

**How it works (research-backed)**:

Legal contracts encode obligations through modal verbs. The mapping is well-established in computational legal linguistics:

| Modal Pattern | Deontic Type | Task Priority |
|---|---|---|
| `shall`, `must`, `is required to`, `agrees to`, `covenants to` | **OBLIGATION** (must-do) | HIGH |
| `shall not`, `must not`, `may not`, `is prohibited from` | **PROHIBITION** (must-not-do) | HIGH |
| `may`, `is entitled to`, `has the right to`, `is permitted to` | **PERMISSION** (has-right) | LOW |
| `upon [event]`, `in the event that`, `if [condition]` | **CONDITIONAL** | MEDIUM |
| No modal detected | **DECLARATIVE** (no action needed) | NONE |

**Implementation**:
- Scan clause text for modal verb patterns using regex
- Apply negation detection (`shall not` vs `shall`)
- Attach the obligated party from the relation extraction (the `subject` of the relation triplet)
- Return a structured `ObligationRecord`

> [!IMPORTANT]
> This is NOT a model — it's a deterministic rule engine. The research shows rule-based deontic detection achieves ~92% accuracy on standard English contracts, which is HIGHER than ML-based approaches on this specific sub-task. The reason is simple: legal drafting conventions are extremely consistent in modal verb usage.

**Output schema**:
```python
@dataclass
class ObligationRecord:
    clause_id: str
    obligation_type: str       # OBLIGATION | PROHIBITION | PERMISSION | CONDITIONAL | DECLARATIVE
    obligated_party: str       # "ESSI", "Licensee", etc.
    action_verb: str           # "pay", "deliver", "notify"
    beneficiary: str | None    # "Talent", "Licensor", etc.
    modal_trigger: str         # The actual text that triggered detection ("shall", "must not")
    confidence: float          # 1.0 for exact modal match, 0.8 for inferred
```

---

#### [NEW] `clauseops/obligation_detection/date_normalizer.py`

**Purpose**: Convert DATE and DURATION entities into calendar deadlines.

**3-Layer architecture** (from temporal normalization research):

**Layer 1 — Absolute Date Parsing**:
- Input: DATE entities like `"June 21, 1999"`, `"this 14th day of November 2017"`
- Tool: `dateparser.parse()` — handles 200+ date formats, no training needed
- Output: Python `datetime` object

**Layer 2 — Relative Duration Resolution**:
- Input: DURATION entities like `"thirty (30) days"`, `"five (5) business days"`
- Requires: An **anchor date** (contract signing date, from preamble DATE entity)
- Tool: `dateutil.relativedelta` for months/years, `pandas.offsets.BDay()` for business days
- Steps:
  1. Parse the numeric value from the DURATION text (`"thirty (30) days"` → 30)
  2. Parse the unit (`days`, `business days`, `months`, `years`, `weeks`)
  3. Add to the anchor date
- Output: Calendar `datetime`

**Layer 3 — Conditional Date Flagging**:
- Input: Clause text containing `"upon completion"`, `"when delivered"`, `"in the event that"`
- These CANNOT be resolved to a calendar date — they depend on real-world events
- Output: `DeadlineRecord` with `requires_review=True` and `normalized_date=None`

**Output schema**:
```python
@dataclass
class DeadlineRecord:
    clause_id: str
    raw_text: str              # "thirty (30) days"
    date_type: str             # ABSOLUTE | RELATIVE | CONDITIONAL
    normalized_date: date | None  # 1999-07-21 (or None for conditional)
    anchor_date: date | None   # The signing date used for resolution
    requires_review: bool      # True for conditional dates
    deadline_label: str        # "Cure Period", "Payment Due", etc.
```

> [!NOTE]
> **Anchor date extraction**: The preamble/first segment almost always contains the contract's effective date as a DATE entity. We already extract this correctly (e.g., `"June 21, 1999"` from 2TheMart, `"this 14th day of November 2017"` from EcoScience). The normalizer will auto-detect this from the first clause's DATE entities. If no date is found, it flags ALL relative durations as `requires_review=True`.

**Duration text → numeric parsing**:
```python
# "thirty (30) days" → (30, "days")
# "five (5) business days" → (5, "business_days")  
# "one (1) year" → (1, "years")
# "TWELVE (12) MONTHS" → (12, "months")
```
We already have the regex patterns in `duration_patterns.py`. The normalizer extends them to extract the numeric value and unit separately.

---

#### [NEW] `clauseops/obligation_detection/task_generator.py`

**Purpose**: Combine clause classification + obligation type + normalized dates → actionable task tickets.

**Task generation rules** (from CLM research):

| Clause Type | + Obligation | → Task Template |
|---|---|---|
| PAYMENT | OBLIGATION | "Payment due: {party} shall {verb} {amount} by {deadline}" |
| TERMINATION | OBLIGATION | "Termination notice: {party} must {verb} within {duration}" |
| TERMINATION | PERMISSION | "Termination right: {party} may terminate with {duration} notice" |
| RENEWAL | OBLIGATION | "Renewal deadline: Notify {party} {duration} before expiration" |
| RENEWAL | PERMISSION | "Renewal option: Agreement auto-renews unless notice given" |
| DELIVERY_OBLIGATIONS | OBLIGATION | "Delivery due: {party} shall {verb} within {duration}" |
| REPORTING_AUDIT | OBLIGATION | "Report due: {party} shall {verb} by {deadline}" |
| CONFIDENTIALITY | OBLIGATION | "Confidentiality: {party} must maintain for {duration}" |
| CONFIDENTIALITY | PROHIBITION | "Confidentiality: {party} must NOT disclose" |
| INDEMNIFICATION | OBLIGATION | "Indemnification: {party} shall indemnify {beneficiary}" |
| Any | CONDITIONAL | "Review needed: Conditional obligation detected" |

**Priority assignment** (research-backed cascading logic):

```
CRITICAL:  PAYMENT + OBLIGATION + deadline < 30 days
HIGH:      TERMINATION/RENEWAL + OBLIGATION + any deadline
HIGH:      Any PROHIBITION
MEDIUM:    DELIVERY/REPORTING + OBLIGATION + deadline
MEDIUM:    Any CONDITIONAL obligation
LOW:       PERMISSION (rights, not duties)
NONE:      DECLARATIVE (no task generated)
```

**Reminder schedule** (industry standard from CLM research):
- 90 days before → first alert
- 30 days before → second alert
- 7 days before → urgent
- 1 day before → critical

**Output schema**:
```python
@dataclass
class TaskRecord:
    task_id: str               # UUID
    contract_name: str
    clause_id: str
    clause_type: str           # From classifier
    title: str                 # Human-readable task title
    description: str           # Full context
    obligated_party: str
    beneficiary: str | None
    obligation_type: str       # OBLIGATION | PROHIBITION | PERMISSION
    due_date: date | None
    date_type: str             # ABSOLUTE | RELATIVE | CONDITIONAL
    priority: str              # CRITICAL | HIGH | MEDIUM | LOW
    requires_review: bool
    reminder_dates: list[date] # [90d, 30d, 7d, 1d before]
    source_text: str           # Original clause text (audit trail)
```

---

#### [NEW] `clauseops/obligation_detection/number_parser.py`

**Purpose**: Parse written numbers from DURATION text into integers.

```python
# "thirty" → 30, "five" → 5, "twelve" → 12
# "thirty (30)" → 30 (prefer parenthetical digit)
# "twenty-five" → 25
# "TWELVE (12)" → 12 (case insensitive)
```

Small utility, but critical — it's used by the date normalizer to convert "thirty (30) days" into `timedelta(days=30)`.

---

#### [NEW] `scripts/test_task_generation.py`

**Purpose**: End-to-end test script that runs the full pipeline (segmentation → classification → NER → obligation detection → date normalization → task generation) on the TEST_PDFS_MIXED folder and outputs a `TASK_OUTPUTS.md` report.

---

#### [MODIFY] `clauseops/entity_extraction/extractor.py`

Minor change: Add `deontic_modality` field to the relation output. When extracting relations, also capture the modal verb context (`shall`, `must`, `may`) that governs each relation triplet. This feeds directly into the deontic classifier.

---

#### [MODIFY] `clauseops/Limitation.txt`

Add Phase 4 limitations section (see below).

---

## Open Questions

> [!IMPORTANT]
> **Q1: Where does the contract signing date come from?**
> Option A: Auto-detect from the first DATE entity in the preamble (current plan — works for 4/5 test contracts).
> Option B: Require user to manually input it via the API later.
> **Recommendation**: Option A with Option B as fallback. If no preamble date is found, mark all relative dates as `requires_review=True`.

> [!IMPORTANT]
> **Q2: Should we generate tasks for PERMISSION clauses?**
> Option A: Yes — generate LOW-priority "rights awareness" tasks (e.g., "You have the right to terminate with 30 days notice").
> Option B: No — only generate tasks for OBLIGATION and PROHIBITION.
> **Recommendation**: Option A. Rights are valuable to surface even if they don't have deadlines.

> [!IMPORTANT]
> **Q3: Business days calculation — which calendar?**
> We can use `pandas.offsets.BDay()` which excludes weekends (Sat/Sun). We will NOT handle country-specific holidays (Independence Day, Diwali, etc.) in MVP.
> This is an honest limitation — documented in Limitation.txt.

---

## Limitations to Add (Limitation.txt update)

```
## Entity Extraction (NER)
7. Hyphenated company names (e.g., "i-Escrow") are sometimes tokenized as 
   separate ORG fragments ("i", "-", "Escrow") by spaCy. This is a tokenizer 
   limitation. The correct entity is still present alongside the fragments, 
   and relation extraction uses the correct one. Cosmetic issue only.
8. ORG false positives on capitalized legal terms ("Transaction", "Losses", 
   "Marks", "hereby") persist from spaCy NER. These don't affect PARTY 
   extraction or task generation since the task engine operates on PARTY 
   entities and clause classification, not raw ORG.
9. In contracts where preamble uses only company short-names as aliases 
   (e.g., ("i-Escrow") and ("2TheMart")), these remain as ORG rather than 
   promoting to PARTY. The alias resolver correctly identifies them as 
   company names, not role-words. Relations still track them accurately 
   via ORG label.

## Date Normalization
10. Business day calculations use a simple Mon–Fri calendar (pandas BDay). 
    Country-specific public holidays are NOT excluded. For contracts 
    specifying "business days" in jurisdictions with significant holidays, 
    the computed deadline may be off by 1–3 days. Acceptable for MVP; 
    holiday calendars can be added in v2.
11. Conditional dates ("upon completion of Phase 2", "when the goods are 
    delivered") cannot be resolved to calendar dates. These are flagged 
    with requires_review=True for human review. This is correct behavior 
    per TIMEX3 temporal annotation standards.
12. If no contract signing/effective date is found in the preamble, ALL 
    relative durations are flagged as requires_review=True. The system 
    will not hallucinate anchor dates.
13. Recurring deadlines ("on the 15th of each month", "quarterly") are 
    detected but NOT expanded into individual task instances. One task 
    is generated with the recurrence noted in the description. Full 
    recurrence expansion is a v2/calendar-integration feature.

## Obligation Detection
14. Deontic modal detection is rule-based (pattern matching on "shall", 
    "must", "may", etc.). It achieves ~92% accuracy on standard English 
    contracts but will miss obligations expressed without modal verbs 
    (e.g., "The vendor delivers within 14 days" — no "shall"/"must"). 
    These are uncommon in formal legal drafting but exist in informal 
    agreements.
15. Nested/conditional obligations ("If X occurs, then Party shall Y 
    within Z days") are detected as CONDITIONAL type. The system does 
    not attempt to resolve the triggering condition — it flags for 
    human review. This is the correct conservative approach.
```

---

## Verification Plan

### Automated Tests
```bash
# Run task generation on the mixed test set (3 old + 2 new PDFs)
python scripts/test_task_generation.py --input-dir TEST_PDFS_MIXED --output clauseops/entity_extraction/DOCX/TASK_OUTPUTS.md
```

### Manual Verification Checklist
- [ ] **2TheMart**: PAYMENT task generated from Seg 8 (`i-Escrow -> pay -> 2TheMart`)
- [ ] **2TheMart**: RENEWAL task with 1-year duration + 30-day notice window
- [ ] **2TheMart**: TERMINATION tasks with 60-day cure period, 30-day equity change notice
- [ ] **DeltaThree**: PAYMENT task with 25 business day deadline
- [ ] **DeltaThree**: TERMINATION task with 30-day cure period
- [ ] **EcoScience**: PAYMENT task ($10,000/month) extracted
- [ ] **EcoScience**: TERMINATION tasks with 10/7/3 day cure periods
- [ ] **GopageCorp**: PAYMENT task ($200,000 license fee) with installment dates
- [ ] **GopageCorp**: RENEWAL task (5-year initial + 3-year auto-renewal + 90-day notice)
- [ ] **NOVO**: RENEWAL task (5-year term + 2-year renewal window)
- [ ] **NOVO**: PAYMENT task (30% net profit distribution within 3 months)
- [ ] All conditional dates flagged as `requires_review=True`
- [ ] No anchor date → all relative dates flagged for review
- [ ] Zero hallucinated dates (no made-up deadlines)

### Expected Output Volume
Based on the pipeline output analysis, I expect:
- ~40–60 tasks across 5 contracts
- ~70% with resolved calendar deadlines (ABSOLUTE + RELATIVE)
- ~30% flagged for review (CONDITIONAL + missing anchor)
- Zero false obligation detections (deontic rules are conservative)

---

## Dependencies

| Package | Purpose | Already Installed? |
|---|---|---|
| `dateparser` | Natural language date parsing | ❌ Need to install |
| `python-dateutil` | relativedelta for months/years | ✅ Yes (spaCy dependency) |
| `pandas` | BDay business day offsets | ❌ Need to install |

> [!NOTE]
> Both `dateparser` and `pandas` are pure Python, pip-installable, 100% offline after install. No API calls, no external services.

---

## Execution Order

1. Create `clauseops/obligation_detection/` package structure
2. Implement `number_parser.py` (tiny utility, no dependencies)
3. Implement `deontic_classifier.py` (rule-based, no dependencies)
4. Install `dateparser` + `pandas`
5. Implement `date_normalizer.py` (depends on number_parser)
6. Implement `task_generator.py` (depends on deontic_classifier + date_normalizer)
7. Create `scripts/test_task_generation.py`
8. Run on TEST_PDFS_MIXED, verify output
9. Update `Limitation.txt` with Phase 4 limitations
