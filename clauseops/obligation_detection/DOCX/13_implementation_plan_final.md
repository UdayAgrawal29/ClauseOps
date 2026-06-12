# Phase 4 v4: Root Cause Analysis & Fresh Architecture

## The Real Problem

After 3 iterations of fixes (v1→v2→v3), we're generating **506 tasks from 5 PDFs** (22-56 segments each). That's an average of **5.5 tasks per segment**. A well-designed contract compliance system should produce approximately **0.5-1.5 tasks per segment** (many segments have no actionable obligation at all).

The v3 fixes (semantic "will" filter, stative verb blocklist, party name blocklist) helped on BORROWMONEY (55→45, -18%) but made Conformis **worse** (59→102, +73%). The fixes are fighting the architecture, not fixing it.

---

## Root Cause Analysis

### Root Cause 1: Sentence-Level Architecture is Fundamentally Wrong

The current system:
1. Takes a clause (1 segment = 1 paragraph of contract text)
2. **Splits it into sentences** via spaCy
3. Runs obligation detection on **each sentence independently**
4. Generates 1 task per sentence × per obligation × per deadline

A single IP clause with 6 sub-paragraphs (a-f) produces **12 tasks** because each sub-paragraph sentence triggers independently.

**Why this is wrong:** In legal contracts, a clause is the atomic unit of obligation. Sub-paragraphs (a), (b), (c) are elaborations of the SAME obligation, not separate ones. Sentence-level splitting destroys the clause's structural intent.

**Evidence:** The Conformis IP clause `6fb03766` generates:
- Task: "Stryker shall vest" (sub-para b)
- Task: "Conformis shall agree" (sub-para b, continued)
- Task: "Stryker shall own" (sub-para c)
- Task: "Conformis shall disclose" (sub-para e)
- Task: "Stryker shall disclose" (sub-para e, duplicate from different sentence)
- Task: "Party shall prepare" (sub-para f)

These are 6+ separate tasks for ONE IP clause. A human compliance officer would create **1 task**: "IP Ownership — ensure proper assignment and disclosure per Section X".

### Root Cause 2: The "will" Semantic Filter Doesn't Work Reliably

The `_is_obligatory_will` function uses `en_core_web_sm` for dependency parsing, which has ~88% accuracy on legal text (vs ~95% on general text). This means:
- ~12% of "will" sentences are incorrectly classified
- Some passive constructions slip through → noise tasks
- Some real obligations get filtered → missed tasks

More critically, the DECLARATIVE gate (`if modality == "DECLARATIVE": return []`) is too aggressive. It blocks ALL processing (including Path A relations) when "will" is rejected. But some of those sentences have legitimate relations from the NER phase that should still generate tasks.

### Root Cause 3: Path A / Path B Dual Architecture Creates Duplicates

When a sentence has relations from NER:
1. Path A iterates over each relation → 1 task per relation
2. If no relations, Path B extracts from body text → 1 task

When a sentence has BOTH a relation AND a modal, both paths can fire. And when the same party appears as both subject and object in different relations within the same sentence, you get duplicates.

---

## Previous Phases Assessment

### Phase 1 (Segmentation) — ✅ NO ISSUES
- Clean clause boundaries
- Correct heading detection
- No over/under-fragmentation
- **Verdict:** Segmentation is NOT causing the problem.

### Phase 2 (Classification) — ✅ NO ISSUES FOR TASK GENERATION
- Macro-F1 = 0.937
- Core task-generating categories (TERMINATION, PAYMENT, DELIVERY, REPORTING) all >0.95 F1
- ENTIRE_AGREEMENT catch-all is harmless — we skip those in `_SKIP_CLAUSE_TYPES`
- **Verdict:** Classification is NOT causing the problem. No retraining needed.

### Phase 3 (Entity Extraction/NER) — ⚠️ MINOR CONTRIBUTOR
- PARTY extraction is accurate
- DATE/DURATION distinction works
- Relations extraction is good BUT it feeds into Path A which amplifies task count
- **Issue:** Relation extraction produces 2-5 relations per clause, each becoming a separate task. This is correct behavior for relation extraction but wrong for task generation.
- **Verdict:** NER is working correctly. The problem is how Phase 4 CONSUMES its output.

### Phase 4 (Obligation Detection) — ❌ ARCHITECTURAL PROBLEM
- The sentence-level design multiplies tasks exponentially
- The rule-based modal matching catches TOO MANY sentences
- The dual-path (relation + body) creates redundancy
- **Verdict:** This phase needs a fresh architecture.

---

## Proposed Fresh Architecture: "Primary Obligation" Model

### Core Principle: **One primary task per clause**

Instead of splitting into sentences and generating a task from every modal verb, we:
1. Analyze the **entire clause** as a unit
2. Extract the **primary obligation** (the main thing someone must do)
3. Generate **exactly 1 task** (with optional sub-items for multi-part clauses)

### Architecture

```
┌──────────────────────────────────────────┐
│  Clause Input (body_text + metadata)     │
│  from Segmentation → Classification → NER│
└─────────────┬────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────┐
│  Step 1: GATE — Should this clause       │
│          generate any task at all?       │
│  • Skip: DEFINITIONS, PREAMBLE,          │
│    RECITALS, ENTIRE_AGREEMENT,           │
│    GOVERNING_LAW, SIGNATURE_BLOCK        │
│  • Skip: No modal verb detected at all   │
│  • Skip: Body < 20 tokens               │
└─────────────┬────────────────────────────┘
              │ passes gate
              ▼
┌──────────────────────────────────────────┐
│  Step 2: PRIMARY OBLIGATION EXTRACTION   │
│  • Scan FULL clause for ALL modals       │
│  • RANK modals by strength:              │
│    shall > must > agrees_to > will > may │
│  • Take the STRONGEST modal as primary   │
│  • Extract its governing verb via dep    │
│    parse (spaCy)                         │
│  • Extract subject (obligated party)     │
│    from the primary modal's sentence     │
└─────────────┬────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────┐
│  Step 3: PARTY RESOLUTION               │
│  • Use NER PARTY entities for validation │
│  • If subject is generic ("Party"),      │
│    resolve from heading or context       │
│  • Extract beneficiary if present        │
└─────────────┬────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────┐
│  Step 4: DATE EXTRACTION                 │
│  • Scan FULL clause for date entities    │
│  • Pick the most specific date/deadline  │
│  • Normalize relative durations          │
└─────────────┬────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────┐
│  Step 5: SINGLE TASK GENERATION          │
│  • Generate 1 TaskRecord per clause      │
│  • Title = f"{party} shall {verb}"       │
│  • Include ALL sub-obligations in        │
│    description (not as separate tasks)   │
│  • Attach date, priority, etc.           │
└──────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Exception: Multi-obligation clauses.** Some clauses genuinely contain multiple distinct obligations (e.g., "Section 5: (a) Stryker shall pay $X. (b) Conformis shall deliver Y."). For these, we detect sub-section markers (a), (b), (i), (ii) and generate **max 1 task per sub-section**, but ONLY if each sub-section has its own modal+subject.

### What Changes in Each File

#### [MODIFY] [deontic_classifier.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/deontic_classifier.py)

**Major rewrite of `classify_obligation()`:**
- Remove Path A/B dual architecture
- New function: `extract_primary_obligation()` — scans full clause, finds strongest modal, returns single ObligationRecord
- New function: `_rank_modals()` — returns all modals ranked by deontic strength
- Remove `_is_obligatory_will()` complexity — handle "will" via ranking instead (it's simply the weakest obligatory modal, so if "shall" or "must" exist in the same clause, "will" is ignored)
- Remove `_extract_from_body()` — no longer needed
- Keep `_validate_party_name()`, `_get_best_party()` — they work fine
- Keep `_SKIP_VERBS` — still useful for filtering stative verbs

#### [MODIFY] [task_generator.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/task_generator.py)

**Major rewrite of `generate_tasks_for_clause()`:**
- Remove sentence splitting entirely for the primary path
- New flow: call `extract_primary_obligation()` on full clause body → get 1 obligation → create 1 task
- New function: `_detect_sub_obligations()` — for clauses with (a), (b), (c) sub-sections, check if each has its own distinct modal+subject. If yes, generate 1 task per sub-section
- Remove `_is_actionable_sentence()` — no longer needed at sentence level
- Keep all date normalization logic — works fine
- Keep `_compute_priority()`, `_format_title()`, `_build_description()` — work fine
- Simplify `_dedupe_tasks()` — much less needed with 1-task-per-clause model

#### [MODIFY] [date_normalizer.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/obligation_detection/date_normalizer.py)

**Minor change:** Accept full clause entities instead of sentence-filtered entities.

---

## Expected Impact

| Contract | v2 Tasks | v3 Tasks | v4 Expected |
|---|---|---|---|
| BORROWMONEY (39 seg) | 55 | 45 | ~20-25 |
| Conformis (29 seg) | 59 | 102 | ~18-22 |
| Creditcards (31 seg) | 40 | 44 | ~15-20 |
| Cybergy (56 seg) | — | 108 | ~25-35 |
| DigitalCinema (22 seg) | — | 207 | ~15-20 |
| **TOTAL** | **154** | **506** | **~95-120** |

The target is **<1.5 tasks per segment** on average. For a 30-segment contract, that's 20-45 tasks — each representing a genuine, distinct obligation.

---

## Open Questions

> [!IMPORTANT]
> **Q1: Should multi-obligation sub-sections generate separate tasks?**
> Example: Section 5(a) says "Stryker shall pay $X" and 5(b) says "Conformis shall deliver Y". 
> Option A: 1 task for the whole section (simpler, fewer tasks)
> Option B: 1 task per sub-section (more granular, more actionable)
> My recommendation: **Option B** — but only when sub-sections have distinct subjects or verbs.

> [!WARNING]
> **Q2: What to do with PERMISSION tasks?**
> Currently ~15% of tasks are PERMISSION type (from "may" modals). These aren't obligations — they're rights.
> Option A: Keep them at LOW priority (current behavior)
> Option B: Remove them entirely from task output
> Option C: Separate section in the report ("Rights & Permissions" vs "Obligations")
> My recommendation: **Option C** for the final product. For now, keep Option A.

## Verification Plan

### Automated Tests
- Re-run on all 5 test PDFs
- Assert: total tasks < 150 (vs current 506)
- Assert: no clause produces > 3 tasks (vs current max of 12)
- Assert: average tasks/segment < 1.5

### Manual Verification
- Spot-check 10 randomly selected tasks against PDF source
- Verify each task maps to a real, distinct obligation
- Verify no critical obligations are missed (check TERMINATION, PAYMENT clauses specifically)
