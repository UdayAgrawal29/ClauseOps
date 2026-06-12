"""
ClauseOps — Visual Segmentation Viewer

A beautiful web UI to upload PDF contracts and visualize the segmentation results.
Serves as both a testing/debugging tool AND the foundation for the full ClauseOps app.

Usage:
    python -m clauseops.app
    # Then open http://localhost:8000 in your browser

Tech: FastAPI backend + embedded HTML/CSS/JS frontend (no build step needed)
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clauseops.segmentation import segment_contract
from clauseops.clause_classification import classify_clauses, is_model_available
from clauseops.entity_extraction import extract_entities_from_contract, is_ner_available

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="ClauseOps", description="AI-Powered Legal Contract Intelligence")

# Temp directory for uploaded PDFs
UPLOAD_DIR = Path(tempfile.gettempdir()) / "clauseops_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Thread pool for running sync segmentation in background
_executor = ThreadPoolExecutor(max_workers=2)

# In-memory progress store: task_id -> list of {"stage": str, "detail": str, "pct": int}
_progress: dict[str, list[dict]] = {}
_results: dict[str, dict] = {}


# =============================================================================
# Helper: build JSON response from clauses
# =============================================================================
def _build_response(clauses, classification_results, filename: str, entity_results=None) -> dict:
    """Convert ClauseChunk list to JSON-serializable response dict."""
    results = []
    for i, c in enumerate(clauses):
        item = {
            "index": i + 1,
            "clause_id": c.clause_id,
            "heading": c.heading or "(No heading)",
            "heading_number": c.heading_number,
            "body_text": c.body_text,
            "level": c.level,
            "start_page": c.start_page + 1,
            "end_page": c.end_page + 1,
            "token_count": c.token_count,
            "is_oversized": c.is_oversized,
            "chunk_type": c.chunk_type,
            "sub_chunk_count": len(c.sub_chunks) if c.sub_chunks else 0,
        }
        if classification_results and i < len(classification_results):
            item["classification"] = classification_results[i]
        if entity_results and i < len(entity_results):
            entity_data = entity_results[i]
            item["entities"] = entity_data.get("entities", [])
            item["entity_summary"] = entity_data.get("entity_summary", {})
            if entity_data.get("definition_entities"):
                item["definition_entities"] = entity_data["definition_entities"]
        if c.chunk_type == "TABLE":
            item["table_markdown"] = c.table_markdown
        elif c.chunk_type == "DEFINITION_GROUP":
            item["definitions"] = [
                {"term": d.term, "definition": d.definition, "token_count": d.token_count}
                for d in c.definitions
            ]
        results.append(item)

    clause_count = sum(1 for c in clauses if c.chunk_type == "CLAUSE")
    table_count = sum(1 for c in clauses if c.chunk_type == "TABLE")
    def_count = sum(1 for c in clauses if c.chunk_type == "DEFINITION_GROUP")
    token_counts = [c.token_count for c in clauses]
    body_lengths = [len(c.body_text.split()) for c in clauses if c.chunk_type == "CLAUSE"]

    stats = {
        "filename": filename,
        "total_chunks": len(clauses),
        "clause_count": clause_count,
        "table_count": table_count,
        "definition_group_count": def_count,
        "oversized_count": sum(1 for c in clauses if c.is_oversized),
        "avg_tokens": round(sum(token_counts) / len(token_counts), 1) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
        "min_tokens": min(token_counts) if token_counts else 0,
        "avg_words": round(sum(body_lengths) / len(body_lengths), 1) if body_lengths else 0,
    }

    return {"status": "success", "stats": stats, "clauses": results}


# =============================================================================
# Helper: run segmentation in thread with progress updates
# =============================================================================
def _run_segmentation(task_id: str, pdf_path: str, filename: str):
    """Run segmentation synchronously, pushing progress updates to _progress store."""
    try:
        _progress[task_id].append({"stage": "model", "detail": "Loading ML model...", "pct": 10})
        # The first call to segment_contract will init the Docling converter (cached after)
        _progress[task_id].append({"stage": "convert", "detail": "Analyzing document layout with AI...", "pct": 25})

        clauses = segment_contract(pdf_path)

        classification_results = None
        if is_model_available():
            _progress[task_id].append({"stage": "classify", "detail": "Classifying clauses with ML...", "pct": 70})
            classification_results = classify_clauses(clauses)

        entity_results = None
        if is_ner_available():
            _progress[task_id].append({"stage": "ner", "detail": "Extracting entities with NER...", "pct": 80})
            entity_results = extract_entities_from_contract(clauses)

        _progress[task_id].append({"stage": "assemble", "detail": f"Assembled {len(clauses)} segments", "pct": 85})
        _progress[task_id].append({"stage": "build", "detail": "Building results...", "pct": 95})

        result = _build_response(clauses, classification_results, filename, entity_results)
        _results[task_id] = result
        _progress[task_id].append({"stage": "done", "detail": "Complete!", "pct": 100})

    except Exception as e:
        logger.exception("Segmentation failed for task %s", task_id)
        _results[task_id] = {"status": "error", "detail": str(e)}
        _progress[task_id].append({"stage": "error", "detail": str(e), "pct": -1})
    finally:
        # Cleanup PDF
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except Exception:
            pass


# =============================================================================
# API Endpoints
# =============================================================================

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF and start async segmentation. Returns a task_id for progress tracking."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    task_id = str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{task_id}_{file.filename}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # Initialize progress tracking
    _progress[task_id] = [{"stage": "upload", "detail": "PDF uploaded, starting analysis...", "pct": 5}]
    _results[task_id] = None

    # Run segmentation in background thread (so we don't block the event loop)
    _executor.submit(_run_segmentation, task_id, str(save_path), file.filename)

    return JSONResponse({"task_id": task_id})


@app.get("/api/result/{task_id}")
async def get_result(task_id: str):
    """Get segmentation results for a completed task."""
    if task_id not in _results:
        raise HTTPException(status_code=404, detail="Task not found")
    result = _results[task_id]
    if result is None:
        return JSONResponse({"status": "processing"}, status_code=202)
    # Clean up stored data
    _progress.pop(task_id, None)
    _results.pop(task_id, None)
    return JSONResponse(result)


@app.websocket("/ws/progress/{task_id}")
async def ws_progress(websocket: WebSocket, task_id: str):
    """WebSocket endpoint that streams progress updates for a segmentation task."""
    await websocket.accept()
    last_idx = 0
    try:
        while True:
            # Check for new progress messages
            msgs = _progress.get(task_id, [])
            if last_idx < len(msgs):
                for msg in msgs[last_idx:]:
                    await websocket.send_json(msg)
                last_idx = len(msgs)

                # If done or error, close
                if msgs and msgs[-1]["stage"] in ("done", "error"):
                    break

            await asyncio.sleep(0.3)  # Poll every 300ms

    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# Keep the old endpoint for backward compatibility (synchronous version)
@app.post("/api/segment")
async def segment_pdf(file: UploadFile = File(...)):
    """Upload a PDF and get segmentation results (synchronous, no progress)."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_id = str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{file_id}_{file.filename}"

    try:
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        loop = asyncio.get_event_loop()
        clauses = await loop.run_in_executor(_executor, segment_contract, str(save_path))
        
        classification_results = None
        if is_model_available():
            classification_results = await loop.run_in_executor(_executor, classify_clauses, clauses)

        entity_results = None
        if is_ner_available():
            entity_results = await loop.run_in_executor(_executor, extract_entities_from_contract, clauses)
            
        return JSONResponse(_build_response(clauses, classification_results, file.filename, entity_results))

    except Exception as e:
        logger.exception("Segmentation failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if save_path.exists():
            save_path.unlink()


# =============================================================================
# Frontend (embedded HTML)
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return FRONTEND_HTML


FRONTEND_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ClauseOps — Clause Segmentation Viewer</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0a0f;
            --bg-secondary: #12121a;
            --bg-card: #1a1a28;
            --bg-card-hover: #22223a;
            --border-color: #2a2a42;
            --border-glow: rgba(99, 102, 241, 0.3);
            --text-primary: #e8e8f0;
            --text-secondary: #9898b0;
            --text-muted: #6868a0;
            --accent-indigo: #6366f1;
            --accent-indigo-dim: rgba(99, 102, 241, 0.15);
            --accent-violet: #8b5cf6;
            --accent-emerald: #10b981;
            --accent-emerald-dim: rgba(16, 185, 129, 0.12);
            --accent-amber: #f59e0b;
            --accent-amber-dim: rgba(245, 158, 11, 0.12);
            --accent-rose: #f43f5e;
            --accent-rose-dim: rgba(244, 63, 94, 0.12);
            --accent-cyan: #06b6d4;
            --accent-cyan-dim: rgba(6, 182, 212, 0.12);
            --accent-blue: #3b82f6;
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --radius-xl: 20px;
            --shadow-lg: 0 20px 60px rgba(0,0,0,0.4);
            --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* Animated gradient background */
        body::before {
            content: '';
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background:
                radial-gradient(ellipse at 20% 20%, rgba(99, 102, 241, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 80%, rgba(139, 92, 246, 0.06) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 50%, rgba(6, 182, 212, 0.04) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 24px;
            position: relative;
            z-index: 1;
        }

        /* ============= HEADER ============= */
        header {
            padding: 40px 0 20px;
            text-align: center;
        }

        .logo {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 8px;
        }

        .logo-icon {
            width: 48px; height: 48px;
            background: linear-gradient(135deg, var(--accent-indigo), var(--accent-violet));
            border-radius: var(--radius-md);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            font-weight: 800;
            color: white;
            box-shadow: 0 0 30px rgba(99, 102, 241, 0.3);
        }

        .logo-text {
            font-size: 32px;
            font-weight: 800;
            background: linear-gradient(135deg, var(--text-primary), var(--accent-indigo));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.5px;
        }

        .subtitle {
            color: var(--text-secondary);
            font-size: 15px;
            font-weight: 400;
            margin-top: 4px;
        }

        /* ============= UPLOAD ZONE ============= */
        .upload-zone {
            border: 2px dashed var(--border-color);
            border-radius: var(--radius-xl);
            padding: 48px 32px;
            text-align: center;
            cursor: pointer;
            transition: var(--transition);
            background: var(--bg-secondary);
            position: relative;
            overflow: hidden;
            margin: 24px 0;
        }

        .upload-zone:hover, .upload-zone.dragover {
            border-color: var(--accent-indigo);
            background: var(--accent-indigo-dim);
            box-shadow: 0 0 40px rgba(99, 102, 241, 0.1);
            transform: translateY(-2px);
        }

        .upload-zone.processing {
            border-color: var(--accent-amber);
            background: var(--accent-amber-dim);
            pointer-events: none;
        }

        .upload-icon {
            font-size: 48px;
            margin-bottom: 16px;
            display: block;
        }

        .upload-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 6px;
        }

        .upload-subtitle {
            color: var(--text-muted);
            font-size: 14px;
        }

        #file-input { display: none; }

        /* ============= LOADING SPINNER ============= */
        .spinner {
            display: none;
            margin: 32px auto;
            text-align: center;
        }

        .spinner.active { display: block; }

        .spinner-ring {
            width: 48px; height: 48px;
            border: 3px solid var(--border-color);
            border-top: 3px solid var(--accent-indigo);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 0 auto 12px;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        .spinner-text {
            color: var(--text-secondary);
            font-size: 14px;
            font-weight: 500;
        }

        /* ============= STATS BAR ============= */
        .stats-bar {
            display: none;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin: 24px 0;
            animation: fadeInUp 0.5s ease;
        }

        .stats-bar.active { display: grid; }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 16px;
            text-align: center;
            transition: var(--transition);
        }

        .stat-card:hover {
            border-color: var(--accent-indigo);
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }

        .stat-value {
            font-size: 28px;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-indigo), var(--accent-violet));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .stat-label {
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 4px;
        }

        /* ============= FILTER TABS ============= */
        .filter-bar {
            display: none;
            gap: 8px;
            margin: 16px 0 24px;
            flex-wrap: wrap;
            animation: fadeInUp 0.5s ease 0.1s both;
        }

        .filter-bar.active { display: flex; }

        .filter-btn {
            padding: 8px 18px;
            border: 1px solid var(--border-color);
            border-radius: 100px;
            background: var(--bg-secondary);
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: var(--transition);
            font-family: inherit;
        }

        .filter-btn:hover { border-color: var(--accent-indigo); color: var(--text-primary); }

        .filter-btn.active {
            background: var(--accent-indigo);
            border-color: var(--accent-indigo);
            color: white;
            box-shadow: 0 0 20px rgba(99, 102, 241, 0.3);
        }

        /* ============= CLAUSE CARDS ============= */
        .clauses-list {
            display: none;
            flex-direction: column;
            gap: 16px;
            padding-bottom: 80px;
        }

        .clauses-list.active { display: flex; }

        .clause-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            overflow: hidden;
            transition: var(--transition);
            animation: fadeInUp 0.4s ease both;
        }

        .clause-card:hover {
            border-color: var(--border-glow);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3);
            transform: translateY(-2px);
        }

        .clause-header {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 16px 20px;
            cursor: pointer;
            user-select: none;
        }

        .clause-index {
            width: 36px; height: 36px;
            border-radius: var(--radius-sm);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 700;
            flex-shrink: 0;
        }

        .clause-card[data-type="CLAUSE"] .clause-index {
            background: var(--accent-indigo-dim);
            color: var(--accent-indigo);
        }
        .clause-card[data-type="TABLE"] .clause-index {
            background: var(--accent-emerald-dim);
            color: var(--accent-emerald);
        }
        .clause-card[data-type="DEFINITION_GROUP"] .clause-index {
            background: var(--accent-amber-dim);
            color: var(--accent-amber);
        }

        .clause-heading {
            flex: 1;
            font-size: 15px;
            font-weight: 600;
        }

        .clause-meta {
            display: flex;
            gap: 8px;
            flex-shrink: 0;
        }

        .badge {
            padding: 4px 10px;
            border-radius: 100px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }

        .badge-type { background: var(--accent-indigo-dim); color: var(--accent-indigo); }
        .badge-table { background: var(--accent-emerald-dim); color: var(--accent-emerald); }
        .badge-def { background: var(--accent-violet-dim); color: var(--accent-violet); }
        .badge-page { background: var(--bg-card); color: var(--text-muted); border: 1px solid var(--border-color); }
        .badge-tokens { background: var(--bg-card); color: var(--text-muted); border: 1px solid var(--border-color); }
        .badge-oversized { background: var(--accent-rose-dim); color: var(--accent-rose); border: 1px solid rgba(244, 63, 94, 0.3); }
        .badge-classification { background: var(--accent-blue-dim); color: #fff; background-color: #2563eb; padding: 4px 10px; font-weight: 600; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
        .badge-classification.review { background-color: var(--accent-amber); color: #fff; }

        .expand-arrow {
            color: var(--text-muted);
            font-size: 12px;
            transition: transform 0.3s ease;
        }

        .clause-card.expanded .expand-arrow { transform: rotate(180deg); }

        .clause-body {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .clause-card.expanded .clause-body { max-height: 2000px; }

        .clause-body-inner {
            padding: 0 20px 20px;
            border-top: 1px solid var(--border-color);
        }

        .clause-text {
            font-size: 14px;
            line-height: 1.7;
            color: var(--text-secondary);
            white-space: pre-wrap;
            word-break: break-word;
            margin-top: 16px;
            padding: 16px;
            background: var(--bg-primary);
            border-radius: var(--radius-sm);
            border: 1px solid var(--border-color);
            max-height: 400px;
            overflow-y: auto;
        }

        .clause-text::-webkit-scrollbar { width: 6px; }
        .clause-text::-webkit-scrollbar-track { background: transparent; }
        .clause-text::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 3px; }

        /* Table markdown rendering */
        .clause-table {
            margin-top: 16px;
            overflow-x: auto;
        }

        .clause-table table {
            border-collapse: collapse;
            width: 100%;
            font-size: 13px;
        }

        .clause-table th, .clause-table td {
            padding: 10px 14px;
            border: 1px solid var(--border-color);
            text-align: left;
        }

        .clause-table th {
            background: var(--accent-emerald-dim);
            color: var(--accent-emerald);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }

        .clause-table td {
            color: var(--text-secondary);
            background: var(--bg-primary);
        }

        .clause-table tr:hover td {
            background: var(--bg-secondary);
        }

        /* Definition items */
        .def-item {
            padding: 12px 16px;
            margin-top: 8px;
            background: var(--bg-primary);
            border-radius: var(--radius-sm);
            border-left: 3px solid var(--accent-amber);
        }

        .def-term {
            font-weight: 600;
            color: var(--accent-amber);
            font-size: 14px;
            margin-bottom: 4px;
        }

        .def-text {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.6;
        }

        /* Entity summary */
        .entity-summary {
            margin-top: 12px;
            padding: 12px;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            background: var(--bg-primary);
        }

        .entity-group {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
            margin-top: 8px;
        }

        .entity-label {
            font-size: 11px;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.4px;
            min-width: 110px;
        }

        .entity-pill {
            padding: 4px 10px;
            border-radius: 100px;
            font-size: 11px;
            font-weight: 600;
            background: var(--accent-cyan-dim);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.25);
        }

        /* Sub-chunks indicator */
        .sub-chunks-info {
            margin-top: 12px;
            padding: 10px 14px;
            background: var(--accent-rose-dim);
            border-radius: var(--radius-sm);
            border: 1px solid rgba(244, 63, 94, 0.2);
            font-size: 13px;
            color: var(--accent-rose);
        }

        /* ============= ANIMATIONS ============= */
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* ============= FILENAME DISPLAY ============= */
        .filename-bar {
            display: none;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 12px 20px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            margin-bottom: 16px;
            animation: fadeInUp 0.4s ease;
        }

        .filename-bar.active { display: flex; }

        .filename-icon { font-size: 20px; }

        .filename-text {
            font-weight: 600;
            color: var(--text-primary);
        }

        .filename-reset {
            margin-left: auto;
            padding: 6px 14px;
            border: 1px solid var(--border-color);
            border-radius: 100px;
            background: none;
            color: var(--text-secondary);
            font-size: 12px;
            cursor: pointer;
            font-family: inherit;
            transition: var(--transition);
        }

        .filename-reset:hover {
            border-color: var(--accent-rose);
            color: var(--accent-rose);
        }

        /* ============= RESPONSIVE ============= */
        @media (max-width: 768px) {
            .clause-meta { flex-wrap: wrap; }
            .stats-bar { grid-template-columns: repeat(2, 1fr); }
            .clause-header { flex-wrap: wrap; }
        }
    </style>
</head>
<body>

<div class="container">
    <!-- Header -->
    <header>
        <div class="logo">
            <div class="logo-icon">C</div>
            <span class="logo-text">ClauseOps</span>
        </div>
        <p class="subtitle">AI-Powered Clause Segmentation Viewer</p>
    </header>

    <!-- Upload Zone -->
    <div class="upload-zone" id="upload-zone" onclick="document.getElementById('file-input').click()">
        <span class="upload-icon">&#128196;</span>
        <div class="upload-title">Drop a PDF contract here, or click to browse</div>
        <div class="upload-subtitle">Supports any PDF — contracts, NDAs, agreements, papers</div>
    </div>
    <input type="file" id="file-input" accept=".pdf">

    <!-- Loading -->
    <div class="spinner" id="spinner">
        <div class="spinner-ring"></div>
        <div class="spinner-text" id="spinner-text">Analyzing document structure...</div>
        <div id="progress-bar-wrap" style="width:280px;margin:14px auto 0;height:6px;background:var(--bg-card);border-radius:3px;overflow:hidden;display:none;">
            <div id="progress-bar" style="width:0%;height:100%;background:linear-gradient(90deg,var(--accent-indigo),var(--accent-violet));border-radius:3px;transition:width 0.5s ease;"></div>
        </div>
    </div>

    <!-- Filename -->
    <div class="filename-bar" id="filename-bar">
        <span class="filename-icon">&#128196;</span>
        <span class="filename-text" id="filename-text"></span>
        <button class="filename-reset" onclick="resetUI()">Upload Another</button>
    </div>

    <!-- Stats -->
    <div class="stats-bar" id="stats-bar"></div>

    <!-- Filter Tabs -->
    <div class="filter-bar" id="filter-bar">
        <button class="filter-btn active" data-filter="ALL" onclick="filterClauses('ALL', this)">All</button>
        <button class="filter-btn" data-filter="CLAUSE" onclick="filterClauses('CLAUSE', this)">Clauses</button>
        <button class="filter-btn" data-filter="TABLE" onclick="filterClauses('TABLE', this)">Tables</button>
        <button class="filter-btn" data-filter="DEFINITION_GROUP" onclick="filterClauses('DEFINITION_GROUP', this)">Definitions</button>
    </div>

    <!-- Clauses List -->
    <div class="clauses-list" id="clauses-list"></div>
</div>

<script>
    // ============= State =============
    let allClauses = [];

    // ============= Upload Handling =============
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');

    // Drag & drop
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('dragover');
    });
    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('dragover');
    });
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFile(files[0]);
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) handleFile(e.target.files[0]);
    });

    async function handleFile(file) {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            alert('Please upload a PDF file');
            return;
        }

        // Show loading state
        uploadZone.style.display = 'none';
        const spinner = document.getElementById('spinner');
        const spinnerText = document.getElementById('spinner-text');
        const progressWrap = document.getElementById('progress-bar-wrap');
        const progressBar = document.getElementById('progress-bar');
        spinner.classList.add('active');
        spinnerText.textContent = 'Uploading PDF...';
        progressWrap.style.display = 'block';
        progressBar.style.width = '0%';

        const formData = new FormData();
        formData.append('file', file);

        try {
            // Step 1: Upload and get task_id
            const uploadRes = await fetch('/api/upload', { method: 'POST', body: formData });
            const uploadData = await uploadRes.json();

            if (!uploadRes.ok) {
                throw new Error(uploadData.detail || 'Upload failed');
            }

            const taskId = uploadData.task_id;

            // Step 2: Connect to WebSocket for progress
            const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/progress/${taskId}`);

            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                spinnerText.textContent = msg.detail || 'Processing...';
                if (msg.pct > 0) {
                    progressBar.style.width = msg.pct + '%';
                }
            };

            // Step 3: Poll for result (WebSocket tells us when done, but we fetch via REST)
            await new Promise((resolve, reject) => {
                ws.onclose = async () => {
                    try {
                        // Small delay to ensure result is stored
                        await new Promise(r => setTimeout(r, 300));
                        const resultRes = await fetch(`/api/result/${taskId}`);
                        const data = await resultRes.json();

                        if (data.status === 'error') {
                            throw new Error(data.detail || 'Segmentation failed');
                        }

                        allClauses = data.clauses;
                        renderStats(data.stats);
                        renderClauses(data.clauses);

                        // Show UI
                        spinner.classList.remove('active');
                        progressWrap.style.display = 'none';
                        document.getElementById('filename-text').textContent = data.stats.filename;
                        document.getElementById('filename-bar').classList.add('active');
                        document.getElementById('stats-bar').classList.add('active');
                        document.getElementById('filter-bar').classList.add('active');
                        document.getElementById('clauses-list').classList.add('active');
                        resolve();
                    } catch (e) {
                        reject(e);
                    }
                };
                ws.onerror = () => reject(new Error('WebSocket connection failed'));
            });

        } catch (err) {
            spinner.classList.remove('active');
            progressWrap.style.display = 'none';
            uploadZone.style.display = '';
            alert('Error: ' + err.message);
        }
    }

    // ============= Render Stats =============
    function renderStats(stats) {
        const bar = document.getElementById('stats-bar');
        bar.innerHTML = `
            <div class="stat-card">
                <div class="stat-value">${stats.total_chunks}</div>
                <div class="stat-label">Total Segments</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${stats.clause_count}</div>
                <div class="stat-label">Clauses</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${stats.table_count}</div>
                <div class="stat-label">Tables</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${stats.definition_group_count}</div>
                <div class="stat-label">Definitions</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${stats.avg_tokens}</div>
                <div class="stat-label">Avg Tokens</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${stats.avg_words}</div>
                <div class="stat-label">Avg Words</div>
            </div>
        `;
    }

    // ============= Render Clauses =============
    function renderClauses(clauses) {
        const list = document.getElementById('clauses-list');
        list.innerHTML = clauses.map((c, i) => {
            const typeBadgeClass = c.chunk_type === 'TABLE' ? 'badge-table'
                : c.chunk_type === 'DEFINITION_GROUP' ? 'badge-def' : 'badge-type';

            let classificationBadge = '';
            if (c.classification && c.chunk_type === 'CLAUSE') {
                const conf = (c.classification.confidence * 100).toFixed(1) + '%';
                const type = c.classification.clause_type;
                const reviewClass = c.classification.needs_review ? 'review' : '';
                classificationBadge = `<span class="badge badge-classification ${reviewClass}">${type} (${conf})</span>`;
            }

            let entityHTML = '';
            if (c.entity_summary && Object.keys(c.entity_summary).length > 0) {
                entityHTML = renderEntitySummary(c.entity_summary);
            }

            let bodyHTML = '';
            if (c.chunk_type === 'TABLE' && c.table_markdown) {
                bodyHTML = entityHTML + `<div class="clause-table">${markdownTableToHTML(c.table_markdown)}</div>`;
            } else if (c.chunk_type === 'DEFINITION_GROUP' && c.definitions) {
                bodyHTML = entityHTML + c.definitions.map(d => `
                    <div class="def-item">
                        <div class="def-term">${escapeHTML(d.term || '(unnamed)')}</div>
                        <div class="def-text">${escapeHTML(d.definition)}</div>
                    </div>
                `).join('');
            } else {
                let alternativesHTML = '';
                if (c.classification && c.classification.needs_review && c.classification.alternatives) {
                    alternativesHTML = `<div class="sub-chunks-info" style="color: var(--accent-amber); margin-bottom: 8px;">&#9888; Low confidence. Alternatives: ` + 
                        c.classification.alternatives.map(alt => `${alt[0]} (${(alt[1]*100).toFixed(1)}%)`).join(', ') + `</div>`;
                }
                bodyHTML = entityHTML + alternativesHTML + `<div class="clause-text">${escapeHTML(c.body_text)}</div>`;
            }

            if (c.is_oversized && c.sub_chunk_count > 0) {
                bodyHTML += `<div class="sub-chunks-info">&#9888; Oversized clause — split into ${c.sub_chunk_count} overlapping sub-chunks for model processing</div>`;
            }

            const pageLabel = c.start_page === c.end_page ? `p.${c.start_page}` : `p.${c.start_page}-${c.end_page}`;

            return `
                <div class="clause-card" data-type="${c.chunk_type}" style="animation-delay: ${i * 0.05}s" onclick="toggleClause(this)">
                    <div class="clause-header">
                        <div class="clause-index">${c.index}</div>
                        <div class="clause-heading">${escapeHTML(c.heading)}</div>
                        <div class="clause-meta">
                            <span class="badge ${typeBadgeClass}">${c.chunk_type.replace('_', ' ')}</span>
                            ${classificationBadge}
                            <span class="badge badge-page">${pageLabel}</span>
                            <span class="badge badge-tokens">${c.token_count} tok</span>
                            ${c.is_oversized ? '<span class="badge badge-oversized">OVERSIZED</span>' : ''}
                        </div>
                        <span class="expand-arrow">&#9660;</span>
                    </div>
                    <div class="clause-body">
                        <div class="clause-body-inner">
                            ${bodyHTML}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ============= Interactions =============
    function toggleClause(card) {
        card.classList.toggle('expanded');
    }

    function filterClauses(type, btn) {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const filtered = type === 'ALL' ? allClauses : allClauses.filter(c => c.chunk_type === type);
        renderClauses(filtered);
    }

    function resetUI() {
        document.getElementById('upload-zone').style.display = '';
        document.getElementById('filename-bar').classList.remove('active');
        document.getElementById('stats-bar').classList.remove('active');
        document.getElementById('filter-bar').classList.remove('active');
        document.getElementById('clauses-list').classList.remove('active');
        document.getElementById('file-input').value = '';
        allClauses = [];
    }

    // ============= Helpers =============
    function escapeHTML(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function markdownTableToHTML(md) {
        if (!md) return '';
        const lines = md.trim().split('\\n').filter(l => l.trim());
        if (lines.length < 2) return `<pre>${escapeHTML(md)}</pre>`;

        let html = '<table>';
        const headers = lines[0].split('|').filter(h => h.trim());
        html += '<thead><tr>' + headers.map(h => `<th>${escapeHTML(h.trim())}</th>`).join('') + '</tr></thead>';
        html += '<tbody>';

        for (let i = 2; i < lines.length; i++) {
            const cells = lines[i].split('|').filter(c => c.trim() !== '' || c.includes(' '));
            if (cells.length === 0) continue;
            // Clean up <br> tags for display
            html += '<tr>' + cells.map(c => {
                let text = c.trim().replace(/<br>/g, ' | ').replace(/_/g, '');
                return `<td>${escapeHTML(text)}</td>`;
            }).join('') + '</tr>';
        }

        html += '</tbody></table>';
        return html;
    }

    function renderEntitySummary(summary) {
        const groups = Object.entries(summary);
        if (!groups.length) return '';

        const groupHtml = groups.map(([label, values]) => {
            const pills = values.slice(0, 6).map(v => `<span class="entity-pill">${escapeHTML(v)}</span>`).join('');
            return `
                <div class="entity-group">
                    <div class="entity-label">${escapeHTML(label)}</div>
                    <div>${pills}</div>
                </div>
            `;
        }).join('');

        return `<div class="entity-summary">${groupHtml}</div>`;
    }
</script>

</body>
</html>
"""


# =============================================================================
# Run
# =============================================================================

if __name__ == "__main__":
    print()
    print("  =============================================")
    print("  ClauseOps -- Clause Segmentation Viewer")
    print("  =============================================")
    print()
    print("  Open your browser at: http://localhost:8000")
    print("  Press Ctrl+C to stop the server")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
