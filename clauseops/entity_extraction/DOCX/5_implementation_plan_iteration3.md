# Phase 3.5b: Fixing Alias Overextraction, Entity Fragments, and Relation Noise

## My Independent Verdict on the Other AI's Analysis

I read the other AI's report carefully, then independently audited the actual [PIPELINE_OUTPUTS_RELATIONS.md](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/DOCX/PIPELINE_OUTPUTS_RELATIONS.md) and the current code. Here is what I **agree with**, what I **disagree with**, and what it **missed**.

---

### ✅ What I Independently Confirmed

**Bug 1 (Alias overextraction) — CONFIRMED, CRITICAL.**
I verified: `PARTY: term` appears in 4 separate clauses across multiple contracts (lines 80, 604, 1593, 2537). `PARTY: viewing period` appears in Euromedia Segment 7 (line 918). `PARTY: marks` in EcoScience Segment 15 (line 436). `PARTY: Retail, retail revenues, basic, retail` in Euromedia Segment 8 (line 940). These are ALL defined contract terms, not parties. The root cause analysis is correct: `_looks_like_party_alias()` at line 49 passes any Title Case multi-word phrase, and line 51 passes any 3-20 char string with a single uppercase letter. This is far too permissive.

**Bug 2 (Entity fragments "Holdings ", "Communications ") — CONFIRMED, CRITICAL.**
I verified: `PARTY: Holdings , Communications ` (with non-breaking spaces / special chars) appears in nearly EVERY Euromedia segment (lines 780, 803, 849, 873, 895, 918, 940, 964, 987, 1011, 1033). The root cause is the infinite backward scan in `extract_alias_map()` (line 79-81) — it takes the **last** ORG/PERSON before the trigger, not the **closest**. When spaCy splits "Rogers Cable Communications Inc." into fragments, a fragment like "Communications" becomes the last ORG before the alias trigger, and gets mapped as the full name.

**Bug 3 (Self-relations and trivial verbs) — CONFIRMED, MEDIUM.**
I verified: `ESSI -> have -> ESSI (PARTY)` at line 111, `ESSI -> have -> Term (PARTY)` at lines 108, 322. `Talent -> have -> Talent (PARTY)` at line 383. `Talent -> use -> Talent (PARTY)` at line 323. These are noise. "Have" in legal text means entitlement/possession, not an actionable obligation.

**Euromedia zero relations — CONFIRMED.**
I verified: 0 "Extracted Relations:" blocks across all 35 Euromedia segments. The other AI's cascade theory is plausible: broken alias map → malformed EntityRuler patterns → entity span misalignment → `get_ent_at_index()` can't find `nsubj` entities → no relations.

---

### ⚠️ Where I Disagree with the Other AI

**1. The stopword approach is NOT generalized.**
The other AI proposed adding a massive `_ALIAS_STOPWORDS` set with 40+ terms like `"rod service"`, `"vod service"`, `"retail revenues"`. This is **exactly the PDF-specific hardcoding you told me to avoid**. If tomorrow we process a construction contract with "Building Period" or "Site Fee", those would slip through because they're not in the stoplist. Instead, we need a **structural/linguistic** filter.

**2. The `_ALIAS_CONCEPT_KEYWORDS` blocklist is fragile.**
Blocking any alias containing words like "date", "service", "work" would reject legitimate party names like "WorkDay Inc." or "ServiceNow". This is a false-negative factory.

**3. The "Title Case only if role word" rule is too restrictive.**
The other AI says Title Case multi-word aliases should only pass if a word is in `_PARTY_ROLE_WORDS`. This would reject legitimate party aliases like "Harvest Gold" or "Novo Healthnet" because neither "harvest", "gold", "novo", nor "healthnet" are role words. We'd break the Novo JV Agreement alias extraction that we just fixed.

---

### 🔬 My Generalized Approach (Research-Backed)

Based on the 2025-2026 research on Legal NER disambiguation:

> The core insight from the literature is: **a party alias always appears with an antecedent ORG/PERSON entity in the same sentence**. A defined term like "Term" or "Viewing Period" appears with a **common noun** antecedent or no antecedent at all.

Instead of trying to classify the alias itself (which is linguistically ambiguous), we should **validate the antecedent**. If the nearest preceding entity is a genuine ORG or PERSON recognized by the transformer model, the alias is a party alias. If there's no such entity nearby, or the "antecedent" is a fragment, it's a defined term and should be rejected.

---

## Proposed Changes

### [MODIFY] [alias_resolver.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/alias_resolver.py)

**Fix 1a — Proximity constraint (fixes entity fragments and infinite lookback):**
- Only consider ORG/PERSON entities that end within **200 characters** of the alias trigger.
- Require the antecedent entity text to be at least **5 characters** long (rejects fragments like "Inc." or "Corp").
- Require the antecedent entity text to contain at least **2 words** OR be all-caps (rejects single-word fragments like "Communications" or "Holdings" while accepting "NVOS").

**Fix 1b — Antecedent-based validation (replaces stopword bloat):**
- Instead of maintaining a giant stopword list, add a small set of **truly universal** stopwords (the ones from contracts across ALL jurisdictions: "agreement", "term", "effective date", "party", "parties").
- For everything else, require that the antecedent entity is a **multi-token proper noun** or a **known role word**. If the trigger `("Viewing Period")` has no valid ORG/PERSON antecedent nearby, it is silently rejected.

**Fix 1c — Tighten `_looks_like_party_alias()` without breaking generality:**
- Keep all-caps acronyms (ESSI, NVOS, HGF) unconditionally — these are always parties.
- Keep role words (Licensor, Licensee) unconditionally.
- For Title Case multi-word: require that the alias does NOT contain common legal concept words ("period", "date", "revenue", "fee", "term"). This is a narrow negative filter, not a massive stopword list.
- Remove the overly broad rule at line 51 (`any(c.isupper() for c in alias_clean)`) — this passes literally everything.

---

### [MODIFY] [extractor.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/extractor.py)

**Fix 2 — Post-filter relations to remove self-references and trivial verbs:**
- Drop relations where `subject == object` (self-references).
- Drop relations with verbs that carry no obligation semantics: `have`, `be`, `include`, `contain`, `mean`, `define`, `refer`.
- This is a linguistically sound filter — the 2025-2026 research on obligation extraction consistently uses verb classification to separate "deontic" verbs (shall, must, agree, provide, deliver, pay, grant, notify, terminate) from "stative" verbs (have, be, include, mean).

---

## Open Questions

> [!IMPORTANT]
> **On the concept-word filter:** I'm proposing a small negative set (`period`, `date`, `revenue`, `fee`, `service`, `content`, `program`, `mark`, `work`) that blocks Title Case aliases containing these words. This is much narrower than the other AI's 40-term stoplist, but it still has a small risk of rejecting a legitimate party name containing "Service" (like "Service Corp International"). Should I add an exception for aliases where the concept word is followed by "Inc", "Corp", "LLC", etc.?

## Verification Plan

1. Re-run the full pipeline on the same 5 test PDFs.
2. Verify that `PARTY: term`, `PARTY: viewing period`, `PARTY: marks`, `PARTY: retail revenues` are ALL gone.
3. Verify that `PARTY: Holdings , Communications ` fragments are gone from Euromedia.
4. Verify that Euromedia now produces extracted relations (since the alias map and EntityRuler will be clean).
5. Verify that `NVOS -> Novo Integrated Sciences Inc.` and `HGF -> Harvest Gold Farms Inc.` still work in the Novo JV Agreement.
6. Verify that self-relations (`ESSI -> have -> ESSI`) are gone.
