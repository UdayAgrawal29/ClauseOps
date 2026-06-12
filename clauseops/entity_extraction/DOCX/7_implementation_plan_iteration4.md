# Phase 3.5c: Fix Remaining Alias Leaks + DATE/DURATION Confusion

## Problem Summary

After fixing the first round of alias overextraction bugs, testing on 3 new unseen CUAD contracts revealed **two remaining critical issues** and one medium-priority issue:

1. **"The Products" and "Technology" as PARTY** — appearing in 20+ segments across the Cybergy contract
2. **DATE vs DURATION confusion** — "thirty (30) days", "ninety (90) days" tagged as DATE instead of DURATION
3. **ORG false positives** — "FOB", "Mail", "Commission" tagged as ORG (minor)

## Root Cause Analysis

### Bug 1: "The Products" / "Technology" as PARTY

**Two failures in the current filter:**

1. **Singular/plural mismatch in `_CONCEPT_WORDS`**: We have `"product"` but the alias is `"The Products"` (plural). The set lookup `alias_words & _CONCEPT_WORDS` does exact matching, so `"products"` ≠ `"product"`.

2. **Antecedent proximity too generous in dense preambles**: The preamble text packs party introductions and defined terms within 200 chars of each other:
   ```
   MOUNT KNOWLEDGE HOLDINGS INC. ... ('MA')...
   ...software products... (the 'Technology')
   ```
   "MOUNT KNOWLEDGE HOLDINGS INC." is a valid ORG within 200 chars of `('Technology')`, so the antecedent validation passes incorrectly.

**Generalized fix approach (NO hardcoded stopword lists):**
- Use **stem-based matching** for concept words instead of exact string matching. This way `"products"` → stem `"product"` → matches. `"services"` → stem `"service"` → matches. No need to enumerate every plural/variant form.
- Additionally, **validate that the antecedent appears in the same syntactic clause** as the alias trigger. If there's a period, semicolon, or another alias trigger `('...')` between the antecedent and the current alias, reject it — the antecedent belongs to a different definition.

> [!IMPORTANT]
> The other AI suggested adding "products", "technology", "service", "content" to `_CONCEPT_WORDS`. That works for THIS contract but fails on a construction contract with "Building Products Inc." (a real company). My stem-based approach handles this by also checking company suffixes — if an alias ends with Inc/Corp/LLC/Ltd, always accept it regardless of concept words.

### Bug 2: DATE vs DURATION Confusion

**Root cause**: spaCy's `en_core_web_trf` tags "thirty (30) days" as `DATE` because it's a temporal expression. Our `duration_patterns.py` also finds it as `DURATION`. The `_apply_semantic_filtering()` in `extractor.py` only promotes DATE→DURATION when the governing verb is in `{"last", "continue", "expire", "renew", "extend"}` — but most occurrences have verbs like "notify", "pay", "deliver" which are NOT in that set.

**Generalized fix**: If a spaCy `DATE` entity matches the duration regex pattern (number + time unit), **always reclassify it as DURATION** regardless of verb context. This is linguistically correct: "thirty (30) days" is never an absolute date, it's always a relative time span.

### Bug 3: ORG False Positives (Minor)

"FOB", "Mail", "Commission" — these are spaCy NER errors on short uppercase or capitalized words. Accept for MVP. The fix would be a minimum 4-char/2-word filter for ORG entities, but that risks dropping real short ORGs like "IBM", "NCM", "MA".

---

## Proposed Changes

### Fix 1: Stem-based concept word matching + inter-clause boundary check

#### [MODIFY] [alias_resolver.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/alias_resolver.py)

1. Add a `_stem_match_concept()` helper that checks if ANY word in the alias, after stemming, matches any concept word root. Uses a minimal Porter-style suffix strip (just `-s`, `-es`, `-ing`, `-tion`) — no external dependency needed.

2. Add company suffix bypass: if alias ends with `Inc`, `Corp`, `LLC`, `Ltd`, `GmbH`, `AG`, `PLC`, `Co`, always accept it even if concept words match.

3. In `extract_alias_map()`, add an **inter-clause boundary check**: if there's another alias trigger `('...')` between the candidate antecedent's position and the current trigger, reject the antecedent (it belongs to a different party/definition clause).

### Fix 2: DATE→DURATION reclassification

#### [MODIFY] [extractor.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/extractor.py)

In `_apply_semantic_filtering()`, change the logic: instead of only promoting DATE→DURATION when verb ∈ {last, continue, expire, renew, extend}, **always promote DATE→DURATION when the DATE text matches the duration regex pattern**. This is a pattern-based override, not a verb-based one.

---

## Verification Plan

### Automated Tests
1. Re-run pipeline on the same 3 CUAD PDFs (`TEST_PDFS_NEW`)
2. Verify "The Products" and "Technology" are gone from PARTY summaries
3. Verify "thirty (30) days", "ninety (90) days" etc. are labelled DURATION, not DATE
4. Verify NVOS/HGF aliases from the original 5 PDFs still work (regression check)

### Regression Checks
- Confirm `BIRCH FIRST GLOBAL INVESTMENTS INC.` and `Company` still resolve correctly
- Confirm `NCM` and `Network Affiliate` still resolve in DigitalCinema contract
