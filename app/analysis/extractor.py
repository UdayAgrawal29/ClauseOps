import spacy
import re
from typing import Dict, Any, List

# ---------------------------------------------------
# Load NLP Model (Singleton Pattern)
# ---------------------------------------------------
print("Loading NLP Model (en_core_web_trf)...")
try:
    nlp = spacy.load("en_core_web_trf")
except OSError:
    print("Transformer model not found. Run: python -m spacy download en_core_web_trf")
    print("Falling back to 'en_core_web_sm' (Expect lower accuracy).")
    nlp = spacy.load("en_core_web_sm")

class ContractExtractor:
    def __init__(self):
        # ------------------------------------------------------------
        # REGEX 1: Legal Tautologies (The "seven (7) days" Fix)
        # Matches: "seven (7) days", "sixty (60) days"
        # Captures the digit inside the parentheses.
        # ------------------------------------------------------------
        self.legal_pattern = re.compile(
            r"(?:\b\w+\s*)?\(\s*(?P<value>\d+)\s*\)\s*(?P<unit>day|days|month|months|year|years)", 
            re.IGNORECASE
        )

        # ------------------------------------------------------------
        # REGEX 2: Simple or Hyphenated (The "10-year" Fix)
        # Matches: "30 days", "10-year", "12-month"
        # ------------------------------------------------------------
        self.simple_pattern = re.compile(
            r"\b(?P<value>\d+)\s*-?\s*(?P<unit>day|days|month|months|year|years)\b", 
            re.IGNORECASE
        )

        # ------------------------------------------------------------
        # BLACKLISTS (The "Garbage Collection" Fix)
        # ------------------------------------------------------------
        self.ROLE_BLACKLIST = {
            "licensor", "licensee", "party", "parties", "affiliate", "affiliates",
            "company", "provider", "customer", "client", "contractor", "subcontractor"
        }

        self.DATE_NOISE = {
            "day", "days", "date", "year", "years", "saturday", "sunday", 
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "business day", "any day", "page", "schedule", "section", "article"
        }

        self.BOILERPLATE_HEADERS = [
            "preamble", "recitals", "witnesseth", "entire agreement", 
            "severability", "counterparts", "headings", "interpretation", 
            "no presumption", "miscellaneous", "definitions"
        ]

    def _normalize_duration(self, value: int, unit: str) -> str:
        """Converts values to ISO 8601 Duration string (e.g., P30D)."""
        unit = unit.lower()
        if "day" in unit:
            return f"P{value}D"
        elif "month" in unit:
            return f"P{value}M"
        elif "year" in unit:
            return f"P{value}Y"
        return None

    def extract_entities(self, text: str) -> Dict[str, Any]:
        doc = nlp(text)

        entities = {
            "dates": [],
            "money": [],
            "organizations": [],
            "percentages": [],
            "durations_raw": [],
            "durations_normalized": []
        }

        # ---- 1. Standard NER (spaCy) with Filtering ----
        for ent in doc.ents:
            text_lower = ent.text.lower()
            
            if ent.label_ == "DATE":
                # Filter out garbage dates like "Page 3" or "a Saturday"
                if not any(noise in text_lower for noise in self.DATE_NOISE):
                    # specific check for noise that might be part of a string (e.g. "3 days")
                    # We want absolute dates here, not durations.
                    if len(ent.text) > 4 and not re.search(r'\d+\s*(day|year|month)', text_lower):
                        entities["dates"].append(ent.text)

            elif ent.label_ == "MONEY":
                entities["money"].append(ent.text)

            elif ent.label_ == "ORG":
                # Filter out Legal Roles (False Positives)
                if text_lower not in self.ROLE_BLACKLIST and len(ent.text) > 3:
                     # Filter out weird punctuation artifacts
                    if not re.search(r'[0-9\(\)]', ent.text):
                        entities["organizations"].append(ent.text)

            elif ent.label_ == "PERCENT":
                entities["percentages"].append(ent.text)

        # ---- 2. Duration Extraction (Regex) ----
        # Run Legal Pattern FIRST (High Confidence)
        for match in self.legal_pattern.finditer(text):
            val = int(match.group("value"))
            unit = match.group("unit")
            entities["durations_raw"].append(match.group(0))
            norm = self._normalize_duration(val, unit)
            if norm: entities["durations_normalized"].append(norm)

        # Run Simple Pattern SECOND
        for match in self.simple_pattern.finditer(text):
            # Check overlap: Don't add if we already caught this span in legal_pattern
            # (Simple heuristic: if the number is already in raw list, skip)
            val = int(match.group("value"))
            unit = match.group("unit")
            raw_str = match.group(0)
            
            # Avoid adding "30 days" if we already added "thirty (30) days"
            if not any(str(val) in existing for existing in entities["durations_raw"]):
                entities["durations_raw"].append(raw_str)
                norm = self._normalize_duration(val, unit)
                if norm: entities["durations_normalized"].append(norm)

        # ---- 3. Deduplication ----
        for key in entities:
            entities[key] = list(set(entities[key]))

        return entities

    def analyze_clause(self, clause_header: str, clause_text: str):
        """
        Determines Intent and Extracts Data.
        """
        header_lower = clause_header.lower()
        text_lower = clause_text.lower()
        intent = "INFO"

        # ---- 1. Boilerplate Check (Fast Fail) ----
        if any(b in header_lower for b in self.BOILERPLATE_HEADERS):
            # Special case: Definitions are INFO, Severability is BOILERPLATE
            intent = "BOILERPLATE"
            # Return early extraction, but force intent
            entities = self.extract_entities(clause_text)
            return {
                "intent": intent, 
                "entities": entities,
                "structured_durations": entities["durations_normalized"]
            }

        # ---- 2. Strict Intent Hierarchy ----
        
        # A. Indemnification (High Priority - Money/Risk)
        if "indemni" in header_lower or "hold harmless" in text_lower:
            intent = "INDEMNIFICATION"

        # B. Dispute Resolution
        elif any(x in header_lower for x in ["dispute", "arbitration", "jurisdiction", "governing law"]):
            intent = "DISPUTE_RESOLUTION"
        elif any(x in text_lower for x in ["arbitration", "arbitrator", "court of competent jurisdiction"]):
            intent = "DISPUTE_RESOLUTION"

        # C. Termination (High Priority)
        # MUST check header first to avoid "licensee shall not terminate" logic in random clauses
        elif any(x in header_lower for x in ["term", "termination", "expire", "survival"]):
            intent = "TERMINATION_LOGIC"
        # Cure periods usually relate to termination
        elif "cure period" in text_lower or "breach" in header_lower:
            intent = "TERMINATION_LOGIC"

        # D. Financials / Taxes
        elif any(x in header_lower for x in ["fees", "payment", "royalties", "taxes", "billing"]):
            intent = "PAYMENT_OBLIGATION"
        elif any(x in text_lower for x in ["shall pay", "reimburse", "remit", "invoice"]):
            intent = "PAYMENT_OBLIGATION"

        # E. Representations
        elif "representation" in header_lower or "warranty" in header_lower:
            intent = "REPRESENTATION"

        # F. Time Bound (Catch-all for operational deadlines)
        elif any(x in text_lower for x in ["within", "no later than", "no longer than"]):
            if intent == "INFO": # Don't overwrite higher priorities
                intent = "TIME_BOUND_OBLIGATION"
        
        # G. General Obligation
        elif "shall" in text_lower or "must" in text_lower:
            if intent == "INFO":
                intent = "OBLIGATION"

        # ---- 3. Run Extraction ----
        entities = self.extract_entities(clause_text)

        # ---- 4. Final Result Construction ----
        return {
            "intent": intent,
            "entities": entities,
            # ✅ DIRECTLY POPULATE THIS FIELD FOR THE JSON RESPONSE
            "structured_durations": entities["durations_normalized"] 
        }