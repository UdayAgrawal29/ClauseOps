# ClauseOps: AI-Powered Legal Contract Intelligence System

**ClauseOps** is a AI-powered contract intelligence system designed to automate the ingestion, segmentation, and classification of legal documents. It converts static PDF contracts into actionable, structured data.

## 🚀 Overview

Many businesses sign hundreds of contracts yearly but lack dedicated staff to monitor obligations. ClauseOps solves this by taking raw PDF contracts and processing them through a multi-stage NLP pipeline.

This module focuses on the **v2 Architecture** which shifts from purely rule-based to a hybrid machine-learning pipeline.

### Core Features (In Progress)
- **PDF Ingestion & Text Extraction**: Extracts text while maintaining structural integrity.
- **Clause Segmentation**: Breaks down complex legal text into individual, manageable clauses.
- **Clause Classification**: Uses NLP (Transformer-based models) to categorize clauses (e.g., Payment, Termination, Non-Compete).
- **Entity & Obligation Extraction**: Detects named entities and obligations for automated tracking.

## 🧠 Architecture Highlights
The core NLP pipeline involves:
1. **Clause Segmentation**: Sentence boundary detection and regex heuristics.
2. **Classification**: Fine-tuned Transformer models (like DeBERTa) on legal datasets (LEDGAR/CUAD).
3. **NER & Obligation Detection**: LegalBERT for entity extraction and RoBERTa-NLI for obligation mapping.

*For full architectural details, see the `PROJECT IDEA/ClauseOps_Complete_Blueprint.md`.*

## 💻 Tech Stack
- **Language**: Python 3.10+
- **NLP**: HuggingFace Transformers, spaCy, PyTorch
- **Document Processing**: PyMuPDF, Docling

## 🛠️ Setup & Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/UdayAgrawal29/ClauseOps.git
   cd ClauseOps
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
