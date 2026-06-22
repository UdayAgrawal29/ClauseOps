import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available
from clauseops.obligation_detection.deontic_classifier import classify_contract_obligations
from clauseops.obligation_detection.bert_classifier import is_bert_available

def _truncate(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    flat = text.replace("\n", " ").strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "... [truncated]"

def _collect_pdfs(root: Path) -> list[Path]:
    return sorted([p for p in root.rglob("*.pdf") if p.is_file()])

def build_report(input_root: Path, output_path: Path):
    pdfs = _collect_pdfs(input_root)[:5]
    clf_ok = is_model_available()
    ner_ok = is_ner_available()
    bert_ok = is_bert_available()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# ClauseOps v4 Full Pipeline Report\n\n")
        f.write(f"> **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> **Input:** `{input_root}`\n")
        f.write(f"> **Classification:** {clf_ok} | **NER:** {ner_ok} | **BERT Obligation:** {bert_ok}\n\n")
        f.write("---\n\n")

        for pdf in pdfs:
            f.write(f"## 📄 Document: {pdf.name}\n\n")
            t0 = time.time()
            
            try:
                # 1. Segmentation
                t_seg = time.time()
                clauses = segment_contract(str(pdf))
                t_seg = time.time() - t_seg
                
                # 2. Classification
                t_class = time.time()
                classification_results = classify_clauses(clauses) if clf_ok else []
                t_class = time.time() - t_class
                
                # 3. NER
                t_ner = time.time()
                entity_results = extract_entities_from_contract(clauses) if ner_ok else []
                t_ner = time.time() - t_ner
                
                # 4. Obligation Detection (v4 BERT)
                t_ob = time.time()
                clauses_data = []
                for i, clause in enumerate(clauses):
                    c_type = classification_results[i].get("clause_type", "") if classification_results and i < len(classification_results) else ""
                    e_sum = entity_results[i].get("entity_summary", {}) if entity_results and i < len(entity_results) else {}
                    rels = entity_results[i].get("relations", []) if entity_results and i < len(entity_results) else []
                    
                    clauses_data.append({
                        "clause_id": f"clause_{i}",
                        "body_text": clause.body_text or "",
                        "clause_type": c_type,
                        "entity_summary": e_sum,
                        "relations": rels
                    })
                
                obligation_results = classify_contract_obligations(clauses_data) if bert_ok else []
                t_ob = time.time() - t_ob
                
                total_time = time.time() - t0
                
                f.write(f"**Processing Time:** {total_time:.1f}s (Seg: {t_seg:.1f}s | Cls: {t_class:.1f}s | NER: {t_ner:.1f}s | Obli: {t_ob:.1f}s)\n\n")
                
                f.write("| Metric | Value |\n|---|---|\n")
                f.write(f"| Total Segments | {len(clauses)} |\n")
                f.write(f"| Clauses | {sum(1 for c in clauses if c.chunk_type == 'CLAUSE')} |\n\n")
                
                f.write("### Segmented Clauses with Obligations\n\n")
                
                for i, clause in enumerate(clauses):
                    if clause.chunk_type != "CLAUSE":
                        continue
                        
                    f.write(f"#### 📋 Segment {i + 1}: {clause.heading or 'Unnamed Clause'}\n\n")
                    f.write("| Property | Value |\n|---|---|\n")
                    f.write(f"| Type | `{clause.chunk_type}` |\n")
                    f.write(f"| Page(s) | p.{clause.start_page + 1}-{clause.end_page + 1} |\n")
                    f.write(f"| Tokens | {clause.token_count} |\n")
                    
                    if classification_results and i < len(classification_results):
                        c = classification_results[i]
                        f.write(f"| Class | `{c.get('clause_type')}` |\n")
                        
                    f.write("\n**Body Text:**\n\n")
                    f.write(f"> {_truncate(clause.body_text)}\n\n")
                    
                    # Print obligations if found
                    if obligation_results and i < len(obligation_results) and obligation_results[i]:
                        f.write("**Extracted Obligations (v4 Custom BERT):**\n\n")
                        for idx, obs in enumerate(obligation_results[i]):
                            f.write(f"- **Obligation {idx+1}:**\n")
                            f.write(f"  - **Modality:** `{obs.obligation_type}` (Conf: {obs.confidence:.2f})\n")
                            f.write(f"  - **Agent:** {obs.obligated_party}\n")
                            f.write(f"  - **Action Verb:** {obs.action_verb}\n")
                            if obs.beneficiary:
                                f.write(f"  - **Beneficiary:** {obs.beneficiary}\n")
                            if obs.financial_params:
                                f.write(f"  - **Financial Params:** {', '.join(obs.financial_params)}\n")
                        f.write("\n")
                    
                    f.write("---\n\n")

            except Exception as e:
                f.write(f"**ERROR:** Failed to process document. {str(e)}\n\n---\n\n")

    print(f"Report written to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("TEST_PDFS"))
    parser.add_argument("--output", type=Path, default=Path("clauseops/obligation_detection/DOCX/5_PIPELINE_OUTPUTS_V4.md"))
    args = parser.parse_args()
    build_report(args.input, args.output)
