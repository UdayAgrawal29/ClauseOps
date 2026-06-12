# Deep Analysis: Pipeline Quality Issues & Generalized Fixes

## Problem Statement

After testing on 3 unseen PDFs (Joint Venture, Development, Affiliate), the pipeline generates **154 tasks** but **~40-50% are noise** — not actionable compliance obligations. The output is not sufficient for Phase 4B/4C (Backend/UI) because a dashboard showing 50% junk tasks would be unusable.

---

## Root-Cause Analysis

I performed a task-by-task audit of all 55 tasks from `BORROWMONEYCOM` (Joint Venture) and spot-checked 59 from `ConformisInc` (Development). Here are the **6 systemic issues** I found, ranked by impact:

---

### 🔴 Issue 1: `"will"` Over-Triggering (THE BIGGEST PROBLEM)

**Impact: ~40% of all noise tasks**

The modal `"will"` is treated identically to `"shall"` in our `_OBLIGATION_PATTERNS`. But in modern contract drafting, `"will"` serves THREE purposes:

| Usage | Example | Is it an actionable obligation? |
|---|---|---|
| **Obligatory** | "All Members **will** contribute their Capital" | ✅ YES |
| **Declarative/Descriptive** | "The business name **will** be BM&V2GO" | ❌ NO — this is a fact, not an action |
| **Passive/Stative** | "Venture funds **will** be held in the name..." | ❌ NO — no agent is performing an action |

**Evidence from BORROWMONEY output:**
- Task 7: `"BM&V2GO shall perform"` ← Source: "The business name of the Venture **will** be BM&V2GO." — **GARBAGE** (declarative fact)
- Task 8: `"purpose shall perform"` ← Source: "The exclusive purpose **will** be IT Development." — **GARBAGE** (definition)
- Task 9: `"Contracting Party shall locate"` ← Source: "The principal office **will** be located at..." — **GARBAGE** (passive descriptive)
- Task 16: `"Contracting Party shall maintain"` ← Source: "An individual capital account **will** be maintained..." — **GARBAGE** (passive, no agent)
- Task 18: `"Contracting Party shall arise"` ← Source: "No borrowing charge **will** be due..." — **GARBAGE** (stative)
- Task 21: `"purpose shall hold"` ← Source: "Regular meetings **will** be held quarterly" — **GARBAGE** (passive)
- Task 28: `"Contracting Party shall determine"` ← Source: "the value **will** be determined based on..." — **GARBAGE** (passive)

> [!CAUTION]
> **7 out of 55 tasks (13%) from BORROWMONEY alone are pure garbage from declarative/passive `"will"` sentences.** Across all contracts, `"will"` accounts for ~35-45% of all generated tasks, and roughly half of those are non-actionable.

**Root Cause:** [deontic_classifier.py line 73](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py#L73) — `\bwill\b` matches every sentence containing "will" without any semantic filtering.

---

### 🟠 Issue 2: Passive Voice Subject Confusion

**Impact: ~15% of noise tasks**

When sentences use passive voice ("X **will be** done"), the pipeline fails to identify the correct agent and falls back to "Contracting Party" — creating meaningless tasks.

**Evidence:**
- `"Contracting Party shall locate"` ← "The principal office will be **located** at..." (no agent — the office isn't locating anything)
- `"Contracting Party shall maintain"` ← "An individual capital account will be **maintained**" (passive — who maintains it?)
- `"Contracting Party shall determine"` ← "the value will be **determined** based on..." (passive — who determines?)

**Root Cause:** The spaCy dep parser finds the ROOT verb but takes the grammatical subject (passive subject), not the logical agent. In passive constructions, the grammatical subject is the **patient** (thing being acted upon), not the **agent** (who does it).

---

### 🟠 Issue 3: Non-Entity Subjects as Party Names

**Impact: ~10% of noise tasks**

Despite Fix 3 (NER validation), some non-party nouns still slip through as "Obligated Party":

| Bad Party Name | Source |
|---|---|
| `"purpose"` | "The exclusive purpose will be..." |
| `"Term"` | "The Term may be extended..." |
| `"This Agreement"` | "This Agreement may be amended..." |
| `"Capital Contributions"` | "Capital Contributions may be amended..." |
| `"Duties of Members"` | "Duties of Members may be amended..." |
| `"An appraiser"` | "An appraiser will be appointed..." |

**Root Cause:** The fragment indicator list doesn't block common contract nouns like "purpose", "term", "agreement", "contributions", "duties", "appraiser". These words are valid English words that start with capitals (due to being at the start of a sentence or being defined terms).

---

### 🟡 Issue 4: Stative Verbs Getting Through

**Impact: ~8% of noise tasks**

Despite the `_SKIP_VERBS` set, some stative/non-actionable verbs still create tasks:

| Bad Verb | Source |
|---|---|
| `"wish"` | "Members wish to enter..." |
| `"arise"` | "disproportion that may arise..." |
| `"state"` | "rights will be as stated..." |
| `"entitle"` | "will be entitled to proceed..." |
| `"survive"` | "This section will survive..." |
| `"base"` | "interest will be based on..." |

**Root Cause:** [deontic_classifier.py line 115-120](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py#L115-L120) — `_SKIP_VERBS` is too small. It needs ~30 more stative/non-action verbs.

---

### 🟡 Issue 5: Duplicate Tasks from Same Sentence

**Impact: ~5% of noise tasks**

When a sentence matches multiple patterns (e.g., OBLIGATION + CONDITIONAL, or OBLIGATION + PERMISSION), we get duplicate tasks from the same sentence:

- Tasks 37 & 38 (BORROWMONEY): Both generate `"indemnify"` for the same sentence — one for "Member" and one for "Contracting Party"
- Tasks 44 & 9 (BORROWMONEY): Both about "locate" from same sentence — one OBLIGATION, one PERMISSION

**Root Cause:** The dedup logic in task_generator is based on `(clause_id, party, verb)` but doesn't catch `(clause_id, sentence_text)` duplicates across different obligation types.

---

### 🟡 Issue 6: Classifier Not Filtering Enough Clause Types

**Impact: ~5% of noise tasks**

The upstream clause classifier produces labels that don't get filtered. For example:
- `ENTIRE_AGREEMENT` is in `_SKIP_CLAUSE_TYPES` but the classifier labels some clause types as empty `""` (when confidence is low), so they bypass the filter entirely.
- The classifier doesn't have a `DECLARATIVE`/`RECITAL` label — meaning recitals and descriptive clauses pass through as if they're obligation-bearing clauses.

**Root Cause:** 
1. The classifier was trained with 20 categories but the `ENTIRE_AGREEMENT` is a catch-all (line 213 of training script: `mapping[idx] = "ENTIRE_AGREEMENT"`). Many non-obligation clauses get this label.
2. Empty clause_type `""` bypasses the skip filter entirely.

---

## Proposed Generalized Fixes

### Fix A: Semantic "will" Filtering (Biggest Impact)

**File:** [deontic_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py)

Use spaCy dependency parsing to distinguish obligatory vs. declarative `"will"`:

```python
def _is_obligatory_will(sentence_text: str) -> bool:
    """
    Returns True only if 'will' is used as an obligatory modal (agent + action).
    Returns False for passive/stative/declarative uses.
    
    Research basis: LEXDEMOD corpus (2024) — obligation requires a
    "capable agent" performing an "action verb", not a passive description.
    
    Filter rules:
    1. PASSIVE VOICE: "will be + past_participle" → REJECT (no agent)
    2. STATIVE SUBJECT: subject is an inanimate noun → REJECT
    3. DEFINITIONAL: "will be [noun]" / "will mean" → REJECT
    4. AGENT CHECK: subject must be a PERSON/ORG/PARTY → ACCEPT
    """
```

This single fix would eliminate ~20-25 noise tasks across the 3 test PDFs.

---

### Fix B: Passive Voice Agent Extraction

**File:** [deontic_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py)

When passive voice is detected, look for `"by + AGENT"` in the sentence:
- "payment will be made **by the Borrower**" → party = "Borrower"
- "records will be maintained" (no agent) → **SKIP** (no actionable party)

---

### Fix C: Expanded Stative Verb Blocklist

**File:** [deontic_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py)

Add ~30 more verbs to `_SKIP_VERBS`:

```python
# Non-action / stative verbs that don't create actionable obligations
_SKIP_VERBS |= {
    "wish", "desire", "intend", "believe", "consider",
    "arise", "arise", "accrue", "lapse", "expire", "elapse",
    "state", "indicate", "specify", "describe", "set",
    "survive", "continue", "remain", "persist", "endure",
    "entitle", "enable", "allow", "permit", "authorize",
    "base", "depend", "rest", "rely",
    "constitute", "represent", "form", "comprise", "consist",
    "exist", "prevail", "pertain", "refer",
}
```

---

### Fix D: Non-Entity Party Name Blocklist

**File:** [deontic_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py)

Expand `_validate_party_name()` fragment_indicators to block common defined terms:

```python
# Contract defined terms that are NOT parties
"purpose", "term", "agreement", "contract", "amendment",
"contributions", "duties", "obligations", "rights", "interests",
"appraiser", "arbitrator", "mediator",  # Roles, not parties
"capital", "funds", "assets", "property", "account",
"notice", "consent", "approval", "request",
```

---

### Fix E: Sentence-Level Dedup 

**File:** [task_generator.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/task_generator.py)

Add a sentence-level deduplication step: for any sentence that produces >2 tasks with the same verb, keep only the one with the highest confidence.

---

### Fix F: Empty Clause Type Handling

**File:** [task_generator.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/task_generator.py)

When `clause_type == ""`, run a quick heuristic check:
1. If the segment starts with "WHEREAS" or "RECITALS" → skip
2. If avg confidence < 0.5 → flag requires_review = True

---

## Expected Impact

| Fix | Estimated Noise Reduction | Complexity |
|---|---|---|
| Fix A: Semantic "will" filter | **-25 to -35 tasks** | Medium (spaCy dep parse) |
| Fix B: Passive voice agent | **-8 to -12 tasks** | Medium |
| Fix C: Stative verbs | **-10 to -15 tasks** | Easy (add to set) |
| Fix D: Party name blocklist | **-5 to -8 tasks** | Easy (add to set) |
| Fix E: Sentence dedup | **-5 to -8 tasks** | Easy |
| Fix F: Empty clause handling | **-3 to -5 tasks** | Easy |
| **TOTAL** | **-56 to -83 tasks** (from 154) | |

**Projected result: 70-98 tasks from 3 PDFs, with <10% noise.**

> [!IMPORTANT]
> **None of these fixes are PDF-specific.** They are all based on linguistic/NLP principles:
> - Passive voice detection (dependency parsing)
> - Agent identification (semantic role labeling)
> - Stative vs. action verb classification (lexicon-based)
> - Defined term recognition (pattern-based)

## Open Questions

1. **Should we re-train the classifier?** Adding a `RECITAL` or `DECLARATIVE` label would help filter non-obligation segments upstream. However, the LEDGAR dataset doesn't have this label, so we'd need to create synthetic training data or annotate manually. This is a **high-effort, medium-reward** change.

2. **Should `"will"` be demoted from OBLIGATION to PERMISSION?** Some legal drafting guides treat `"will"` as weaker than `"shall"`. We could classify `"will"` obligations as `MEDIUM` priority instead of the same level as `"shall"`.

3. **Do you want the pipeline to suppress PERMISSION tasks entirely?** Currently ~25% of tasks are PERMISSION type ("may" verbs), which are rights, not obligations. For a compliance dashboard, these may not be relevant.

## Verification Plan

### Automated Tests
- Re-run `test_task_generation.py` on the same 3 CUAD PDFs
- Compare task counts before/after
- Spot-check the 7 known garbage tasks from BORROWMONEY are eliminated

### Manual Verification
- Review the Conformis output for Stryker/Conformis party accuracy
- Verify no legitimate "will" obligations were incorrectly filtered
