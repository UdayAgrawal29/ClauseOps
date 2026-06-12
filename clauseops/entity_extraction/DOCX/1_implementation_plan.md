# Phase 3: Named Entity Recognition — Implementation Plan

## Goal

Extract actionable structured data (parties, amounts, dates, durations, percentages, jurisdictions) from each classified clause, bridging the gap between "this is a PAYMENT clause" and "EHN must pay Dr. Murray $8,333/month for 12 months."

## Background & Research Cross-Check

The Phase 3 NER doc proposes a **hybrid architecture** (rule-based for structured entities + fine-tuned LegalBERT for semantic entities). Here's my independent analysis of each claim:

### What I verified and agree with:

1. **General NER degrades 29–60% on legal text** (E-NER, arXiv:2212.09306) — Confirmed. SpaCy's `en_core_web_sm` was trained on OntoNotes (news, conversation). Legal text has different entity patterns (abbreviated party names like "EHN", Indian currency ₹, "within thirty (30) days" dual-format durations).

2. **Hybrid rule-based + model is the production standard** — Confirmed by every source. MONEY/DATE/DURATION follow rigid patterns where rules give 90-95% precision. PARTY/ORG require contextual understanding where a model is needed.

3. **Alias resolution from preamble is critical** — Confirmed. Every contract defines "EHN" → "Emerald Health Nutraceuticals Inc." in the opening paragraph. Without resolving these, the task generator can't name the obligated party.

### What I disagree with or would change:

1. **The doc recommends `nlpaueb/legal-bert-base-uncased` for NER** — This is problematic. The doc itself notes that SpotDraft uses **cased** BERT because party names are case-sensitive ("EHN" vs "ehn"). But `legal-bert-base-uncased` is **uncased** — it throws away casing information. For an MVP, I'd rather use `spaCy en_core_web_trf` (RoBERTa-based, cased, already has PERSON/ORG/MONEY/DATE/GPE labels at ~90% F1 on OntoNotes) and supplement with custom rules, than fine-tune an uncased model on E-NER data we may not even have enough of.

2. **The doc proposes training a NER model on E-NER + manually annotated CUAD data** — This is a Weeks 3-4 effort with unclear ROI. E-NER has PER/ORG/LOC/MISC labels on SEC filings, not PARTY/ALIAS/JURISDICTION. You'd need to manually relabel hundreds of examples. For an MVP, spaCy's built-in PERSON/ORG recognition + rule-based alias resolution gives you 80% of the value with 10% of the effort.

3. **The doc treats DATE and DURATION as separate problems** — SpaCy's `en_core_web_trf` already tags both as `DATE` entities. The real challenge is distinguishing them (is "30 days" a duration or a date?) and normalizing relative expressions. Custom rules on top of spaCy's DATE output handle this cleanly.

### My revised architecture:

```
ClauseChunk.body_text
    │
    ├──► spaCy en_core_web_trf (built-in NER)  → PERSON, ORG, MONEY, DATE, PERCENT, GPE
    │    [No training needed, ~90% F1]           [Covers 6 of 8 entity types immediately]
    │
    ├──► Custom EntityRuler (spaCy)             → DURATION, legal DATE patterns
    │    [Rule-based, covers gaps]               [Handles "thirty (30) days", "two-year"]
    │
    ├──► Alias Extraction (regex)               → Maps "Licensor" → "Data Call Technologies"
    │    [From preamble/definitions only]         [Pattern-based, no training]
    │
    └──► Post-processing                        → Merge, deduplicate, resolve aliases
         [Deterministic logic]                    [Map spaCy labels to our schema]
```

**Key insight the doc misses:** `en_core_web_trf` already handles MONEY, DATE, PERSON, ORG, PERCENT, GPE out of the box at ~90% F1. We don't need a separate EntityRuler for MONEY/DATE — we need one for DURATION (which spaCy conflates with DATE) and for legal-specific date formats that spaCy misses.

> [!IMPORTANT]
> This plan deliberately avoids model training in Phase 3. We use spaCy's pre-trained `en_core_web_trf` + rules + alias extraction. If entity quality is insufficient after testing, we can fine-tune in a follow-up sprint. The MVP philosophy: **ship a working pipeline first, optimize later.**

---

## Resolved Questions

1. **`en_core_web_trf` performance:** Will be slower on CPU (~2-5s per clause) but acceptable. If model training is ever needed later, Kaggle will be used.

2. **No manual annotation possible.** This is fine — the MVP plan uses spaCy's pre-trained model (zero training, zero annotation). If we later find quality is insufficient, we can fine-tune on the E-NER dataset (already annotated by researchers, no manual work needed) on Kaggle.

3. **Fallback path (only if spaCy NER proves inadequate):** Fine-tune `bert-base-cased` on the E-NER dataset (PER/ORG/LOC/MISC labels already annotated). Map PER→PARTY, ORG→ORG, LOC→JURISDICTION. Train on Kaggle. But we likely won't need this.

---

## Proposed Changes

### Entity Extraction Module

#### [NEW] [\_\_init\_\_.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/__init__.py)
Module init exposing `extract_entities_from_clause()` and `extract_entities_from_contract()`.

#### [NEW] [extractor.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/extractor.py)
Core extraction engine:
- `_load_nlp()`: Singleton spaCy `en_core_web_trf` loader (like our classifier's `_load_model()`)
- `_extract_spacy_entities(text)`: Run spaCy NER, map labels to our schema (PERSON→PARTY, GPE→JURISDICTION, PERCENT→PERCENTAGE)
- `_extract_duration_entities(text)`: Custom regex/patterns for DURATION entities that spaCy misses or conflates with DATE
- `extract_entities_from_clause(chunk, alias_map)`: Master function — runs spaCy + duration rules + alias resolution + deduplication

#### [NEW] [alias_resolver.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/alias_resolver.py)
Alias extraction and resolution:
- `extract_alias_map(full_contract_text)`: Regex patterns to extract alias definitions from preamble ("EHN, a Delaware corporation ('Licensor')")
- `resolve_aliases(entities, alias_map)`: Replace alias references with full names in entity output

#### [NEW] [duration_patterns.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/duration_patterns.py)
Custom patterns for DURATION entities:
- Numeric: "30 days", "24 months", "2 years"
- Written-out: "thirty (30) days", "sixty days"
- Compound: "two-year", "five-year term"
- Business days: "30 business days", "14 working days"

---

### Integration

#### [MODIFY] [app.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/app.py)
- Add NER stage to `_run_segmentation()` pipeline (after classification, before response building)
- Add `entities` field to each clause in the JSON response
- Add entity badges/tags to the UI card for each clause

#### [MODIFY] [\_build\_response()](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/app.py)
- Include `entities` dict in each clause item
- Add entity summary stats to the response

---

### Testing

#### [NEW] [test_entity_extraction.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/scripts/test_entity_extraction.py)
Test script similar to `test_classifier.py`:
- Process 3-5 PDFs through full pipeline (segment → classify → extract entities)
- Generate `ENTITY_EXTRACTION_OUTPUTS.md` report
- Verify known entities: "$8,333" → MONEY, "Dr. Murray" → PARTY, "twelve months" → DURATION

---

## Entity Label Schema

| Our Label | Source | spaCy Label Mapping | Example |
|---|---|---|---|
| `PARTY` | spaCy NER | PERSON → PARTY | "Dr. Murray", "Edward Rogers" |
| `ORG` | spaCy NER | ORG (keep as-is) | "Emerald Health Sciences Inc." |
| `MONEY` | spaCy NER | MONEY (keep as-is) | "$8,333", "USD 100,000" |
| `DATE` | spaCy NER | DATE (when absolute) | "January 30, 2027", "April 1, 2006" |
| `DURATION` | Custom rules | DATE (when relative) + custom | "60 days", "twelve months", "two-year" |
| `PERCENTAGE` | spaCy NER | PERCENT → PERCENTAGE | "6%", "50%" |
| `JURISDICTION` | spaCy NER | GPE → JURISDICTION (in legal context) | "Karnataka", "New York", "Ontario" |
| `ALIAS` | Alias resolver | Regex from preamble | "Licensor", "Franchisee", "plan_b" |

---

## Verification Plan

### Automated Tests
- Run entity extraction on 3-5 known CUAD contracts from our test set
- Verify specific known entities against ground truth:
  - Dev Agreement: "$8,333" (MONEY), "Dr. Murray" (PARTY), "twelve months" (DURATION)
  - License Agreement: "Data Call Technologies" (ORG), "plan_b" (ALIAS)
  - ChinaRealEstate: "ten (10) years" (DURATION), "Beijing SINA" (ORG)
- Generate detailed markdown report with entity extraction results per clause

### Manual Verification
- Review entity extraction quality visually in the UI
- Spot-check alias resolution accuracy on 2-3 contracts
- Verify no false positives on SIGNATURE_BLOCK/PREAMBLE filtered clauses (they should be skipped)
