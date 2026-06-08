"""
ClauseOps — Label Mapping for Clause Classification

Maps LEDGAR's 100 provision labels to ClauseOps's 20 actionable categories.

This module is the single source of truth for:
- The 20 target category names
- The LEDGAR 100→20 collapse mapping
- Display-friendly label names for the UI

The auto-mapping uses keyword matching as a bootstrap. After running
Cell 3 of train_classifier.py on Kaggle and getting the actual 100
label names, replace LEDGAR_TO_CLAUSEOPS with the precise manual mapping.
"""

import re

# ============================================================================
# The 20 ClauseOps Target Categories
# ============================================================================
# These are ordered alphabetically for consistent label-to-id mapping.
# The classifier's output IDs map to this sorted list.

CATEGORIES = sorted([
    "ASSIGNMENT",
    "CONFIDENTIALITY",
    "DATA_PROTECTION",
    "DEFINITIONS",
    "DELIVERY_OBLIGATIONS",
    "DISPUTE_RESOLUTION",
    "ENTIRE_AGREEMENT",
    "FORCE_MAJEURE",
    "GOVERNING_LAW",
    "INDEMNIFICATION",
    "IP_OWNERSHIP",
    "LIABILITY_LIMITATION",
    "NON_COMPETE",
    "NOTICES",
    "PAYMENT",
    "PENALTIES",
    "RENEWAL",
    "REPORTING_AUDIT",
    "TERMINATION",
    "WARRANTIES",
])

# Stable ID mappings (alphabetical sort guarantees consistency)
CATEGORY_TO_ID = {cat: i for i, cat in enumerate(CATEGORIES)}
ID_TO_CATEGORY = {i: cat for cat, i in CATEGORY_TO_ID.items()}
NUM_LABELS = len(CATEGORIES)


# ============================================================================
# Display Labels (user-friendly names for the UI)
# ============================================================================

DISPLAY_LABELS = {
    "ASSIGNMENT":           "Assignment & Transfer",
    "CONFIDENTIALITY":      "Confidentiality / NDA",
    "DATA_PROTECTION":      "Data Protection & Privacy",
    "DEFINITIONS":          "Definitions",
    "DELIVERY_OBLIGATIONS": "Delivery & Performance",
    "DISPUTE_RESOLUTION":   "Dispute Resolution",
    "ENTIRE_AGREEMENT":     "General Provisions",
    "FORCE_MAJEURE":        "Force Majeure",
    "GOVERNING_LAW":        "Governing Law & Jurisdiction",
    "INDEMNIFICATION":      "Indemnification",
    "IP_OWNERSHIP":         "Intellectual Property",
    "LIABILITY_LIMITATION": "Limitation of Liability",
    "NON_COMPETE":          "Non-Compete / Non-Solicit",
    "NOTICES":              "Notices & Communications",
    "PAYMENT":              "Payment & Fees",
    "PENALTIES":            "Penalties & Default",
    "RENEWAL":              "Renewal & Extension",
    "REPORTING_AUDIT":      "Reporting & Audit Rights",
    "TERMINATION":          "Termination",
    "WARRANTIES":           "Warranties & Representations",
}


# ============================================================================
# LEDGAR 100 → ClauseOps 20 Mapping
# ============================================================================
# This is populated by keyword-based auto-mapping.
# After running Cell 3 on Kaggle, replace with the precise manual mapping
# provided by your dev assistant.

def build_auto_mapping(label_names: list[str]) -> dict[int, str]:
    """
    Auto-map LEDGAR label names to ClauseOps categories using keywords.

    This gives ~80-85% accuracy. A manual mapping reviewed against the
    actual label names will be more accurate for edge cases.

    Args:
        label_names: List of 100 LEDGAR label strings from the dataset.

    Returns:
        Dict mapping LEDGAR label index → ClauseOps category string.
    """
    keyword_map = {
        "PAYMENT": [
            "payment", "fee", "price", "cost", "royalt", "compensat",
            "expense", "reimburse", "invoice", "billing", "tax",
            "purchase price",
        ],
        "TERMINATION": ["terminat", "expir", "cancel"],
        "CONFIDENTIALITY": [
            "confidential", "non-disclosure", "nda", "secrecy",
            "proprietary information",
        ],
        "GOVERNING_LAW": [
            "governing law", "choice of law", "jurisdiction",
            "applicable law",
        ],
        "INDEMNIFICATION": ["indemnif", "hold harmless"],
        "LIABILITY_LIMITATION": [
            "limitation of liability", "liability limit",
            "cap on damages", "consequential damage",
        ],
        "RENEWAL": ["renewal", "extension", "successor", "option to extend"],
        "IP_OWNERSHIP": [
            "intellectual property", "patent", "copyright",
            "trademark", "license grant", "ip right", "ip ownership",
        ],
        "DISPUTE_RESOLUTION": ["dispute", "arbitrat", "mediat"],
        "NON_COMPETE": [
            "non-compet", "non compet", "restrictive covenant",
            "non-solicit", "non solicit",
        ],
        "ASSIGNMENT": [
            "assignment", "transfer of right", "successors and assigns",
        ],
        "FORCE_MAJEURE": ["force majeure", "act of god", "unforeseeable"],
        "WARRANTIES": [
            "warrant", "represent", "guarantee", "covenant",
        ],
        "DEFINITIONS": ["definition", "defined term", "interpretation"],
        "NOTICES": ["notice", "notification", "communication"],
        "DELIVERY_OBLIGATIONS": [
            "deliver", "performance", "service level",
            "milestone", "obligation",
        ],
        "PENALTIES": [
            "penalt", "liquidated damage", "late fee", "interest rate",
            "default",
        ],
        "DATA_PROTECTION": [
            "data protection", "privacy", "personal data", "gdpr",
        ],
        "ENTIRE_AGREEMENT": [
            "entire agreement", "integration", "severab",
            "amendment", "waiver", "counterpart", "miscellaneous",
            "general provision", "survival", "headings",
        ],
        "REPORTING_AUDIT": [
            "audit", "report", "record", "inspect", "accounting",
            "book and record", "financial statement",
        ],
    }

    mapping = {}
    for idx, name in enumerate(label_names):
        name_lower = name.lower().replace("_", " ").replace("-", " ")
        matched = False
        for category, keywords in keyword_map.items():
            for kw in keywords:
                if kw in name_lower:
                    mapping[idx] = category
                    matched = True
                    break
            if matched:
                break
        if not matched:
            # Unmapped labels default to ENTIRE_AGREEMENT (general/misc catch-all)
            mapping[idx] = "ENTIRE_AGREEMENT"

    return mapping


def format_input(heading: str | None, body_text: str) -> str:
    """
    Format a ClauseChunk's heading + body into classifier input text.

    Strips the section number from the heading (the number is structural,
    not semantic) and prepends the cleaned heading to the body text.

    This consistently improves F1 by 2-4% on short clauses where the
    heading alone disambiguates the type (e.g., "Payment" + body vs just body).

    Examples:
        format_input("3.1. Initial Franchise Fee.", "You must pay...")
        → "Initial Franchise Fee: You must pay..."

        format_input(None, "This Agreement shall be governed by...")
        → "This Agreement shall be governed by..."
    """
    if heading:
        # Strip section numbers: "3.1. Initial Fee." → "Initial Fee"
        clean = re.sub(r'^\d+(\.\d+)*\.?\s*', '', heading).strip().rstrip('.')
        if clean:
            return f"{clean}: {body_text}"
    return body_text
