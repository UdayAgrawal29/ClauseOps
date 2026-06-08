"""
Verify the 3 classification fixes on the Euromedia contract.
This contract had 3 RENEWAL predictions:
  - "AVAILABILITY DATE" -> RENEWAL 97.3% -- WRONG (availability window)
  - "4. LICENSE PERIOD" -> RENEWAL 98.8% -- WRONG (license duration)
  - "7. TERM" -> RENEWAL 99.7% -- CORRECT ("shall renew for a subsequent term")
After fixes, the first two should be downgraded to needs_review,
and the third should remain HIGH.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from pathlib import Path

PDF = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf\Part_I\License_Agreements\EuromediaHoldingsCorp_20070215_10SB12G_EX-10.B(01)_525118_EX-10.B(01)_Content License Agreement.pdf")

def main():
    if not is_model_available():
        print("Model not available")
        return
    
    print("Segmenting...")
    clauses = segment_contract(str(PDF))
    
    print("Classifying...")
    results = classify_clauses(clauses)
    
    print("\n" + "="*80)
    print("VERIFICATION RESULTS")
    print("="*80)
    
    # Show all segments with focus on RENEWAL and SIGNATURE_BLOCK
    for chunk, result in zip(clauses, results):
        if chunk.chunk_type != "CLAUSE":
            continue
        
        pred = result.get("clause_type", "?")
        conf = result.get("confidence", 0)
        review = result.get("needs_review", False)
        source = result.get("source", "?")
        heading = chunk.heading or "(No heading)"
        
        # Highlight RENEWAL, PREAMBLE, SIGNATURE_BLOCK, and needs_review
        flag = ""
        if pred == "RENEWAL":
            flag = " *** RENEWAL ***"
        elif pred == "SIGNATURE_BLOCK":
            flag = " *** FILTERED:SIG ***"
        elif pred == "PREAMBLE":
            flag = " *** FILTERED:PREAMBLE ***"
        elif review:
            flag = " [REVIEW]"
        
        status = "REVIEW" if review else "HIGH" if source != "filtered" else "FILTERED"
        body_preview = (chunk.body_text or "")[:120].replace('\n', ' ')
        
        print(f"\n{heading}")
        print(f"  -> {pred} ({conf*100:.1f}%) [{status}] {source}{flag}")
        print(f"  Body: {body_preview}...")
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    filtered = sum(1 for r in results if r.get("source") == "filtered")
    classified = sum(1 for r in results if r.get("source") not in ("filtered", "pre_labeled"))
    high = sum(1 for r in results if r.get("source") not in ("filtered", "pre_labeled") and not r.get("needs_review"))
    review = sum(1 for r in results if r.get("source") not in ("filtered", "pre_labeled") and r.get("needs_review"))
    
    renewal_all = [(c, r) for c, r in zip(clauses, results) if r.get("clause_type") == "RENEWAL"]
    renewal_high = [x for x in renewal_all if not x[1].get("needs_review")]
    renewal_review = [x for x in renewal_all if x[1].get("needs_review")]
    
    print(f"Total segments: {len(clauses)}")
    print(f"Filtered: {filtered}")
    print(f"Classified: {classified} (High: {high}, Review: {review})")
    print(f"\nRENEWAL predictions: {len(renewal_all)}")
    print(f"  HIGH (genuine): {len(renewal_high)}")
    for c, r in renewal_high:
        print(f"    - {c.heading}: {r['confidence']*100:.1f}%")
    print(f"  REVIEW (downgraded): {len(renewal_review)}")
    for c, r in renewal_review:
        print(f"    - {c.heading}: {r['confidence']*100:.1f}%")

if __name__ == "__main__":
    main()
