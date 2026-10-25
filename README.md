# PaperPilot — Your Documents, Under Control.

> **Every document. Every tool. One place.**  
> Convert, edit, organize, protect, and understand your documents with ease.

---

## 🌟 Highlights

- **Original Brand Identity**: Custom geometric paper & supersonic flight navigation wing iconography.
- **7 Specification Tool Categories**:
  1. Organize PDF
  2. Optimize PDF
  3. Convert to PDF
  4. Convert from PDF
  5. Edit PDF
  6. PDF Security
  7. PDF Intelligence
- **Modern Color System**:
  - Primary Navy: `#173B4F`
  - Dark Navy: `#102C3C`
  - Mint Accent: `#B8F2DC`
  - Sky Blue (AI Features): `#8AC8F9`
  - Canvas Background: `#F6F8FA`
  - Deep Text: `#243746` / Muted Text: `#536777`
  - Borders: `#DCE5EB`
- **31 Complete Working Tools**: Full functional coverage with real processing logic, automated tests, and immediate downloads.
- **High-Fidelity Document Engines**: Powered by PyMuPDF (`fitz`), `pdf2docx`, `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, ReportLab, and `Pillow`.
- **Hybrid Architecture**:
  - **Server-Side Engine**: Deep layout preservation, stream deflation, table detection, and format reconstruction.
  - **In-Browser Fallback Engine**: Pure client-side PDF.js & docx.js conversion (`Demo.html`).
- **Interactive Dashboard & Workspaces**:
  - Dual-mode signature stamping (interactive HTML5 draw canvas + signature upload).
  - Interactive PDF form field discovery and automatic filling.
  - Multi-language translation with character count and bilingual alignments.
  - ISO 19005-2 PDF/A-2b archival conversion and compliance reporting.
  - Searchable tool directory with instant filtering and category tabs.
- **Comprehensive Automated Test Suite**: 20 automated tests passing with `pytest`.

---

## 🛠️ Feature Audit Table

| Category | Tool | Endpoint / Logic | Status |
|---|---|---|:---:|
| **Organize PDF** | Merge PDF | `/api/pdf/merge` | Implemented and tested |
| | Split PDF (All or Custom Ranges) | `/api/pdf/split` | Implemented and tested |
| | Organize PDF | `/api/pdf/organize` | Implemented and tested |
| | Rotate PDF (90°, 180°, 270°) | `/api/pdf/rotate` | Implemented and tested |
| | Page Numbers (Position & Format) | `/api/pdf/page-numbers` | Implemented and tested |
| | Crop PDF Margins | `/api/pdf/crop` | Implemented and tested |
| **Optimize PDF** | Compress PDF (Extreme / Recommended / Low) | `/api/pdf/compress` | Implemented and tested |
| | Repair Damaged PDF | `/api/pdf/repair` | Implemented and tested |
| | Compare 2 PDFs (Diff View) | `/api/pdf/compare` | Implemented and tested |
| **Convert to PDF** | Word to PDF (.docx to .pdf) | `/api/pdf/convert/word-to-pdf` | Implemented and tested |
| | Excel to PDF (.xlsx to .pdf) | `/api/pdf/convert/excel-to-pdf` | Implemented and tested |
| | PowerPoint to PDF (.pptx to .pdf) | `/api/pdf/convert/powerpoint-to-pdf` | Implemented and tested |
| | JPG to PDF (Images to .pdf) | `/api/pdf/convert/images-to-pdf` | Implemented and tested |
| | Scan to PDF (Contrast enhance / Grayscale) | `/api/pdf/convert/scan-to-pdf` | Implemented and tested |
| | HTML to PDF | `/api/pdf/convert/html-to-pdf` | Implemented and tested |
| **Convert from PDF**| PDF to Word (.docx) | `/api/pdf/convert/word` | Implemented and tested |
| | PDF to Excel (.xlsx) | `/api/pdf/convert/excel` | Implemented and tested |
| | PDF to PowerPoint (.pptx) | `/api/pdf/convert/powerpoint` | Implemented and tested |
| | PDF to Images (.jpg/.zip) | `/api/pdf/convert/images` | Implemented and tested |
| | PDF to Markdown (.md) | `/api/pdf/convert/markdown` | Implemented and tested |
| | PDF to PDF/A (Archival ISO 19005-2) | `/api/pdf/convert/pdfa` | Implemented and tested |
| **Edit PDF** | Edit PDF / Annotations | `/api/pdf/edit` | Implemented and tested |
| | Watermark (Custom text/opacity) | `/api/pdf/watermark` | Implemented and tested |
| | Sign PDF (Canvas Draw & Upload) | `/api/pdf/sign` | Implemented and tested |
| | PDF Forms (Discover & Fill) | `/api/pdf/forms/detect`, `/fill` | Implemented and tested |
| **PDF Security** | Protect PDF (AES-256 encryption) | `/api/pdf/protect` | Implemented and tested |
| | Unlock PDF (Authorized passphrase) | `/api/pdf/unlock` | Implemented and tested |
| | Redact PDF (Permanent sanitization) | `/api/pdf/redact` | Implemented and tested |
| **PDF Intelligence**| AI Document Summarizer | `/api/pdf/summarize` | Implemented and tested |
| | Translate PDF (Multi-language) | `/api/pdf/translate` | Implemented and tested |
| | OCR PDF (Searchable text layer) | `/api/pdf/ocr` | Implemented and tested |

---

## 🚀 Quick Start

### 1. One-Click Launch (Windows)
Double-click `run.bat` in the project root:
```cmd
run.bat
```
Then open:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

### 2. Manual Command Line
```powershell
# Navigate to the backend directory
cd backend

# Set python path and start the application
$env:PYTHONPATH="."
python app.py
```
Open **[http://127.0.0.1:5000](http://127.0.0.1:5000)** in your browser.

---

### 3. Running Automated Tests
```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests/test_backend.py -v
```

---

## 📦 System Dependencies (Optional for Scanned OCR)

- **Tesseract OCR**:
  Download the Windows installer from [UB-Mannheim Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and install to default location (`C:\Program Files\Tesseract-OCR\`). PaperPilot auto-detects it on startup.
