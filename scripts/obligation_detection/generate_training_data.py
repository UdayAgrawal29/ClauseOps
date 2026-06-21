"""
ClauseOps — Phase 4 v6.0: Training Data Generation (Groq + Llama 3.3 70B)
==========================================================================
Uses Groq API (free tier) with Llama 3.3 70B to auto-annotate legal clauses
from CUAD and LEDGAR datasets for fine-tuning two offline models:

  Model A: Modality Classifier (OBLIGATION/PROHIBITION/PERMISSION/DECLARATIVE)
  Model B: Agent+Action NER Extractor (BIO token tags)

Usage:
    1. Set your Groq API key:
         set GROQ_API_KEY=your_key_here        (Windows CMD)
         $env:GROQ_API_KEY="your_key_here"     (PowerShell)

    2. Run:
         python scripts/obligation_detection/generate_training_data.py

    3. Output: scripts/obligation_detection/training_data/
         - modality_train.jsonl     (for Model A)
         - modality_val.jsonl
         - modality_test.jsonl
         - ner_train.jsonl          (for Model B)
         - ner_val.jsonl
         - ner_test.jsonl
         - raw_annotations.jsonl    (all LLM outputs, for debugging)
         - generation_report.md     (stats and quality report)

Estimated time: 1-2 hours for 1000 clauses (at ~30 RPM on Groq free tier)
Estimated API calls: ~1000 (free tier: 1000 RPD for Llama 3.3 70B)
"""

from __future__ import annotations

import json
import os
import sys
import time
import random
import re
import logging
import hashlib
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict

from pydantic import BaseModel, Field
from typing import Optional, Literal

class LegalAnnotation(BaseModel):
    modality: Literal["OBLIGATION", "PROHIBITION", "PERMISSION", "DECLARATIVE"]
    agent: Optional[str] = Field(default=None)
    action: Optional[str] = Field(default=None)
    reasoning: str

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("datasets").setLevel(logging.WARNING)
logging.getLogger("filelock").setLevel(logging.WARNING)

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = Path(__file__).parent / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Groq Client Setup (COMMENTED OUT — using Gemma via Google AI instead)
# ---------------------------------------------------------------------------
# def get_groq_client():
#     """Initialize Groq client. Requires GROQ_API_KEY env var."""
#     try:
#         from groq import Groq
#     except ImportError:
#         logger.error("groq not installed. Run: pip install groq")
#         sys.exit(1)

#     api_key = os.environ.get("GROQ_API_KEY")
#     if not api_key:
#         logger.error(
#             "GROQ_API_KEY environment variable not set.\n"
#             "  PowerShell: $env:GROQ_API_KEY='your_key'\n"
#             "  CMD:        set GROQ_API_KEY=your_key\n"
#             "  Get your free key at: https://console.groq.com/keys"
#         )
#         sys.exit(1)

#     return Groq(api_key=api_key)

# ---------------------------------------------------------------------------
# Google Gemma Client Setup
# ---------------------------------------------------------------------------
# GEMMA_MODEL = "gemma-4-26b-a4b-it" 
GEMMA_MODEL = "gemma-4-31b-it"  # Switched from  (broken)
def get_gemma_client():
    """Initialize Google GenAI client. Requires GEMINI_API_KEY env var."""
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error(
            "GEMINI_API_KEY environment variable not set.\n"
            "  PowerShell: $env:GEMINI_API_KEY='your_key'\n"
            "  Get your free key at: https://aistudio.google.com/apikey"
        )
        sys.exit(1)

    return genai.Client(api_key=api_key)

def get_gemma_clients():
    """Initialize one Google GenAI client per API key (round-robin across workers)."""
    try:
        from google import genai
    except ImportError:
        logger.error("google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    keys_raw = os.environ.get("GEMINI_API_KEYS", "")
    if not keys_raw:
        # Fallback: single key from old env var
        single = os.environ.get("GEMINI_API_KEY", "")
        if not single:
            logger.error(
                "No API keys found. Set GEMINI_API_KEYS=key1,key2 (or GEMINI_API_KEY=key1)\n"
                "  PowerShell: $env:GEMINI_API_KEYS='key1,key2'"
            )
            sys.exit(1)
        keys_raw = single

    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    clients = [genai.Client(api_key=k) for k in keys]
    logger.info(f"Initialized {len(clients)} Gemma client(s).")
    return clients

# ---------------------------------------------------------------------------
# Annotation Schema — v6.0 with Few-Shot Examples
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a legal contract clause annotator. You classify clauses and extract spans.

IMPORTANT RULES:
1. MODALITY must be one of: OBLIGATION, PROHIBITION, PERMISSION, DECLARATIVE
2. For "agent" and "action": you MUST copy-paste the EXACT substring from the clause text.
   - Do NOT paraphrase, reword, fix typos, or change verb tense.
   - Do NOT convert passive voice to active voice.
   - The extracted text must appear CHARACTER-FOR-CHARACTER in the original clause.
3a. NOTICE/PROCEDURAL clauses: If the clause specifies HOW something must be done (format, delivery method, timing) but doesn't name WHO must do it, classify as DECLARATIVE. Example: "All notices shall be in writing".
3b. PASSIVE-VOICE OBLIGATIONS: If the clause imposes a real duty but uses passive voice or implied subjects, extract the implied agent from context clues in the clause. Example: "Comply, and cause its Subsidiaries to comply". Agent = "its Subsidiaries" or the implied party if explicitly named in the clause. Only fall back to DECLARATIVE if NO party is named anywhere in the clause.
4. If the clause is DECLARATIVE, set both agent and action to null.
5. If a clause has BOTH a declarative binding effect ("This Agreement shall be binding upon...") AND a prohibition ("provided no party may assign..."), label it as PROHIBITION ONLY IF the prohibition is clearly the PRIMARY duty of the clause. Otherwise, classify as DECLARATIVE.
6. For "action": extract ONLY the core action verb phrase — the thing the agent must do/not do. Do NOT include exceptions, carve-outs, conditions, provisos, or "unless" clauses. Keep the action CONCISE (typically 10-40 words).

CLASSIFICATION DEFINITIONS:
- OBLIGATION: A named party IS REQUIRED to do something. Triggers: "shall", "must", "agrees to", "will" (imposing duty), "covenants to"
- PROHIBITION: A named party is FORBIDDEN. Triggers: "shall not", "must not", "may not", "will not"
- PERMISSION: A named party is ALLOWED but not required. Triggers: "may" (granting right), "is entitled to", "has the right to"
- DECLARATIVE: Statement of fact, definition, boilerplate, recital, survival clause, representation/warranty about current state, limitation of liability, OR a true procedural requirement with no identifiable agent. No action required.

CRITICAL: "agent" must be a HUMAN or CORPORATE ENTITY (e.g., "the Company", "Borrower", "Executive"). NEVER extract inanimate objects like "notices", "this Plan", "each payment", or "Such termination" as agents. If only inanimate objects appear as grammatical subjects, classify as DECLARATIVE.

Respond with valid JSON only: {"modality": "...", "agent": "..." or null, "action": "..." or null, "reasoning": "..."} Keep "reasoning" under 35 words. Be concise."""
FEW_SHOT_EXAMPLES = [
    {
        "role": "user",
        "content": "Classify this clause:\n\"The Company and Executive shall enter into an Indemnification Agreement.\""
    },
    {
        "role": "assistant",
        "content": "{\"modality\": \"OBLIGATION\", \"agent\": \"The Company and Executive\", \"action\": \"enter into an Indemnification Agreement\", \"reasoning\": \"Shall imposes a mandatory duty.\"}"
    },
    {
        "role": "user",
        "content": "Classify this clause:\n\"All representations and warranties shall survive the execution hereof.\""
    },
    {
        "role": "assistant",
        "content": "{\"modality\": \"DECLARATIVE\", \"agent\": null, \"action\": null, \"reasoning\": \"Survival clause, no performance duty on a named party.\"}"
    },
    {
        "role": "user",
        "content": "Classify this clause:\n\"Sublessee may not sublease the Premises without written consent.\""
    },
    {
        "role": "assistant",
        "content": "{\"modality\": \"PROHIBITION\", \"agent\": \"Sublessee\", \"action\": \"sublease the Premises without written consent\", \"reasoning\": \"May not forbids the action.\"}"
    },
    {
        "role": "user",
        "content": "Classify this clause:\n\"This Agreement may be terminated by the Representatives.\""
    },
    {
        "role": "assistant",
        "content": "{\"modality\": \"PERMISSION\", \"agent\": \"the Representatives\", \"action\": \"terminated by the Representatives\", \"reasoning\": \"May grants a right, not a duty.\"}"
    },
]

USER_PROMPT_TEMPLATE = "Classify this clause:\n\"{clause_text}\""




# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------
def load_cuad_clauses(max_clauses: int = 1500) -> list[dict]:
    """Load expert-annotated clauses from local CUAD json."""
    logger.info("Loading CUAD dataset from local JSON...")
    cuad_path = r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\CUAD_v1.json"
    
    if not os.path.exists(cuad_path):
        logger.warning(f"Local CUAD not found at {cuad_path}")
        return []

    try:
        with open(cuad_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Error reading CUAD JSON: {e}")
        return []

    seen = set()
    clauses = []
    
    # Extract only the expert-annotated spans
    for entry in data.get('data', []):
        for para in entry.get('paragraphs', []):
            for qa in para.get('qas', []):
                question = qa.get('question', '')

                match = re.search(r'related to "([^"]+)"', question)
                cat = match.group(1) if match else "contract_clause"
                
                for ans in qa.get('answers', []):
                    text = ans.get('text', '').strip()
                    if not text or len(text) < 50 or len(text) > 2000:
                        continue
                        
                    key = text[:150].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    
                    clauses.append({
                        "text": text,
                        "source": "CUAD_EXPERT",
                        "category": cat,
                    })
                    
                    if len(clauses) >= max_clauses:
                        logger.info(f"Extracted {len(clauses)} unique clauses from CUAD")
                        return clauses

    logger.info(f"Extracted {len(clauses)} unique clauses from CUAD")
    return clauses


# ---------------------------------------------------------------------------
# Modality heuristic: scan clause text to estimate likely modality
# Used to front-load PROHIBITION and PERMISSION clauses in the annotation queue
# ---------------------------------------------------------------------------
def _estimate_modality(text: str) -> str:
    """Quick keyword scan to guess the likely modality of a clause."""
    t = text.lower()
    # PROHIBITION signals (check these first — they're substrings of obligation signals)
    if any(p in t for p in [
        "shall not", "must not", "may not", "will not", "cannot",
        "neither", "no party shall", "not permitted", "prohibited",
        "is not allowed", "shall in no event", "in no case shall",
        "shall be prohibited", "not entitled",
    ]):
        return "PROHIBITION"
    # PERMISSION signals
    if any(p in t for p in [
        " may ", " may,", "is entitled to", "has the right to",
        "is permitted", "at the option of", "in its sole discretion",
        "at its election", "reserves the right", "shall be entitled",
        "shall have the right", "may elect", "is authorized",
    ]):
        return "PERMISSION"
    # OBLIGATION signals
    if any(p in t for p in [
        "shall ", "must ", "agrees to", "covenants to", "will ",
        "is required to", "undertakes to", "hereby agrees",
    ]):
        return "OBLIGATION"
    return "DECLARATIVE"


def load_ledgar_clauses(max_clauses: int = 6000) -> list[dict]:
    """
    Load raw clause texts from LEDGAR (via LexGLUE).
    No balancing done here — we just collect a massive pool.
    """
    logger.info("Loading LEDGAR dataset from HuggingFace...")
    try:
        from datasets import load_dataset
    except ImportError:
        return []

    try:
        ds = load_dataset("coastalcph/lex_glue", "ledgar", split="train")
    except Exception as e:
        logger.error(f"Could not load LEDGAR: {e}")
        return []

    label_names = ds.features['label'].names
    logger.info(f"LEDGAR loaded: {len(ds)} provisions, {len(label_names)} labels")

    seen = set()
    clauses = []
    
    # We want to pull enough candidates from all categories
    # The global balancer in main() will handle the capping
    for example in ds:
        text = example['text'].strip()
        if len(text) < 50 or len(text) > 1500:
            continue
            
        key = text[:100]
        if key in seen:
            continue
        seen.add(key)
        
        cat_name = label_names[example['label']]
        clauses.append({
            "text": text,
            "source": "LEDGAR",
            "category": cat_name,
        })
        
        if len(clauses) >= max_clauses:
            break

    logger.info(f"Extracted {len(clauses)} unique clauses from LEDGAR")
    return clauses


def load_test_pdf_clauses() -> list[dict]:
    """
    Load clauses from actual PDFs using the existing segmenter.
    Why do this instead of just using clean TXT? Because real-world pipeline
    data is messy (OCR artifacts, weird line breaks, headers/footers bleeding in).
    Training the model on this exact noise profile makes it robust in production.
    """
    logger.info("Loading noisy clauses from CUAD PDFs using segment_contract...")
    
    pdf_dir = Path(r"C:\Users\Uday Agrawal\Downloads\CUAD_v1\CUAD_v1\full_contract_pdf\Part_I")
    if not pdf_dir.exists():
        logger.warning(f"PDF directory not found at {pdf_dir}")
        return []

    try:
        from clauseops.segmentation import segment_contract
    except ImportError:
        logger.warning("Segmenter not available. Skipping PDF clauses.")
        return []

    clauses = []
    pdf_files = list(pdf_dir.rglob("*.pdf")) + list(pdf_dir.rglob("*.PDF"))
    logger.info(f"Found {len(pdf_files)} PDFs in {pdf_dir}")

    # Process 5 small PDFs to get ~50-100 noisy clauses without OOM crashes
    random.shuffle(pdf_files)
    small_pdfs = [p for p in pdf_files if p.stat().st_size < 500 * 1024]
    for pdf_path in small_pdfs[:5]:
        try:
            logger.info(f"  Segmenting (for noise simulation): {pdf_path.name}")
            segments = segment_contract(str(pdf_path))
            
            added_from_file = 0
            for seg in segments:
                text = seg.body_text.strip()
                if len(text) < 100 or len(text) > 800:
                    continue
                    
                # Skip things that look like tables/TOCs
                if text.count('.') > 15 or text.count('_') > 5:
                    continue

                clauses.append({
                    "text": text,  # Keep the exact newlines and noise
                    "source": f"PDF_NOISY:{pdf_path.name[:30]}",
                    "category": "contract_clause",
                })
                added_from_file += 1
                if added_from_file >= 150:  # Max 25 noisy clauses per PDF
                    break
        except (Exception, KeyboardInterrupt) as e:
            logger.warning(f"  Failed to segment {pdf_path.name}: {e}")
            continue

    logger.info(f"Extracted {len(clauses)} NOISY clauses from test PDFs")
    return clauses


# ---------------------------------------------------------------------------
# Groq Annotation (COMMENTED OUT — using Gemma via Google AI instead)
# To switch back: uncomment this block + get_groq_client(), comment Gemma block
# ---------------------------------------------------------------------------
# GROQ_MODELS = [
#     "llama-3.3-70b-versatile",           # Primary: best quality, 70B
#     # "llama-3.1-8b-instant",              # Fallback 1: 8B, 500,000 TPD, supports JSON mode
#     # "mixtral-8x7b-32768",
# ]


# def annotate_clause(client, clause_text: str, retries: int = 3,
#                     model_idx: list = None) -> dict | None:
#     """
#     Send a single clause to Groq (Llama 3.3 70B) for annotation.
#     Uses few-shot examples and native JSON mode for reliable output.
#     Returns parsed JSON dict or None on failure.
#     """
#     if model_idx is None:
#         model_idx = [0]

#     user_prompt = USER_PROMPT_TEMPLATE.format(clause_text=clause_text[:2000])
#     current_model = GROQ_MODELS[model_idx[0]]

#     messages = [
#         {"role": "system", "content": SYSTEM_PROMPT},
#         *FEW_SHOT_EXAMPLES,
#         {"role": "user", "content": user_prompt},
#     ]

#     for attempt in range(retries):
#         try:
#             response = client.chat.completions.create(
#                 model=current_model,
#                 messages=messages,
#                 temperature=0.0,
#                 max_tokens=500,
#                 response_format={"type": "json_object"},
#             )

#             text = response.choices[0].message.content.strip()

#             # Clean markdown code blocks if present
#             if text.startswith("```"):
#                 text = re.sub(r'^```(?:json)?\s*', '', text)
#                 text = re.sub(r'\s*```$', '', text)

#             result = json.loads(text)

#             # Validate required fields
#             if "modality" not in result:
#                 continue
#             if result["modality"] not in ("OBLIGATION", "PROHIBITION", "PERMISSION", "DECLARATIVE"):
#                 continue

#             return result

#         except json.JSONDecodeError:
#             logger.debug(f"  JSON parse error (attempt {attempt+1}): {text[:100]}")
#             time.sleep(2)
#         except Exception as e:
#             error_str = str(e)
#             if "429" in error_str or "rate_limit" in error_str.lower():
#                 # Try switching to fallback model
#                 if model_idx[0] < len(GROQ_MODELS) - 1:
#                     model_idx[0] += 1
#                     new_model = GROQ_MODELS[model_idx[0]]
#                     logger.warning(f"  Rate limited on {current_model}. Switching to {new_model}")
#                     current_model = new_model
#                     time.sleep(5)
#                 else:
#                     wait = 60 * (attempt + 1)
#                     logger.warning(f"  Rate limited on all models. Waiting {wait}s...")
#                     time.sleep(wait)
#             elif "400" in error_str or "invalid" in error_str.lower():
#                 logger.error(f"  Invalid request (attempt {attempt+1}): {error_str}")
#                 time.sleep(2)
#                 return None
#             else:
#                 logger.warning(f"  API error (attempt {attempt+1}): {error_str[:200]}")
#                 time.sleep(5)

#     return None


# ---------------------------------------------------------------------------
# Gemma Annotation (Google AI — Gemma 4 31B)
# ---------------------------------------------------------------------------
def annotate_clause(client, clause_text: str, retries: int = 2,
                    model_idx: list = None) -> dict | None:
    """
    Send a single clause to Google Gemma 4 31B for annotation.
    Embeds few-shot examples in system instruction to avoid multi-turn issues.
    Returns parsed JSON dict or None on failure.
    """
    from google.genai import types

    user_prompt = USER_PROMPT_TEMPLATE.format(clause_text=clause_text[:1000])

    # Build system instruction with few-shot examples embedded
    # This avoids the multi-turn conversation bug where Gemma tries to
    # regenerate all prior turns and hits MAX_TOKENS
    few_shot_text = "\n\nHere are examples of correct classifications:\n"
    for i in range(0, len(FEW_SHOT_EXAMPLES), 2):
        user_ex = FEW_SHOT_EXAMPLES[i]["content"]
        assistant_ex = FEW_SHOT_EXAMPLES[i + 1]["content"]
        few_shot_text += f"\nInput: {user_ex}\nOutput: {assistant_ex}\n"

    full_system = SYSTEM_PROMPT + few_shot_text

    # Single user message — no multi-turn conversation
    contents = [
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]

    config = types.GenerateContentConfig(
        system_instruction=full_system,
        temperature=0.2,
        max_output_tokens=2048,
        # response_mime_type="application/json",
        # response_schema=LegalAnnotation,
        stop_sequences=["Input:", "Classify this clause", "Here are examples"],
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        ]
    )

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=GEMMA_MODEL,
                contents=contents,
                config=config,
            )

            if response.text is None:
                finish_reason = response.candidates[0].finish_reason if response.candidates else "Unknown"
                logger.warning(f"  Empty response text (attempt {attempt+1}). Finish reason: {finish_reason}")
                # MAX_TOKENS = clause is too long for output limit, retrying won't help
                if "MAX_TOKENS" in str(finish_reason):
                    logger.warning(f"  [DATA THAT HIT LIMIT]: {clause_text}")
                    return None
                time.sleep(2)
                continue

            text = response.text.strip()

            # Clean markdown code blocks if present
            if text.startswith("```"):
                text = re.sub(r'^```(?:json)?\s*', '', text)
                text = re.sub(r'\s*```$', '', text)

            result = json.loads(text)

            # Validate required fields
            if "modality" not in result:
                logger.debug(f"  Missing 'modality' key (attempt {attempt+1})")
                continue
            if result["modality"] not in ("OBLIGATION", "PROHIBITION", "PERMISSION", "DECLARATIVE"):
                logger.debug(f"  Invalid modality: {result['modality']} (attempt {attempt+1})")
                continue

            return result

        except json.JSONDecodeError:
            logger.debug(f"  JSON parse error (attempt {attempt+1}): {text[:100]}")
            time.sleep(2)
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower() or "RESOURCE_EXHAUSTED" in error_str:
                wait = 30 * (attempt + 1)
                logger.warning(f"  Rate limited on {GEMMA_MODEL}. Waiting {wait}s...")
                time.sleep(wait)
            elif "400" in error_str or "invalid" in error_str.lower():
                logger.error(f"  Invalid request (attempt {attempt+1}): {error_str[:300]}")
                time.sleep(1)
                return None
            else:
                logger.warning(f"  API error (attempt {attempt+1}): {error_str[:300]}")
                time.sleep(5)

    return None


# ---------------------------------------------------------------------------
# BIO Tag Conversion (for NER model)
# ---------------------------------------------------------------------------
def text_to_bio_tags(clause_text: str, agent_text: str | None, action_text: str | None,
                     tokenizer=None) -> list[dict] | None:
    """
    Convert annotation to BIO-tagged token sequence.

    Returns list of {"token": str, "tag": str} or None if alignment fails.
    Tags: O, B-AGENT, I-AGENT, B-ACTION, I-ACTION
    """
    if tokenizer is None:
        return None

    # Tokenize the clause
    encoding = tokenizer(clause_text, return_offsets_mapping=True, truncation=True,
                          max_length=512, add_special_tokens=True)
    tokens = tokenizer.convert_ids_to_tokens(encoding["input_ids"])
    offsets = encoding["offset_mapping"]

    # Initialize all tags as O
    tags = ["O"] * len(tokens)

    # Find agent span in original text
    if agent_text and agent_text.lower() != "null":
        agent_start = clause_text.lower().find(agent_text.lower())
        if agent_start >= 0:
            agent_end = agent_start + len(agent_text)
            _apply_bio_tags(tags, offsets, agent_start, agent_end, "AGENT")

    # Find action span in original text
    if action_text and action_text.lower() != "null":
        action_start = clause_text.lower().find(action_text.lower())
        if action_start >= 0:
            action_end = action_start + len(action_text)
            _apply_bio_tags(tags, offsets, action_start, action_end, "ACTION")

    return [{"token": t, "tag": tag} for t, tag in zip(tokens, tags)]


def _apply_bio_tags(tags: list, offsets: list, span_start: int, span_end: int, label: str):
    """Apply B-/I- tags to tokens that overlap with the given span."""
    first = True
    for i, (tok_start, tok_end) in enumerate(offsets):
        if tok_start is None or tok_end is None:
            continue
        if tok_end <= span_start or tok_start >= span_end:
            continue
        # This token overlaps with the span
        if tags[i] != "O":
            # Prevent overwriting nested entities (e.g. AGENT inside an ACTION)
            continue
            
        if first:
            tags[i] = f"B-{label}"
            first = False
        else:
            tags[i] = f"I-{label}"


# ---------------------------------------------------------------------------
# Dataset Splitting & Saving
# ---------------------------------------------------------------------------
def split_and_save(annotations: list[dict], output_dir: Path):
    """
    Split annotations into train/val/test and save in formats for both models.

    Model A (Modality): {"text": str, "label": int}
    Model B (NER):      {"tokens": list[str], "tags": list[int]}
    """
    random.shuffle(annotations)

    n = len(annotations)
    n_train = int(n * 0.75)
    n_val = int(n * 0.125)

    train = annotations[:n_train]
    val = annotations[n_train:n_train + n_val]
    test = annotations[n_train + n_val:]

    logger.info(f"Split: train={len(train)}, val={len(val)}, test={len(test)}")

    # --- Model A: Modality Classification ---
    MODALITY_TO_ID = {"OBLIGATION": 0, "PROHIBITION": 1, "PERMISSION": 2, "DECLARATIVE": 3}

    for split_name, split_data in [("train", train), ("val", val), ("test", test)]:
        path = output_dir / f"modality_{split_name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for ann in split_data:
                f.write(json.dumps({
                    "text": ann["clause_text"],
                    "label": MODALITY_TO_ID[ann["modality"]],
                    "label_name": ann["modality"],
                }, ensure_ascii=False) + "\n")
        logger.info(f"  Saved {path.name}: {len(split_data)} examples")

    # --- Model B: NER (Agent + Action extraction) ---
    NER_TAG_TO_ID = {"O": 0, "B-AGENT": 1, "I-AGENT": 2, "B-ACTION": 3, "I-ACTION": 4}

    for split_name, split_data in [("train", train), ("val", val), ("test", test)]:
        path = output_dir / f"ner_{split_name}.jsonl"
        count = 0
        with open(path, "w", encoding="utf-8") as f:
            for ann in split_data:
                if "bio_tags" not in ann or ann["bio_tags"] is None:
                    continue
                tokens = [t["token"] for t in ann["bio_tags"]]
                tag_ids = [NER_TAG_TO_ID.get(t["tag"], 0) for t in ann["bio_tags"]]
                f.write(json.dumps({
                    "tokens": tokens,
                    "tags": tag_ids,
                    "text": ann["clause_text"],
                    "agent": ann.get("agent"),
                    "action": ann.get("action"),
                }, ensure_ascii=False) + "\n")
                count += 1
        logger.info(f"  Saved {path.name}: {count} examples")

    # Save metadata
    meta = {
        "modality_labels": MODALITY_TO_ID,
        "ner_tags": NER_TAG_TO_ID,
        "total_annotations": len(annotations),
        "train_size": len(train),
        "val_size": len(val),
        "test_size": len(test),
    }
    with open(output_dir / "metadata.json", "w") as f:
        json.dump(meta, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logger.info("=" * 70)
    logger.info("ClauseOps Phase 4 v5.0 — Training Data Generation")
    logger.info("=" * 70)

    # 1. Collect raw clauses from all sources
    raw_clauses = []

    cuad_clauses = load_cuad_clauses(max_clauses=2000)
    raw_clauses.extend(cuad_clauses)

    ledgar_clauses = load_ledgar_clauses(max_clauses=4000)
    raw_clauses.extend(ledgar_clauses)

    pdf_clauses = load_test_pdf_clauses()
    raw_clauses.extend(pdf_clauses)

    # 1b. Global Deduplication
    seen = set()
    unique_raw = []
    for c in raw_clauses:
        key = c["text"][:150].strip().lower()
        if key not in seen:
            seen.add(key)
            unique_raw.append(c)
            
    # 1c. Global Category & Modality Balancing
    # Apply modality heuristic to everything
    for c in unique_raw:
        c["_estimated_modality"] = _estimate_modality(c["text"])
        
    # Bucket by category

    category_buckets = defaultdict(list)
    for c in unique_raw:
        category_buckets[c["category"]].append(c)
        
    # Sort each bucket to put PROHIBITION/PERMISSION first, then cap at 30
    modality_priority = {"PROHIBITION": 0, "PERMISSION": 1, "OBLIGATION": 2, "DECLARATIVE": 3}
    capped_clauses = []
    for cat, items in category_buckets.items():
        random.shuffle(items)
        items.sort(key=lambda x: modality_priority.get(x["_estimated_modality"], 3))
        capped_clauses.extend(items[:50])
        
    # Split by modality for bucketed access
    prohibitions = [c for c in capped_clauses if c["_estimated_modality"] == "PROHIBITION"]
    permissions = [c for c in capped_clauses if c["_estimated_modality"] == "PERMISSION"]
    obligations = [c for c in capped_clauses if c["_estimated_modality"] == "OBLIGATION"]
    declaratives = [c for c in capped_clauses if c["_estimated_modality"] == "DECLARATIVE"]

    random.shuffle(prohibitions)
    random.shuffle(permissions)
    random.shuffle(obligations)
    random.shuffle(declaratives)

    # Store them in a dictionary for dynamic sampling
    modality_queues = {
        "PROHIBITION": prohibitions,
        "PERMISSION": permissions,
        "OBLIGATION": obligations,
        "DECLARATIVE": declaratives
    }

    logger.info(f"\n=== RAW DATA READY ===")
    logger.info(f"Prohibitions: {len(prohibitions)}, Permissions: {len(permissions)}")
    logger.info(f"Obligations: {len(obligations)}, Declaratives: {len(declaratives)}")

    # 2. Initialize Google Gemma client
    # client = get_gemma_client()
    clients = get_gemma_clients()
    import itertools
    client_cycle = itertools.cycle(clients)

    logger.info(f"Gemma client initialized ({GEMMA_MODEL}).")

    # 3. Try to load the tokenizer for BIO tag generation
    tokenizer = None
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained("nlpaueb/legal-bert-base-uncased")
        logger.info("Legal-BERT tokenizer loaded for BIO tag generation.")
    except Exception as e:
        logger.warning(f"Could not load tokenizer ({e}). NER data will be generated without BIO tags.")
        logger.warning("You can add BIO tags later on Kaggle where transformers is available.")

    # 4. Annotate with Google Gemma 4 31B
    annotations = []
    raw_outputs = []
    annotated_texts = set()

    # Check for existing progress (resume support)
    raw_path = OUTPUT_DIR / "raw_annotations.jsonl"
    if raw_path.exists():
        with open(raw_path, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                
                # Regenerate BIO tags for cached data
                if tokenizer is not None:
                    data["bio_tags"] = text_to_bio_tags(
                        data["clause_text"], 
                        data.get("agent"), 
                        data.get("action"), 
                        tokenizer
                    )
                    
                raw_outputs.append(data)
                annotations.append(data)
                text_hash = hashlib.md5(data["clause_text"].strip().lower().encode("utf-8")).hexdigest()
                annotated_texts.add(text_hash)
        logger.info(f"Resuming with {len(annotations)} existing annotations.")

    success = len(annotations)
    failures = 0
    TARGET_TOTAL = 2129  # Generate up to 1500 total annotations

    logger.info(f"\nStarting DYNAMIC TARGET-SEEKING annotation (3 parallel workers)...")
    logger.info(f"Primary model: {GEMMA_MODEL}")

    # Track category counts to prevent over-representation at runtime

    category_counts = Counter(a.get("category", "unknown") for a in annotations)
    MAX_PER_CATEGORY = 25  # Hard cap

    from concurrent.futures import ThreadPoolExecutor, as_completed
    PARALLEL_WORKERS = 4

    # def _pick_candidate(modality_queues, annotated_texts, category_counts, current_balance):
    #     """Pick one candidate from the rarest modality queue."""
    #     needed_modality = current_balance.most_common()[-1][0]
    #     queue = modality_queues[needed_modality]
    def _pick_candidate(modality_queues, annotated_texts, category_counts, current_balance):
        """Pick one candidate from the rarest modality queue."""
        needed_modality = current_balance.most_common()[-1][0]
        logger.info(f"  → Targeting: {needed_modality} (has {current_balance[needed_modality]})")
        queue = modality_queues[needed_modality]

        while queue:
            c = queue.pop(0)
            text_key = hashlib.md5(c["text"].strip().lower().encode("utf-8")).hexdigest()
            if text_key in annotated_texts:
                continue
            if category_counts[c["category"]] >= MAX_PER_CATEGORY:
                continue
            # Reserve this text immediately so parallel workers don't pick the same one
            annotated_texts.add(text_key)
            return c, text_key, needed_modality

        # Queue exhausted for this modality
        current_balance[needed_modality] = 9999
        return None, None, needed_modality

    def _annotate_and_postprocess(client, candidate, clause_text, tokenizer):
        """Worker function: call API + validate agent/action."""
        result = annotate_clause(client, clause_text)
        if result is None:
            return None, candidate

        agent = result.get("agent")
        action = result.get("action")

        if agent and agent.lower() != "null":
            if agent.lower() not in clause_text.lower():
                agent = None

        if action and action.lower() != "null":
            if action.lower() not in clause_text.lower():
                action = None

        bio = text_to_bio_tags(clause_text, agent, action, tokenizer)

        return {
            "clause_text": clause_text,
            "modality": result["modality"],
            "agent": agent,
            "action": action,
            "reasoning": result.get("reasoning", ""),
            "source": candidate.get("source", "unknown"),
            "category": candidate.get("category", "unknown"),
            "bio_tags": bio,
        }, candidate

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        while success < TARGET_TOTAL:
            # Step 1: Compute current balance
            current_balance = Counter()
            for m in ["PROHIBITION", "PERMISSION", "OBLIGATION", "DECLARATIVE"]:
            # for m in [ "PERMISSION", "OBLIGATION", "DECLARATIVE"]:
                current_balance[m] = 0
            for a in annotations:
                m = a.get("modality", "DECLARATIVE")
                if m in current_balance:
                    current_balance[m] += 1

            # Log progress every 15 successful API calls
            if success > 0 and success % 15 == 0:
                logger.info(f"Progress: {success}/{TARGET_TOTAL} (Failures={failures})")
                logger.info(f"  Current Balance: {dict(current_balance)}")

            # Step 2: Pick up to PARALLEL_WORKERS candidates
            batch = []
            for _ in range(PARALLEL_WORKERS):
                cand, text_key, needed = _pick_candidate(
                    modality_queues, annotated_texts, category_counts, current_balance
                )
                if cand:
                    batch.append((cand, text_key))

            if not batch:
                if all(len(q) == 0 for q in modality_queues.values()):
                    logger.info("Out of candidates in all queues!")
                    break
                continue

            # Step 3: Fire API calls in parallel
            futures = {}
            for cand, text_key in batch:
                fut = executor.submit(
                    _annotate_and_postprocess,
                    next(client_cycle), cand, cand["text"], tokenizer
                )
                futures[fut] = (cand, text_key)

            # Step 4: Collect results as they complete
            for fut in as_completed(futures):
                cand, text_key = futures[fut]
                try:
                    annotation, _ = fut.result()
                except Exception as e:
                    logger.warning(f"  Worker exception: {e}")
                    annotation = None

                if annotation is None:
                    failures += 1
                    continue

                annotations.append(annotation)
                success += 1
                category_counts[cand.get("category", "unknown")] += 1

                # Save raw output for resume support
                with open(raw_path, "a", encoding="utf-8") as f:
                    raw = {k: v for k, v in annotation.items() if k != "bio_tags"}
                    f.write(json.dumps(raw, ensure_ascii=False) + "\n")

            # Brief pause between batches to be polite to the API
            time.sleep(1)

    logger.info(f"\nAnnotation complete: {success} successful, {failures} failed")

    # 5. Quality filtering & post-processing cleanup
    logger.info("Applying quality filters and post-processing cleanup...")
    before = len(annotations)

    # Remove annotations with no modality
    annotations = [a for a in annotations if a.get("modality")]

    # Remove very short clauses
    annotations = [a for a in annotations if len(a.get("clause_text", "")) >= 80]

    # --- FIX Issue 1: Handle null-agent non-DECLARATIVE records ---
    # If Gemma (31B model) couldn't find an agent, a regex won't do better.
    # Don't try to guess — either downgrade or exclude.
    fix1_downgraded = 0
    fix1_excluded = 0
    for a in annotations:
        if a["modality"] != "DECLARATIVE" and not a.get("agent"):
            clause = a["clause_text"]

            # Procedural/notice clauses → safely downgrade to DECLARATIVE
            notice_keywords = ['notice', 'notification', 'communication',
                               'delivery', 'payment shall be made',
                               'shall be in writing', 'shall be deemed']
            if any(kw in clause.lower() for kw in notice_keywords):
                a["modality"] = "DECLARATIVE"
                a["action"] = None
                fix1_downgraded += 1
            else:
                # Non-recoverable: exclude rather than poison the dataset
                a["_exclude"] = True
                fix1_excluded += 1

    logger.info(f"  Fix1: {fix1_downgraded} procedural downgraded, {fix1_excluded} excluded")
    annotations = [a for a in annotations if not a.get("_exclude")]

    # --- FIX Issue 1b: Drop non-DECLARATIVE with null action ---
    # If we have an agent but no action, the NER model sees AGENT tags but no ACTION tags,
    # which teaches it that actions are optional. That's wrong.
    fix1b_count = 0
    for a in annotations:
        if a["modality"] != "DECLARATIVE" and a.get("agent") and not a.get("action"):
            a["_exclude"] = True
            fix1b_count += 1
    if fix1b_count:
        logger.info(f"  Fix1b: {fix1b_count} records with agent but no action excluded")
        annotations = [a for a in annotations if not a.get("_exclude")]

    # --- FIX Issue 2: Truncate overly long actions ---
    fix2_count = 0
    for a in annotations:
        action = a.get("action")
        if action and len(action.split()) > 40:
            # Try to truncate at first exception/condition boundary
            truncation_markers = [
                ", except ", ", unless ", ", provided that ", ", provided, however",
                "; provided ", " unless such ", " even if ", ", subject to ",
                ", in which case ", ", notwithstanding ", ", including "
            ]
            best_cut = len(action)
            for marker in truncation_markers:
                idx = action.lower().find(marker)
                if 0 < idx < best_cut:
                    best_cut = idx
            
            if best_cut < len(action):
                truncated = action[:best_cut].rstrip(", ;")
                # Safety: don't truncate to something absurdly short
                if len(truncated.split()) >= 5:
                    old_len = len(action.split())
                    a["action"] = truncated
                    fix2_count += 1
                    logger.info(f"  Fix2: Truncated action from {old_len} to {len(a['action'].split())} words")
    logger.info(f"  Fix2 (action truncation): {fix2_count} annotations fixed")

    # --- FIX Issue 4: Remove near-duplicates using MD5 Hash ---
    fix4_count = 0
    seen_texts = {}
    deduped = []
    for a in annotations:
        key = hashlib.md5(a["clause_text"].strip().lower().encode("utf-8")).hexdigest()
        if key not in seen_texts:
            seen_texts[key] = True
            deduped.append(a)
        else:
            fix4_count += 1
    annotations = deduped
    logger.info(f"  Fix4 (duplicates removed): {fix4_count}")

    # --- FIX Issue 5: Downsample over-saturated categories ---

    MAX_CAT_IN_FINAL = 25  # No category should dominate the final dataset
    cat_counts = Counter(a.get("category", "unknown") for a in annotations)
    oversaturated = {cat for cat, cnt in cat_counts.items() if cnt > MAX_CAT_IN_FINAL}
    if oversaturated:
        fix5_annotations = []
        cat_kept = Counter()
        # Shuffle so we don't always keep the first N
        random.shuffle(annotations)
        for a in annotations:
            cat = a.get("category", "unknown")
            if cat in oversaturated and cat_kept[cat] >= MAX_CAT_IN_FINAL:
                continue
            cat_kept[cat] += 1
            fix5_annotations.append(a)
        fix5_removed = len(annotations) - len(fix5_annotations)
        annotations = fix5_annotations
        logger.info(f"  Fix5 (category downsample): removed {fix5_removed} from categories {oversaturated}")
    else:
        logger.info(f"  Fix5 (category downsample): no categories over {MAX_CAT_IN_FINAL}, skipped")

    # Drop clauses where NER tagging failed due to LLM paraphrasing
    valid_annotations = []
    for a in annotations:
        if a["modality"] == "DECLARATIVE":
            valid_annotations.append(a)
            continue
            
        # For actionable clauses, ensure we successfully created B-ACTION tags
        if a.get("bio_tags"):
            has_action_tag = any(tag["tag"] == "B-ACTION" for tag in a["bio_tags"])
            if has_action_tag:
                valid_annotations.append(a)
        else:
            # If no bio_tags were generated at all (e.g., tokenizer missing), keep it
            valid_annotations.append(a)
            
    annotations = valid_annotations

    # Check class balance
    modality_counts = Counter(a["modality"] for a in annotations)
    logger.info(f"Class distribution after cleanup: {dict(modality_counts)}")

    total_annotations = len(annotations)
    for mod, count in modality_counts.items():
        pct = count / total_annotations * 100
        if pct < 10:
            logger.warning(f"  {mod} is underrepresented: {count} ({pct:.1f}%)")

    logger.info(f"After all cleanup: {len(annotations)} (removed {before - len(annotations)} total)")

    # 6. Split and save
    split_and_save(annotations, OUTPUT_DIR)

    # 7. Generate report
    report = f"""# Training Data Generation Report

**Generated:** {time.strftime('%Y-%m-%d %H:%M')}
**Total clauses annotated:** {len(annotations)}
**Failures:** {failures}

## Class Distribution

| Modality | Count | Percentage |
|----------|-------|------------|
"""
    for mod in ["OBLIGATION", "PROHIBITION", "PERMISSION", "DECLARATIVE"]:
        count = modality_counts.get(mod, 0)
        pct = count / len(annotations) * 100 if annotations else 0
        report += f"| {mod} | {count} | {pct:.1f}% |\n"

    report += f"""
## Data Sources

| Source | Count |
|--------|-------|
| CUAD | {len(cuad_clauses)} |
| LEDGAR | {len(ledgar_clauses)} |
| Test PDFs | {len(pdf_clauses)} |

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
"""

    report_path = OUTPUT_DIR / "generation_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"\n{'=' * 70}")
    logger.info(f"✅ DONE! Output saved to: {OUTPUT_DIR}")
    logger.info(f"   Report: {report_path}")
    logger.info(f"{'=' * 70}")


if __name__ == "__main__":
    main()
