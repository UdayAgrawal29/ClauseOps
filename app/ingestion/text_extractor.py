import pdfplumber
import pytesseract
from pdf2image import convert_from_path
import os

# Point to Tesseract exe if on Windows (uncomment if needed)
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def extract_text(file_path: str) -> str:
    text = ""
    
    # 1. Try Digital Extraction
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")

    # 2. OCR Fallback (If text is empty or too short)
    if len(text.strip()) < 50:
        print("Scanned document detected. Running OCR...")
        try:
            # Requires poppler installed on OS
            images = convert_from_path(file_path)
            for img in images:
                text += pytesseract.image_to_string(img) + "\n"
        except Exception as e:
            print(f"OCR Failed: {e}. Is Poppler/Tesseract installed?")
            
    return text