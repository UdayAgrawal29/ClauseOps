# Training Data Generation Report

**Generated:** 2026-06-17 01:19
**Total clauses annotated:** 1998
**Failures:** 0

## Class Distribution

| Modality | Count | Percentage |
|----------|-------|------------|
| OBLIGATION | 448 | 22.4% |
| PROHIBITION | 259 | 13.0% |
| PERMISSION | 305 | 15.3% |
| DECLARATIVE | 986 | 49.3% |

## Data Sources

| Source | Count |
|--------|-------|
| CUAD | 2000 |
| LEDGAR | 4000 |
| Test PDFs | 106 |

## Output Files

- `modality_train.jsonl` — Training data for modality classifier
- `modality_val.jsonl` — Validation data
- `modality_test.jsonl` — Test data
- `ner_train.jsonl` — Training data for agent+action NER
- `ner_val.jsonl` — Validation data
- `ner_test.jsonl` — Test data

## Next Steps

1. Upload `training_data/` folder to Kaggle
2. Run the training notebook (cell-wise)
3. Download trained models
4. Integrate into ClauseOps pipeline
