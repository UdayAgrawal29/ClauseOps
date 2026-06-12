# Generalized Zero-Anaphora and Alias Resolution Plan

Based on the latest 2025-2026 NLP research for legal document processing, relying on rigid regex rules or flat extraction is insufficient. The research dictates that to solve "Zero Anaphora" (missing/implicit subjects like in "1.1 To maintain..."), models must utilize **Hierarchical Context Propagation**. Additionally, to solve alias resolution generally, we must shift from Regex to **Syntactic Dependency Matching**.

Below is the proposed, fully generalized, API-free local architecture to solve these two issues permanently.

## User Review Required
> [!IMPORTANT]
> Please review this approach. We are replacing the hardcoded regex with a generalized linguistic model (spaCy DependencyMatcher), and adding a hierarchical tree parser to inherit subjects from headings. Does this align with your vision for a generalized, non-PDF-specific solution?

## Proposed Changes

### 1. Hierarchical Context Propagation (Zero Anaphora Resolution)
When legal contracts use bullet points or numbered lists (e.g., "10.1 To assist the Company"), the grammatical subject is omitted because it is implied by the parent heading (e.g., "ARTICLE 10 - OBLIGATIONS OF HGF"). 
* **Research Approach**: In 2026, the SOTA approach is to propagate entities down the document tree.
* **Implementation**: We will modify `extractor.py`. When we process a `ClauseChunk`, we will first extract entities from the `chunk.heading`. If the relation extractor finds a verb without a syntactic subject (`nsubj`), it will "inherit" the `PARTY` entity found in the heading. If no party is in the heading, it will default to a placeholder like `[Implied Party]`.

### 2. Syntactic Alias Resolution (spaCy DependencyMatcher)
The regex engine failed because it explicitly expected `hereinafter`, but the contract used `herein` inside parentheses. Regex cannot easily parse nested grammatical structures without becoming impossibly complex and brittle.
* **Research Approach**: We will use spaCy's `DependencyMatcher` to search for the *linguistic structure* of an alias assignment, rather than exact text. 
* **Implementation**: We will rewrite `extract_alias_map` in `alias_resolver.py`. We will create a generalized rule that looks for:
  1. A proper noun or noun phrase (The full name)
  2. A defining verb lemma (`refer`, `mean`, `call`, `know`) or defining punctuation (parentheses/quotes).
  3. A short proper noun (The alias).
  This will universally catch aliases regardless of whether the lawyer used "hereinafter", "herein", "collectively referred to", or just `("Alias")`.

---

### File Modifications

#### [MODIFY] [alias_resolver.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/alias_resolver.py)
- Remove `_ALIAS_PATTERNS` regex list.
- Implement `extract_alias_map` using `spacy.matcher.Matcher` or `DependencyMatcher` to identify the semantic structure of aliases.

#### [MODIFY] [extractor.py](file:///c:/Users/Uday%20Agrawal/Desktop/Projects/ClauseOps/clauseops/entity_extraction/extractor.py)
- Update `extract_entities_from_clause` to run NER on the `chunk.heading`.
- Update `_extract_relations` to accept a `context_subject` parameter.
- Add logic to capture verbs that act as the root of infinitive phrases (e.g. `xcomp` or `ROOT` with `VerbForm=Inf`) and assign them the `context_subject`.

## Verification Plan
1. Re-run the full pipeline without OCR.
2. Verify that `NVOS` and `HGF` are correctly identified as aliases in the Joint Venture Agreement, regardless of the `herein` phrasing.
3. Verify that Segment 16 successfully outputs relations like `HGF -> assist -> Company`, inheriting the subject from the "OBLIGATIONS OF HGF" heading.
