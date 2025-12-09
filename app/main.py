from fastapi import FastAPI, UploadFile, File
from app.db import ensure_tables, get_connection
from app.ingestion.document_loader import save_upload
from app.ingestion.text_extractor import extract_text
from app.ingestion.text_cleaner import clean_text
from app.ingestion.segmenter import segment_text
from app.analysis.extractor import ContractExtractor
from app.analysis.temporal import TemporalNormalizer
app = FastAPI(title="LexFlow Engine")

@app.on_event("startup")
def startup():
    ensure_tables()
    


@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    # 1. Save
    file_path, file_type = save_upload(file.file, file.filename)
    
    # 2. Extract
    raw = extract_text(file_path)
    
    # 3. Clean
    clean = clean_text(raw)
    
    # 4. Segment (Legal Logic)
    clauses = segment_text(clean)
    
    # 5. Store
    conn = get_connection()
    doc_id = None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (filename, file_type) VALUES (%s, %s) RETURNING id",
                    (file.filename, file_type)
                )
                doc_id = cur.fetchone()[0]
                
                cur.execute(
                    "INSERT INTO raw_text (document_id, full_text) VALUES (%s, %s)",
                    (doc_id, clean)
                )
                
                for idx, c in enumerate(clauses):
                    cur.execute(
                        "INSERT INTO clauses (document_id, header_text, body_text, clause_index) VALUES (%s, %s, %s, %s)",
                        (doc_id, c['header'], c['text'], idx)
                    )
    finally:
        conn.close()

    return {
        "status": "success", 
        "doc_id": doc_id, 
        "clauses_found": len(clauses),
        "preview": clauses[:25]
    }

# Initialize NLP engine
extractor = ContractExtractor()
temporal_engine = TemporalNormalizer()

# @app.post("/analyze/{doc_id}")
# async def analyze_document(doc_id: int):
#     conn = get_connection()
#     results = []

#     try:
#         with conn:
#             with conn.cursor() as cur:
#                 # 1. Fetch clauses for this doc
#                 cur.execute(
#                     "SELECT id, header_text, body_text FROM clauses WHERE document_id = %s ORDER BY clause_index",
#                     (doc_id,)
#                 )
#                 rows = cur.fetchall()

#                 if not rows:
#                     return {"status": "error", "message": "No clauses found for this Doc ID"}

#                 # 2. Run AI Analysis ONCE per clause
#                 for row in rows:
#                     c_id, header, body = row

#                     analysis = extractor.analyze_clause(header, body)

#                     # ✅ Clean Absolute Dates Only
#                     clean_dates = []
#                     for date_text in analysis["entities"]["dates"]:
#                         abs_date = temporal_engine.normalize_date(date_text)
#                         if abs_date:
#                             clean_dates.append(abs_date)

#                     # ✅ FINAL JSON OUTPUT (ONE PER CLAUSE)
#                     results.append({
#                         "clause_id": c_id,
#                         "header": header,
#                         "intent": analysis["intent"],
#                         "extracted_data": analysis["entities"],
#                         "structured_dates": clean_dates,
#                         "structured_durations": analysis["structured_durations"]
#                     })

#     finally:
#         conn.close()

#     return {
#         "doc_id": doc_id,
#         "analysis_count": len(results),
#         "results": results
#     }
import json # Add this import at top

# ... (keep previous imports)

@app.post("/analyze/{doc_id}")
async def analyze_document(doc_id: int):
    conn = get_connection()
    results = []
    
    try:
        with conn:
            with conn.cursor() as cur:
                # 1. Clear previous analysis for this doc (Idempotency)
                # This ensures if you run analyze twice, you don't get duplicate rows
                cur.execute("""
                    DELETE FROM analysis_results 
                    WHERE clause_id IN (SELECT id FROM clauses WHERE document_id = %s)
                """, (doc_id,))

                # 2. Fetch clauses
                cur.execute(
                    "SELECT id, header_text, body_text FROM clauses WHERE document_id = %s ORDER BY clause_index",
                    (doc_id,)
                )
                rows = cur.fetchall()
                
                if not rows:
                    return {"status": "error", "message": "No clauses found for this Doc ID"}

                print(f"Analyzing {len(rows)} clauses for Document {doc_id}...")

                # 3. Run AI + Save to DB
                for row in rows:
                    c_id, header, body = row
                    
                    # A. Run Extraction
                    analysis = extractor.analyze_clause(header, body)
                    
                    # B. Prepare Data
                    intent = analysis["intent"]
                    entities = json.dumps(analysis["entities"]) # Convert dict to JSON string
                    durations = analysis["structured_durations"]
                    
                    # C. Insert into DB
                    cur.execute("""
                        INSERT INTO analysis_results 
                        (clause_id, intent, extracted_entities, structured_durations)
                        VALUES (%s, %s, %s, %s)
                    """, (c_id, intent, entities, durations))
                    
                    # D. Add to response list (for UI)
                    results.append({
                        "clause_id": c_id,
                        "header": header,
                        "intent": intent,
                        "extracted_data": analysis["entities"],
                        "structured_durations": analysis["structured_durations"]
                    })
                    
    finally:
        conn.close()

    return {
        "doc_id": doc_id,
        "status": "saved_to_db",
        "analysis_count": len(results),
        "results": results
    }