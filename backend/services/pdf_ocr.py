import os
import shutil
from pathlib import Path
import pymupdf as fitz

class PDFOCRService:
    @staticmethod
    def is_tesseract_available():
        if shutil.which("tesseract"):
            return True
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")
        ]
        return any(os.path.exists(p) for p in common_paths)

    @staticmethod
    def get_tesseract_path():
        if shutil.which("tesseract"):
            return "tesseract"
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        ]
        for p in common_paths:
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def ocr_pdf(input_path, output_path=None, lang="eng"):
        tess_path = PDFOCRService.get_tesseract_path()
        if not tess_path:
            return {
                "available": False,
                "error": "Tesseract OCR binary not found on host. Please install Tesseract-OCR for Windows from https://github.com/UB-Mannheim/tesseract/wiki",
                "extracted_text": ""
            }

        import pytesseract
        from PIL import Image
        pytesseract.pytesseract.tesseract_cmd = tess_path

        doc = fitz.open(str(input_path))
        extracted_pages = []

        for page_idx, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang=lang)
            extracted_pages.append(f"--- Page {page_idx + 1} ---\n" + text)

        doc.close()
        full_text = "\n\n".join(extracted_pages)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(full_text)

        return {
            "available": True,
            "extracted_text": full_text,
            "page_count": len(extracted_pages)
        }
