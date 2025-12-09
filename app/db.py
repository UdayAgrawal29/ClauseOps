import psycopg2
from app.config import DB
import json

def get_connection():
    return psycopg2.connect(**DB)

def ensure_tables():
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # 1. Documents
                cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_type TEXT,
                    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)

                # 2. Raw Text
                cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_text (
                    id SERIAL PRIMARY KEY,
                    document_id INT REFERENCES documents(id) ON DELETE CASCADE,
                    full_text TEXT
                );
                """)

                # 3. Clauses
                cur.execute("""
                CREATE TABLE IF NOT EXISTS clauses (
                    id SERIAL PRIMARY KEY,
                    document_id INT REFERENCES documents(id) ON DELETE CASCADE,
                    header_text TEXT,
                    body_text TEXT,
                    clause_index INT
                );
                """)
                
                # 4. Analysis Results (NEW)
                # We store entities as JSONB so we can query inside them easily later
                cur.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id SERIAL PRIMARY KEY,
                    clause_id INT REFERENCES clauses(id) ON DELETE CASCADE,
                    intent TEXT,
                    extracted_entities JSONB, 
                    structured_durations TEXT[],
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
                # 5. Tasks (The Output of the Scheduler)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    document_id INT REFERENCES documents(id) ON DELETE CASCADE,
                    clause_id INT REFERENCES clauses(id) ON DELETE CASCADE,
                    task_type TEXT,       -- 'REVIEW', 'DEADLINE', 'RISK'
                    description TEXT,
                    status TEXT DEFAULT 'PENDING', -- 'PENDING', 'COMPLETED'
                    due_date TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
                
                # 6. Add 'processed' flag to analysis_results
                # We need this so we don't generate duplicate tasks for the same clause
                cur.execute("""
                ALTER TABLE analysis_results 
                ADD COLUMN IF NOT EXISTS processed BOOLEAN DEFAULT FALSE;
                """)
    finally:
        conn.close()