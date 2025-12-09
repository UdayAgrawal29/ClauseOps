import pdfplumber
import pytesseract
import tempfile
import cv2
import numpy as np

def extract_text_from_pdf(file):
    text = ""

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    # OCR fallback if digital text is empty
    if len(text.strip()) < 50:
        file.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file.read())
            tmp_path = tmp.name

        images = pdfplumber.open(tmp_path).images
        for img in images:
            image = cv2.imread(img["stream"])
            text += pytesseract.image_to_string(image)

    return text
