import psycopg2
from app.config import DB
from datetime import datetime, timedelta

def get_connection():
    return psycopg2.connect(**DB)

def generate_tasks():
    conn = get_connection()
    tasks_created = 0
    
    try:
        with conn:
            with conn.cursor() as cur:
                # 1. Find unprocessed analysis results
                cur.execute("""
                    SELECT ar.id, ar.clause_id, ar.intent, ar.extracted_entities, c.document_id 
                    FROM analysis_results ar
                    JOIN clauses c ON ar.clause_id = c.id
                    WHERE ar.processed = FALSE
                """)
                rows = cur.fetchall()
                
                for row in rows:
                    analysis_id, clause_id, intent, entities, doc_id = row
                    
                    # Logic: Map Intent -> Task
                    task_type = "REVIEW"
                    description = f"AI flagged this clause as {intent}. Please review."
                    due_date = None

                    # A. Payment Logic
                    if intent == "PAYMENT_OBLIGATION":
                        task_type = "FINANCE"
                        description = "Verify payment terms and schedule invoice."
                        # Default due date: 30 days from now (MVP logic)
                        due_date = datetime.now() + timedelta(days=30)

                    # B. Termination Logic
                    elif intent == "TERMINATION_LOGIC":
                        task_type = "RISK"
                        description = "Critical: Review termination conditions and cure periods."
                        due_date = datetime.now() + timedelta(days=7)

                    # C. Dispute Logic
                    elif intent == "DISPUTE_RESOLUTION":
                        task_type = "LEGAL"
                        description = "Check arbitration jurisdiction alignment with company policy."

                    # D. Date Logic (If extraction found a date)
                    # entities is a dict (from JSONB). 
                    # Note: Postgres returns JSONB as a dict automatically in psycopg2 with RealDictCursor, 
                    # but here we used standard cursor, so it might be a dict or string depending on setup.
                    # For safety, we assume basic mapping.
                    
                    # 2. Insert Task
                    cur.execute("""
                        INSERT INTO tasks (document_id, clause_id, task_type, description, due_date)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (doc_id, clause_id, task_type, description, due_date))

                    # 3. Mark Analysis as Processed
                    cur.execute("""
                        UPDATE analysis_results SET processed = TRUE WHERE id = %s
                    """, (analysis_id,))
                    
                    tasks_created += 1
                    
        print(f"[Task Engine] Generated {tasks_created} new tasks.")
        
    except Exception as e:
        print(f"[Task Engine] Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    generate_tasks()