# Fresh Dataset Evaluation — 274 Records

## 1. Data Source Analysis

### What's Actually Being Used

| Source | Records | % | Status |
|--------|---------|---|--------|
| LEDGAR | 191 | 70% | Working — loaded from HuggingFace |
| CUAD_EXPERT | 83 | 30% | Working — loaded from local `CUAD_v1.json` |
| PDF_NOISY | 0 | 0% | **BROKEN** — 0 PDFs found |

### The PDF Problem

The script looks for PDFs in:
```
C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf\Part_I\*.pdf
```

But there are **0 PDFs directly in `Part_I`**. The 198 PDFs are in **subfolders**:
```
Part_I\Affiliate_Agreements\*.pdf
Part_I\Co_Branding\*.pdf
Part_I\Development\*.pdf
... (10 subdirectories)
```

The script uses `pdf_dir.glob("*.pdf")` — non-recursive — so it finds nothing.

> [!WARNING]
> **Impact:** The model trains on ZERO noisy/OCR-degraded text. In production, your pipeline processes real PDFs through Docling, which produces messy segmented text. The model has never seen this noise profile and will likely underperform on real-world PDFs.
> 
> **Fix:** Change `glob("*.pdf")` to `rglob("*.pdf")` (recursive glob).

### CUAD_EXPERT Source Quality

The CUAD data is loaded from the SQuAD-format JSON (`CUAD_v1.json`). This extracts **expert-annotated answer spans** — the gold-standard clauses that humans highlighted as relevant to specific legal categories. This is excellent training data because:
- Each clause has a semantic category (e.g., "Audit Rights", "Insurance")
- The spans are human-curated, not randomly sampled
- Diverse contract types are represented

### LEDGAR Source Quality

LEDGAR provides SEC filing provisions with 100 contract-type labels. Good diversity but all from SEC filings — potentially biasing toward public company language. Not a concern for your use case (ClauseOps processes exactly these types of contracts).

---

## 2. Data Quality — The Honest Verdict

### What's Good (genuinely good, not yes-man good)

- **0 hallucinated agents** — every agent string exists in its clause text
- **0 hallucinated actions** — every action string exists in its clause text  
- **0 null agents in non-DECLARATIVE** — the Fix1 cleanup works
- **0 duplicates** — MD5 dedup is working
- **0 over-saturated categories** — no single category dominates
- **Reasoning quality is high** — the model explains WHY, not just WHAT

### What's Bad

#### Problem A: 16 records have agent but NULL action (5.8%)

These are non-DECLARATIVE clauses where the model found the agent but couldn't extract the action. Examples:

| Line | Modality | Agent | Why action is null |
|------|----------|-------|--------------------|
| L263 | PROHIBITION | "Reseller" | **Fragment clause** — text is literally "In particular, and without limitation, Reseller shall not" — the clause was truncated before the action |
| L272 | OBLIGATION | "Franchisee" | OCR-corrupted text from CUAD — garbled formatting broke action extraction |
| L30 | PROHIBITION | "a Party" | The action text was paraphrased by the model, so the action-in-clause validation dropped it |

> [!IMPORTANT]
> **Impact:** These 16 records will produce NER training examples where the model sees an AGENT tag but no ACTION tag. This teaches the model that actions are optional — which is wrong.
> 
> **Fix:** Filter out non-DECLARATIVE records with null action in the post-processing step.

#### Problem B: 2 fragment clauses

- L197: `"The Plan shall be effective as of May 16, 2016 (the 'Effective Date')."` — only 71 chars. Too short to train on.
- L263: `"In particular, and without limitation, Reseller shall not"` — **this is a truncated clause**. It got into the dataset because CUAD extracted a partial span.

> [!WARNING]
> **Fix:** Increase minimum clause length from 50 to 80 chars.

#### Problem C: 1 confirmed mislabel

- L100: Contains "shall have the right" — labeled OBLIGATION but should be PERMISSION. The model's reasoning contradicts its own label.

#### Problem D: 11 actions over 40 words

Fix2 truncation runs at the END of generation when all 1500 records are done. These will be handled. Not a current issue.

---

## 3. Class Imbalance

```
OBLIGATION  :   50 ( 18.2%)
PROHIBITION :   47 ( 17.2%)
PERMISSION  :   47 ( 17.2%)
DECLARATIVE :  130 ( 47.4%)
```

> [!NOTE]  
> The dynamic balancer is working well — PROHIBITION and PERMISSION are nearly equal, and OBLIGATION is close. DECLARATIVE is intentionally higher because many clauses genuinely are declarative (representations, warranties, boilerplate).
>
> The training script uses `WeightedLossTrainer` with `compute_class_weight("balanced")`, which will automatically upweight the minority classes. So 47% DECLARATIVE is fine.

---

## 4. Speed Analysis

### Current Throughput: ~60 records/hour

**Where time is spent per successful record:**
- API call latency: ~15-25 seconds (Gemma 4 31B thinking time)
- `time.sleep(2)`: 2 seconds (your rate-limit guard)  
- BIO tag generation: <0.1 seconds
- **Total: ~20-27 seconds per success**

**Where time is WASTED:**
- MAX_TOKENS failures: ~60 seconds wasted per failure (attempt 1: ~30s + sleep 2s + attempt 2: ~30s)
- With 25 failures in 274 records (9.1% failure rate), that's ~25 minutes wasted

### Bottleneck: It's NOT the rate limit or sleep — it's the API latency

The model takes 15-25 seconds to THINK about each clause. `time.sleep(2)` only adds 2 seconds — removing it entirely would only speed things up by ~8%. The real bottleneck is Gemma 4 31B inference time.

### How to Actually Speed It Up

The only way to meaningfully increase throughput without changing the prompt is **parallel requests** — fire multiple API calls simultaneously instead of waiting for each to complete before starting the next.

---

## 5. PDF Fix Details

The glob must be made recursive to find PDFs in subfolders:
```diff
- pdf_files = list(pdf_dir.glob("*.pdf")) + list(pdf_dir.glob("*.PDF"))
+ pdf_files = list(pdf_dir.rglob("*.pdf")) + list(pdf_dir.rglob("*.PDF"))
```
