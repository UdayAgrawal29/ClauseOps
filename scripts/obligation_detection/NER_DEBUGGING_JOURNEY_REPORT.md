# ClauseOps NER Debugging & Architecture Pivot Report

**Date:** June 17, 2026
**Topic:** Critical Evaluation of the Sentence-Level Pipeline on New Unseen PDFs

## 1. The Initial Problem
The pipeline was experiencing a "silent failure" where it fell back to hardcoded defaults (`Agent: Contracting Party`, `Action: perform`) instead of extracting the actual obligated parties and actions from the text. 

Upon investigation, we found that the output dictionaries from the Custom BERT NER model were misaligned with the keys expected by the pipeline.

## 2. The "Shotgun" and "Subword" Bugs
After fixing the label mapping, the true output of the Custom NER model was revealed:
- **Subword Artifacts:** The extraction output contained raw BERT subword tokens (e.g., `##s`, `[SEP]`, `[CLS]`).
- **The "Shotgun" Effect:** The model tagged *multiple* non-contiguous proper nouns in a single sentence as `AGENT`. 

## 3. Root Cause Analysis: The Dataset
The root cause was traced back to the data generation script (`generate_training_data.py`).
- The script used an LLM (Gemma) to generate "Action" spans of 10-40 words from the **CUAD** dataset. 
- By forcing a Token Classification (NER) model to predict 40 sequential words as an action, we broke the model's understanding of grammar. 

## 4. The First Pivot: Restoring spaCy
We pivoted to retaining the BERT Modality model and restoring spaCy Dependency Parsing for Agent/Action extraction.

## 5. Evaluating the First Pivot (Why it failed)
- **Model A Flaw:** The Custom Modality model was trained as a **Single-Label Sequence Classifier**. When fed a 200-word paragraph that contained *both* a PROHIBITION and an OBLIGATION, it mathematically forced itself to ignore one of them ("Label Masking").
- **Model B Flaw:** The spaCy dependency parsing logic failed on compound legal sentences, mapping subjects from sentence 1 to verbs in sentence 3.

## 6. The Second Pivot: Sentence-Level Processing
To fix this locally, we segmented the paragraphs into individual sentences using `doc.sents` and passed each sentence to BERT and spaCy independently.

## 7. CRITICAL EVALUATION ON NEW PDFS (The "Honest Review")
Following a request for a strict, critical evaluation, we tested the Sentence-Level pipeline on 3 completely new, unseen PDFs (`TEST_PDFS_NEW`).

**Findings: The output is still unacceptable for production.**

While the pipeline now correctly identifies *multiple* obligations per paragraph (solving the label-masking issue), the extraction of the actual Agent and Action is deeply flawed:
1. **Brittle Modality Detection:** In Segment 7, it completely missed the primary obligation ("2TheMart will widely promote the Services"). BERT is failing on sentences that differ slightly from its training data.
2. **Agent Resolution Failure:** In Segment 9, the text clearly states "i-Escrow shall provide" and "2TheMart may inspect". However, the spaCy logic outputted `Agent: Contracting Party` repeatedly. It is failing to resolve the grammatical subject to the actual named entities.
3. **Nonsense Verbs:** The spaCy parser is extracting strange, compound verb phrases like "pay however immediately" or "agree sign", proving that rule-based tree traversal is too rigid for the complex syntax of legal documents.

**Final Conclusion:**
Hardcoded dependency parsing (spaCy) is fundamentally incompatible with the extreme syntactic variability of legal contracts. No amount of rule-tweaking will reliably extract Agents and Actions. The only robust solution for extracting structured relationships from complex legal text is an LLM-based Information Extraction approach.
