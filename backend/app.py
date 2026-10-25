import os
import re
import json
from flask import Flask, request, jsonify, send_file, render_template, g
from flask_cors import CORS
from pathlib import Path
from werkzeug.utils import secure_filename

from config import OUTPUT_FOLDER, MAX_CONTENT_LENGTH, SECRET_KEY
from utils.file_manager import create_job, register_job, get_job, save_upload_file, create_zip, cleanup_old_files
from services.pdf_organize import PDFOrganizeService
from services.pdf_optimize import PDFOptimizeService
from services.pdf_convert import PDFConvertService
from services.pdf_security import PDFSecurityService
from services.pdf_edit import PDFEditService
from services.pdf_ocr import PDFOCRService
from services.pdf_intelligence import PDFIntelligenceService
from services.auth_service import AuthService, auth_required
from services.team_service import TeamService
from services.workflow_service import WorkflowService, WORKFLOW_RUNS_DIR
from services.signature_service import SignatureService, SIGNINGS_DIR
from services.billing_service import BillingService
from services.email_service import EmailService, EmailValidationError, EmailNotConfiguredError
from database import init_db

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = SECRET_KEY
CORS(app, resources={r"/*": {"origins": "*"}})

# Initialize database tables on startup
init_db()

SAFE_ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-]+$')

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File size exceeds the maximum limit (100 MB)."}), 413

@app.before_request
def periodic_cleanup():
    cleanup_old_files()
    g.user = None
    auth_header = request.headers.get('Authorization', '')
    token = None
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ', 1)[1]
    elif 'auth_token' in request.args:
        token = request.args.get('auth_token')
    if token:
        g.user = AuthService.get_user_by_token(token)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "service": "PaperPilot Document Platform API",
        "ocr_available": PDFOCRService.is_tesseract_available()
    })

# ----------------- ORGANIZE -----------------

@app.route('/api/pdf/merge', methods=['POST'])
def api_merge():
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"error": "At least 2 PDF files are required to merge."}), 400

    job_id, up_dir, out_dir = create_job()
    paths = [save_upload_file(f, up_dir) for f in files]
    out_pdf = out_dir / "merged_document.pdf"

    try:
        PDFOrganizeService.merge_pdfs(paths, out_pdf)
    except Exception as e:
        return jsonify({"error": f"Failed to merge PDFs: {str(e)}"}), 400
    register_job(job_id, "merge", out_pdf, "merged_document.pdf", file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": "merged_document.pdf",
        "download_url": f"/api/jobs/{job_id}/download/merged_document.pdf"
    })

@app.route('/api/pdf/split', methods=['POST'])
def api_split():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    mode = request.form.get('mode', 'all')
    ranges = request.form.get('ranges', '')

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    try:
        generated = PDFOrganizeService.split_pdf(input_path, out_dir, mode, ranges)
    except Exception as e:
        return jsonify({"error": f"Failed to split PDF: {str(e)}"}), 400

    if not generated:
        return jsonify({"error": "No pages produced with the given ranges."}), 400

    zip_path = out_dir / "split_pages.zip"
    create_zip(generated, zip_path)
    register_job(job_id, "split", zip_path, "split_pages.zip", file_type="application/zip", metadata={"page_count": len(generated)})

    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "page_count": len(generated),
        "filename": "split_pages.zip",
        "download_url": f"/api/jobs/{job_id}/download/split_pages.zip"
    })

@app.route('/api/pdf/rotate', methods=['POST'])
def api_rotate():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    try:
        rotation = int(request.form.get('rotation', 90))
    except (ValueError, TypeError):
        return jsonify({"error": "Rotation must be a valid integer."}), 400

    if rotation % 90 != 0:
        return jsonify({"error": "Rotation must be a multiple of 90 degrees."}), 400

    pages = request.form.get('pages', 'all')

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"rotated_{safe_name}"

    try:
        PDFOrganizeService.rotate_pdf(input_path, out_pdf, rotation, pages)
    except Exception as e:
        return jsonify({"error": f"Failed to rotate PDF: {str(e)}"}), 400
    register_job(job_id, "rotate", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/organize', methods=['POST'])
def api_organize():
    file = request.files.get('file')
    order_raw = request.form.get('page_order')
    rotations_json = request.form.get('rotations', '{}')

    if not file or not order_raw:
        return jsonify({"error": "File and page_order are required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)

    import pymupdf as fitz
    try:
        doc = fitz.open(str(input_path))
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        return jsonify({"error": f"Invalid PDF file: {str(e)}"}), 400

    # Parse page_order which might be JSON list or comma-separated list/ranges
    page_order = []
    try:
        trimmed = order_raw.strip()
        if trimmed.startswith('['):
            page_order = json.loads(trimmed)
        else:
            parts = [p.strip() for p in trimmed.split(',') if p.strip()]
            for p in parts:
                if '-' in p:
                    sub = p.split('-')
                    start, end = int(sub[0]), int(sub[1])
                    for n in range(start, end + 1):
                        page_order.append(n)
                else:
                    page_order.append(int(p))
    except Exception as e:
        return jsonify({"error": f"Invalid page_order format: {str(e)}"}), 400

    if not page_order:
        return jsonify({"error": "Page order cannot be empty."}), 400

    # If user provided 1-based indices (min >= 1 and max <= total_pages), convert to 0-based
    if min(page_order) >= 1 and max(page_order) <= total_pages:
        page_order = [p - 1 for p in page_order]

    try:
        rotations = json.loads(rotations_json) if isinstance(rotations_json, str) else (rotations_json or {})
    except Exception:
        rotations = {}

    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"organized_{safe_name}"
    PDFOrganizeService.organize_pdf(input_path, out_pdf, page_order, rotations)
    register_job(job_id, "organize", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/crop', methods=['POST'])
def api_crop():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    try:
        top = float(request.form.get('top', 5))
        bottom = float(request.form.get('bottom', 5))
        left = float(request.form.get('left', 5))
        right = float(request.form.get('right', 5))
    except (ValueError, TypeError):
        return jsonify({"error": "Crop margins must be valid numbers."}), 400

    if top < 0 or bottom < 0 or left < 0 or right < 0:
        return jsonify({"error": "Crop margins cannot be negative."}), 400

    if top + bottom >= 100 or left + right >= 100:
        return jsonify({"error": "Total crop margins must be less than 100%."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"cropped_{safe_name}"

    try:
        PDFOrganizeService.crop_pdf(input_path, out_pdf, top, bottom, left, right)
    except Exception as e:
        return jsonify({"error": f"Failed to crop PDF: {str(e)}"}), 400
    register_job(job_id, "crop", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/page-numbers', methods=['POST'])
def api_page_numbers():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    position = request.form.get('position', 'bottom-right')
    try:
        start_num = int(request.form.get('start_num', 1))
        font_size = int(request.form.get('font_size', 10))
    except (ValueError, TypeError):
        return jsonify({"error": "Start number and font size must be integers."}), 400

    format_pattern = request.form.get('format', '{n}')

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"numbered_{safe_name}"

    try:
        PDFOrganizeService.add_page_numbers(input_path, out_pdf, position, start_num, font_size, format_pattern)
    except Exception as e:
        return jsonify({"error": f"Failed to add page numbers: {str(e)}"}), 400
    register_job(job_id, "page-numbers", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

# ----------------- OPTIMIZE -----------------

@app.route('/api/pdf/compress', methods=['POST'])
def api_compress():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    level = request.form.get('level', 'medium')
    if level not in ('low', 'medium', 'extreme'):
        level = 'medium'

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"compressed_{safe_name}"

    try:
        res = PDFOptimizeService.compress_pdf(input_path, out_pdf, level)
    except Exception as e:
        return jsonify({"error": f"Failed to compress PDF: {str(e)}"}), 400
    register_job(job_id, "compress", out_pdf, out_pdf.name, file_type="application/pdf", metadata={
        "original_size": res["original_size"],
        "compressed_size": res["compressed_size"],
        "reduction_percent": res["reduction_percent"]
    })
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "original_size": res["original_size"],
        "compressed_size": res["compressed_size"],
        "reduction_percent": res["reduction_percent"],
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/repair', methods=['POST'])
def api_repair():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"repaired_{safe_name}"

    success, path, message = PDFOptimizeService.repair_pdf(input_path, out_pdf)
    if not success:
        return jsonify({"error": message}), 500

    register_job(job_id, "repair", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "message": message,
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/compare', methods=['POST'])
def api_compare():
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"error": "Two PDF files are required for comparison."}), 400

    job_id, up_dir, _ = create_job()
    p1 = save_upload_file(files[0], up_dir)
    p2 = save_upload_file(files[1], up_dir)

    result = PDFOptimizeService.compare_pdfs(p1, p2)
    return jsonify({"success": True, "comparison": result})

# ----------------- CONVERT -----------------

@app.route('/api/pdf/convert/word', methods=['POST'])
def api_convert_word():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_docx = out_dir / f"{safe_stem}.docx"

    PDFConvertService.pdf_to_word(input_path, out_docx)
    register_job(job_id, "convert_word", out_docx, out_docx.name, file_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_docx.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_docx.name}"
    })

@app.route('/api/pdf/convert/images', methods=['POST'])
def api_convert_images():
    file = request.files.get('file')
    fmt = request.form.get('format', 'jpg')
    dpi = int(request.form.get('dpi', 200))

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    images = PDFConvertService.pdf_to_images(input_path, out_dir, img_format=fmt, dpi=dpi)

    zip_path = out_dir / f"{Path(file.filename).stem}_images.zip"
    create_zip(images, zip_path)
    register_job(job_id, "convert_images", zip_path, zip_path.name, file_type="application/zip")

    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "count": len(images),
        "filename": zip_path.name,
        "download_url": f"/api/jobs/{job_id}/download/{zip_path.name}"
    })

@app.route('/api/pdf/convert/images-to-pdf', methods=['POST'])
def api_images_to_pdf():
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "At least one image is required."}), 400

    job_id, up_dir, out_dir = create_job()
    paths = [save_upload_file(f, up_dir) for f in files]
    out_pdf = out_dir / "images_combined.pdf"

    PDFConvertService.images_to_pdf(paths, out_pdf)
    register_job(job_id, "images_to_pdf", out_pdf, "images_combined.pdf", file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": "images_combined.pdf",
        "download_url": f"/api/jobs/{job_id}/download/images_combined.pdf"
    })

@app.route('/api/pdf/convert/powerpoint', methods=['POST'])
def api_convert_pptx():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_pptx = out_dir / f"{safe_stem}.pptx"

    PDFConvertService.pdf_to_powerpoint(input_path, out_pptx)
    register_job(job_id, "convert_powerpoint", out_pptx, out_pptx.name, file_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pptx.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pptx.name}"
    })

@app.route('/api/pdf/convert/excel', methods=['POST'])
def api_convert_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_xlsx = out_dir / f"{safe_stem}.xlsx"

    PDFConvertService.pdf_to_excel(input_path, out_xlsx)
    register_job(job_id, "convert_excel", out_xlsx, out_xlsx.name, file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_xlsx.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_xlsx.name}"
    })

@app.route('/api/pdf/convert/markdown', methods=['POST'])
def api_convert_markdown():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_md = out_dir / f"{safe_stem}.md"

    PDFConvertService.pdf_to_markdown(input_path, out_md)
    register_job(job_id, "convert_markdown", out_md, out_md.name, file_type="text/markdown")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_md.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_md.name}"
    })

@app.route('/api/pdf/convert/html-to-pdf', methods=['POST'])
def api_html_to_pdf():
    html_content = request.form.get('html', '')
    if not html_content.strip() and 'file' in request.files:
        f = request.files.get('file')
        if f and f.filename:
            try:
                html_content = f.read().decode('utf-8', errors='ignore')
            except Exception:
                pass

    if not html_content.strip():
        return jsonify({"error": "HTML content or an uploaded .html file is required."}), 400

    job_id, _, out_dir = create_job()
    out_pdf = out_dir / "converted_page.pdf"

    PDFConvertService.html_to_pdf(html_content, out_pdf)
    register_job(job_id, "html_to_pdf", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/convert/word-to-pdf', methods=['POST'])
def api_word_to_pdf():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "Word document (.docx) is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_pdf = out_dir / f"{safe_stem}.pdf"

    PDFConvertService.word_to_pdf(input_path, out_pdf)
    register_job(job_id, "word_to_pdf", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/convert/excel-to-pdf', methods=['POST'])
def api_excel_to_pdf():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "Excel workbook (.xlsx) is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_pdf = out_dir / f"{safe_stem}.pdf"

    PDFConvertService.excel_to_pdf(input_path, out_pdf)
    register_job(job_id, "excel_to_pdf", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/convert/powerpoint-to-pdf', methods=['POST'])
def api_powerpoint_to_pdf():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PowerPoint presentation (.pptx) is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_pdf = out_dir / f"{safe_stem}.pdf"

    PDFConvertService.powerpoint_to_pdf(input_path, out_pdf)
    register_job(job_id, "powerpoint_to_pdf", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/convert/pdfa', methods=['POST'])
def api_pdf_to_pdfa():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_pdf = out_dir / f"{safe_stem}_pdfa.pdf"

    res = PDFConvertService.pdf_to_pdfa(input_path, out_pdf)
    register_job(job_id, "pdfa", out_pdf, out_pdf.name, file_type="application/pdf", metadata=res)
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "conformance": res["conformance"],
        "page_count": res["page_count"],
        "report": res["report"],
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/convert/scan-to-pdf', methods=['POST'])
def api_scan_to_pdf():
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "At least one scanned image is required."}), 400

    filter_mode = request.form.get('filter', 'enhance')
    job_id, up_dir, out_dir = create_job()
    paths = [save_upload_file(f, up_dir) for f in files]
    out_pdf = out_dir / "scanned_document.pdf"

    PDFConvertService.scan_to_pdf(paths, out_pdf, filter_mode=filter_mode)
    register_job(job_id, "scan_to_pdf", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

# ----------------- SECURITY & EDITING -----------------

@app.route('/api/pdf/protect', methods=['POST'])
def api_protect():
    file = request.files.get('file')
    password = request.form.get('password')
    if not file or not password:
        return jsonify({"error": "File and password are required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"protected_{safe_name}"

    PDFSecurityService.protect_pdf(input_path, out_pdf, password)
    register_job(job_id, "protect", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/unlock', methods=['POST'])
def api_unlock():
    file = request.files.get('file')
    password = request.form.get('password', '')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"unlocked_{safe_name}"

    try:
        PDFSecurityService.unlock_pdf(input_path, out_pdf, password)
        register_job(job_id, "unlock", out_pdf, out_pdf.name, file_type="application/pdf")
        return jsonify({
            "success": True,
            "job_id": job_id,
            "status": "completed",
            "filename": out_pdf.name,
            "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/pdf/redact', methods=['POST'])
def api_redact():
    file = request.files.get('file')
    terms = request.form.get('terms', '').split(',')
    if not file or not terms:
        return jsonify({"error": "File and terms to redact are required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"redacted_{safe_name}"

    count, _ = PDFSecurityService.redact_pdf(input_path, out_pdf, terms)
    register_job(job_id, "redact", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "redactions_applied": count,
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/watermark', methods=['POST'])
def api_watermark():
    file = request.files.get('file')
    text = request.form.get('text', 'CONFIDENTIAL')
    try:
        opacity = float(request.form.get('opacity', 0.3))
    except (ValueError, TypeError):
        opacity = 0.3
    try:
        font_size = int(request.form.get('font_size', 40))
    except (ValueError, TypeError):
        font_size = 40
    try:
        rotation = float(request.form.get('rotation', 0))
    except (ValueError, TypeError):
        rotation = 0.0

    layer = request.form.get('layer', 'overlay').strip().lower()
    overlay = (layer != 'background')

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"watermarked_{safe_name}"

    PDFEditService.watermark_pdf(input_path, out_pdf, text=text, opacity=opacity, font_size=font_size, rotation=rotation, overlay=overlay)
    register_job(job_id, "watermark", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/sign', methods=['POST'])
def api_sign():
    file = request.files.get('file')
    sig_data = request.form.get('signature_data')
    sig_file = request.files.get('signature_file')
    page_num = int(request.form.get('page', 1))
    x = float(request.form.get('x', 100))
    y = float(request.form.get('y', 100))
    w = float(request.form.get('width', 160))
    h = float(request.form.get('height', 60))

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"signed_{safe_name}"

    if sig_file:
        saved_sig = save_upload_file(sig_file, up_dir)
        PDFEditService.sign_pdf(input_path, out_pdf, saved_sig, page_num=page_num, x=x, y=y, width=w, height=h)
    elif sig_data:
        PDFEditService.sign_pdf(input_path, out_pdf, sig_data, page_num=page_num, x=x, y=y, width=w, height=h)
    else:
        return jsonify({"error": "Signature drawing or signature image file is required."}), 400

    register_job(job_id, "sign", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/edit', methods=['POST'])
def api_edit():
    file = request.files.get('file')
    text = request.form.get('text', '')
    page_num = int(request.form.get('page', 1))
    x = float(request.form.get('x', 50))
    y = float(request.form.get('y', 50))
    font_size = float(request.form.get('font_size', 12))

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"edited_{safe_name}"

    items = [{"text": text, "page": page_num, "x": x, "y": y, "font_size": font_size}] if text else []
    PDFEditService.edit_pdf(input_path, out_pdf, text_items=items)
    register_job(job_id, "edit", out_pdf, out_pdf.name, file_type="application/pdf")

    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

@app.route('/api/pdf/forms/detect', methods=['POST'])
def api_forms_detect():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, _ = create_job()
    input_path = save_upload_file(file, up_dir)
    fields = PDFEditService.detect_form_fields(input_path)

    return jsonify({
        "success": True,
        "field_count": len(fields),
        "fields": fields
    })

@app.route('/api/pdf/forms/fill', methods=['POST'])
def api_forms_fill():
    file = request.files.get('file')
    values_json = request.form.get('fields', '{}')

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_name = secure_filename(file.filename) or "document.pdf"
    out_pdf = out_dir / f"filled_{safe_name}"

    try:
        field_values = json.loads(values_json)
    except Exception:
        field_values = {}

    count, _ = PDFEditService.fill_form_fields(input_path, out_pdf, field_values)
    register_job(job_id, "forms_fill", out_pdf, out_pdf.name, file_type="application/pdf")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "fields_updated": count,
        "filename": out_pdf.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_pdf.name}"
    })

# ----------------- OCR & INTELLIGENCE -----------------

@app.route('/api/pdf/ocr', methods=['POST'])
def api_ocr():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_txt = out_dir / f"{safe_stem}_ocr.txt"

    res = PDFOCRService.ocr_pdf(input_path, out_txt)
    if not res["available"]:
        return jsonify(res), 503

    register_job(job_id, "ocr", out_txt, out_txt.name, file_type="text/plain")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "page_count": res.get("page_count", 0),
        "filename": out_txt.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_txt.name}"
    })

@app.route('/api/pdf/summarize', methods=['POST'])
def api_summarize():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, _ = create_job()
    input_path = save_upload_file(file, up_dir)
    summary_data = PDFIntelligenceService.summarize_pdf(input_path)

    return jsonify({
        "success": True,
        "analysis": summary_data
    })

@app.route('/api/pdf/translate', methods=['POST'])
def api_translate():
    file = request.files.get('file')
    target_lang = request.form.get('target_lang', 'es')

    if not file:
        return jsonify({"error": "PDF file is required."}), 400

    job_id, up_dir, out_dir = create_job()
    input_path = save_upload_file(file, up_dir)
    safe_stem = Path(secure_filename(file.filename) or "document.pdf").stem
    out_txt = out_dir / f"{safe_stem}_translated_{target_lang}.txt"

    trans_result = PDFIntelligenceService.translate_pdf(input_path, target_lang=target_lang, output_txt_path=out_txt)
    register_job(job_id, "translate", out_txt, out_txt.name, file_type="text/plain")
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
        "translation": trans_result,
        "filename": out_txt.name,
        "download_url": f"/api/jobs/{job_id}/download/{out_txt.name}"
    })

# ----------------- AUTH & ACCOUNTS -----------------

@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    data = request.get_json(silent=True) or {}
    email = data.get('email', '')
    password = data.get('password', '')
    display_name = data.get('display_name')
    country = data.get('country', 'IN')
    timezone = data.get('timezone', 'Asia/Kolkata')

    try:
        user_info = AuthService.register_user(email, password, display_name, country, timezone)
        return jsonify({"success": True, "user": user_info}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Registration failed: {str(e)}"}), 500

@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    email = data.get('email', '')
    password = data.get('password', '')

    try:
        user_info = AuthService.login_user(email, password)
        return jsonify({"success": True, "user": user_info})
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 500

@app.route('/api/auth/logout', methods=['POST'])
@auth_required
def api_auth_logout():
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.split(' ', 1)[1] if auth_header.startswith('Bearer ') else None
    if token:
        AuthService.logout_token(token)
    return jsonify({"success": True, "message": "Logged out successfully."})

@app.route('/api/auth/me', methods=['GET'])
@auth_required
def api_auth_me():
    sub = BillingService.get_user_subscription(g.user['id'])
    user_data = dict(g.user)
    user_data['subscription'] = sub
    user_data['is_premium'] = sub.get('is_premium', False)
    return jsonify({"success": True, "user": user_data})

@app.route('/api/auth/profile', methods=['PUT'])
@auth_required
def api_auth_update_profile():
    data = request.get_json(silent=True) or {}
    try:
        AuthService.update_profile(
            g.user['id'],
            display_name=data.get('display_name'),
            country=data.get('country'),
            timezone=data.get('timezone'),
            avatar_url=data.get('avatar_url')
        )
        return jsonify({"success": True, "message": "Profile updated successfully."})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/auth/password', methods=['POST'])
@auth_required
def api_auth_change_password():
    data = request.get_json(silent=True) or {}
    old_pwd = data.get('current_password', '')
    new_pwd = data.get('new_password', '')

    try:
        AuthService.change_password(g.user['id'], old_pwd, new_pwd)
        return jsonify({"success": True, "message": "Password changed successfully."})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auth/account', methods=['DELETE'])
@auth_required
def api_auth_delete_account():
    try:
        AuthService.delete_account(g.user['id'])
        return jsonify({"success": True, "message": "Account deleted successfully."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------- TEAMS -----------------

@app.route('/api/teams', methods=['GET'])
@auth_required
def api_teams_list():
    teams = TeamService.get_user_teams(g.user['id'])
    return jsonify({"success": True, "teams": teams})

@app.route('/api/teams', methods=['POST'])
@auth_required
def api_teams_create():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    try:
        res = TeamService.create_team(g.user['id'], name)
        return jsonify({"success": True, "team": res}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/teams/<team_id>/members', methods=['GET'])
@auth_required
def api_teams_members(team_id):
    try:
        res = TeamService.get_team_members(team_id, g.user['id'])
        return jsonify({"success": True, **res})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/teams/<team_id>/invite', methods=['POST'])
@auth_required
def api_teams_invite(team_id):
    data = request.get_json(silent=True) or {}
    email = data.get('email', '')
    role = data.get('role', 'member')
    try:
        inv = TeamService.invite_member(team_id, g.user['id'], email, role)
        return jsonify({"success": True, "invitation": inv}), 201
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/teams/join', methods=['POST'])
@auth_required
def api_teams_join():
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    try:
        res = TeamService.accept_invitation(token, g.user['id'])
        return jsonify({"success": True, **res})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/teams/<team_id>/members/<target_uid>', methods=['PUT'])
@auth_required
def api_teams_update_role(team_id, target_uid):
    data = request.get_json(silent=True) or {}
    role = data.get('role', 'member')
    try:
        TeamService.update_member_role(team_id, g.user['id'], target_uid, role)
        return jsonify({"success": True, "message": "Member role updated."})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/teams/<team_id>/members/<target_uid>', methods=['DELETE'])
@auth_required
def api_teams_remove_member(team_id, target_uid):
    try:
        TeamService.remove_member(team_id, g.user['id'], target_uid)
        return jsonify({"success": True, "message": "Member removed."})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ----------------- WORKFLOWS -----------------

@app.route('/api/workflows/steps', methods=['GET'])
def api_workflows_supported_steps():
    return jsonify({"success": True, "steps": WorkflowService.get_supported_steps()})

@app.route('/api/workflows', methods=['GET'])
@auth_required
def api_workflows_list():
    team_id = request.args.get('team_id')
    workflows = WorkflowService.list_workflows(g.user['id'], team_id=team_id)
    return jsonify({"success": True, "workflows": workflows})

@app.route('/api/workflows', methods=['POST'])
@auth_required
def api_workflows_create():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    description = data.get('description', '')
    steps = data.get('steps', [])
    team_id = data.get('team_id')

    try:
        wf = WorkflowService.create_workflow(g.user['id'], name, description, steps, team_id=team_id)
        return jsonify({"success": True, "workflow": wf}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/workflows/<workflow_id>', methods=['GET'])
@auth_required
def api_workflows_get(workflow_id):
    wf = WorkflowService.get_workflow(workflow_id, g.user['id'])
    if not wf:
        return jsonify({"error": "Workflow not found."}), 404
    return jsonify({"success": True, "workflow": wf})

@app.route('/api/workflows/<workflow_id>', methods=['PUT'])
@auth_required
def api_workflows_update(workflow_id):
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    description = data.get('description', '')
    steps = data.get('steps', [])

    try:
        wf = WorkflowService.update_workflow(workflow_id, g.user['id'], name, description, steps)
        return jsonify({"success": True, "workflow": wf})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/workflows/<workflow_id>', methods=['DELETE'])
@auth_required
def api_workflows_delete(workflow_id):
    try:
        WorkflowService.delete_workflow(workflow_id, g.user['id'])
        return jsonify({"success": True, "message": "Workflow deleted."})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

@app.route('/api/workflows/execute', methods=['POST'])
@auth_required
def api_workflows_execute():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "Input document file is required."}), 400

    workflow_id = request.form.get('workflow_id')
    steps_raw = request.form.get('steps')
    custom_steps = None
    if steps_raw:
        try:
            custom_steps = json.loads(steps_raw)
        except Exception:
            return jsonify({"error": "Invalid JSON format for custom steps."}), 400

    if not workflow_id and not custom_steps:
        return jsonify({"error": "Either workflow_id or steps list must be provided."}), 400

    job_id, up_dir, _ = create_job()
    input_path = save_upload_file(file, up_dir)

    try:
        res = WorkflowService.execute_workflow(
            user_id=g.user['id'],
            input_file_path=input_path,
            original_filename=file.filename,
            workflow_id=workflow_id,
            custom_steps=custom_steps
        )
        return jsonify({"success": True, **res})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Workflow execution error: {str(e)}"}), 500

@app.route('/api/workflows/executions', methods=['GET'])
@auth_required
def api_workflows_executions_list():
    runs = WorkflowService.list_executions(g.user['id'])
    return jsonify({"success": True, "executions": runs})

@app.route('/api/workflows/executions/<exec_id>/download', methods=['GET'])
@app.route('/api/workflows/executions/<exec_id>/download/<path:filename>', methods=['GET'])
def api_workflows_download(exec_id, filename=None):
    if not SAFE_ID_REGEX.match(exec_id):
        return jsonify({"error": "Invalid execution identifier."}), 400
    exec_record = WorkflowService.get_execution(exec_id)
    if not exec_record or not exec_record.get('output_filename'):
        return jsonify({"error": "Execution file not found."}), 404
    raw_name = exec_record['output_filename']
    run_dir = (WORKFLOW_RUNS_DIR / exec_id).resolve()
    if not run_dir.is_relative_to(WORKFLOW_RUNS_DIR.resolve()):
        return jsonify({"error": "Access denied."}), 403

    candidate = (run_dir / raw_name).resolve()
    if not candidate.exists() or not candidate.is_relative_to(run_dir):
        candidate = (run_dir / secure_filename(raw_name)).resolve()

    if not candidate.exists() or not candidate.is_relative_to(run_dir):
        job = get_job(exec_id)
        if job and job.get('output_path'):
            job_p = Path(job['output_path']).resolve()
            if job_p.exists():
                candidate = job_p

    if not candidate.exists() or not candidate.is_file():
        return jsonify({"error": "Output file expired or missing."}), 404
    return send_file(str(candidate), as_attachment=True, download_name=raw_name)


# ----------------- SIGNATURES -----------------

@app.route('/api/signatures', methods=['GET'])
@auth_required
def api_signatures_list():
    sigs = SignatureService.list_signatures(g.user['id'])
    return jsonify({"success": True, "signatures": sigs})

@app.route('/api/signatures', methods=['POST'])
@auth_required
def api_signatures_create():
    data = request.get_json(silent=True) or {}
    name = data.get('name', 'My Signature')
    sig_type = data.get('sig_type', 'draw')
    data_url = data.get('data_url', '')
    is_default = bool(data.get('is_default', False))

    if not data_url:
        return jsonify({"error": "Signature data URL is required."}), 400

    sig = SignatureService.save_signature(g.user['id'], name, sig_type, data_url, is_default)
    return jsonify({"success": True, "signature": sig}), 201

@app.route('/api/signatures/<sig_id>/default', methods=['PUT'])
@auth_required
def api_signatures_set_default(sig_id):
    SignatureService.set_default_signature(sig_id, g.user['id'])
    return jsonify({"success": True, "message": "Default signature updated."})

@app.route('/api/signatures/<sig_id>', methods=['DELETE'])
@auth_required
def api_signatures_delete(sig_id):
    SignatureService.delete_signature(sig_id, g.user['id'])
    return jsonify({"success": True, "message": "Signature deleted."})

@app.route('/api/signatures/sign-document', methods=['POST'])
@auth_required
def api_signatures_sign_doc():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "PDF document is required."}), 400

    title = request.form.get('title', file.filename)
    page = int(request.form.get('page', 1))
    x = float(request.form.get('x', 100))
    y = float(request.form.get('y', 100))
    width = float(request.form.get('width', 160))
    height = float(request.form.get('height', 60))

    signature_data = request.form.get('signature_data')
    signature_id = request.form.get('signature_id')

    if signature_id:
        saved_sig = SignatureService.get_signature(signature_id, g.user['id'])
        if not saved_sig:
            return jsonify({"error": "Saved signature not found."}), 404
        signature_data = saved_sig['data_url']

    if not signature_data:
        return jsonify({"error": "Signature data or valid signature ID required."}), 400

    job_id, up_dir, _ = create_job()
    input_path = save_upload_file(file, up_dir)

    signer_name = g.user.get('display_name') or 'Verified Signer'
    signer_email = g.user.get('email') or 'user@paperpilot.local'
    client_ip = request.remote_addr or '127.0.0.1'

    try:
        result = SignatureService.sign_document(
            user_id=g.user['id'],
            input_pdf_path=input_path,
            signature_data=signature_data,
            title=title,
            page=page,
            x=x,
            y=y,
            width=width,
            height=height,
            signer_name=signer_name,
            signer_email=signer_email,
            ip_address=client_ip
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": f"Signing error: {str(e)}"}), 500

@app.route('/api/signatures/documents', methods=['GET'])
@auth_required
def api_signatures_documents_list():
    status = request.args.get('status')
    docs = SignatureService.list_documents(g.user['id'], status=status)
    return jsonify({"success": True, "documents": docs})

@app.route('/api/signatures/documents/<signing_id>', methods=['GET'])
@auth_required
def api_signatures_document_get(signing_id):
    doc = SignatureService.get_document(signing_id, g.user['id'])
    if not doc:
        return jsonify({"error": "Signed document record not found."}), 404
    return jsonify({"success": True, "document": doc})

@app.route('/api/signatures/documents/<signing_id>/download', methods=['GET'])
def api_signatures_download(signing_id):
    if not SAFE_ID_REGEX.match(signing_id):
        return jsonify({"error": "Invalid signing identifier."}), 400
    doc = SignatureService.get_document(signing_id)
    if not doc or not doc.get('signed_file'):
        return jsonify({"error": "Signed file not found."}), 404

    raw_name = os.path.basename(doc['signed_file'])
    safe_name = secure_filename(raw_name)

    file_path = (SIGNINGS_DIR / safe_name).resolve()
    if not (file_path.is_relative_to(SIGNINGS_DIR.resolve()) and file_path.exists()):
        raw_path = (SIGNINGS_DIR / raw_name).resolve()
        if raw_path.is_relative_to(SIGNINGS_DIR.resolve()) and raw_path.exists():
            file_path = raw_path
        else:
            return jsonify({"error": "Signed PDF file missing on disk."}), 404

    return send_file(str(file_path), as_attachment=True)


# ----------------- BILLING & RAZORPAY -----------------

@app.route('/api/billing/plans', methods=['GET'])
def api_billing_plans():
    return jsonify({"success": True, "plans": BillingService.get_plans()})

@app.route('/api/billing/subscription', methods=['GET'])
@auth_required
def api_billing_subscription():
    sub = BillingService.get_user_subscription(g.user['id'])
    return jsonify({"success": True, "subscription": sub})

@app.route('/api/billing/gateway-status', methods=['GET'])
@auth_required
def api_billing_gateway_status():
    """Returns Razorpay configuration status so the frontend can show test-mode warnings."""
    key_id, _, _, payment_link = BillingService.get_credentials()
    configured = BillingService.is_configured()
    is_test = configured and key_id.startswith('rzp_test_')
    is_live = configured and key_id.startswith('rzp_live_')
    return jsonify({
        "success": True,
        "configured": configured,
        "test_mode": is_test,
        "live_mode": is_live,
        "payment_link_mode": not configured,
        "payment_link": payment_link or "https://razorpay.me/@nishantrajeshpatil"
    })

@app.route('/api/billing/order', methods=['POST'])
@auth_required
def api_billing_order():
    data = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id')
    try:
        order = BillingService.create_order(g.user['id'], plan_id)
        return jsonify({"success": True, "mode": order.get("mode"), "order": order})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/verify', methods=['POST'])
@auth_required
def api_billing_verify():
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id')
    payment_id = data.get('payment_id')
    signature = data.get('signature')
    plan_id = data.get('plan_id', 'premium_monthly')
    # Accept a client-reported amount ONLY for underpayment detection;
    # the backend always uses its own trusted expected amount and will
    # reject anything that doesn't match.
    reported_amount = data.get('amount')

    try:
        res = BillingService.verify_payment(
            g.user['id'], order_id, payment_id, signature, plan_id,
            reported_amount=reported_amount
        )
        return jsonify(res)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Verification error: {str(e)}"}), 500

@app.route('/api/billing/claim-payment', methods=['POST'])
@auth_required
def api_billing_claim_payment():
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id')
    payment_id = data.get('payment_id')
    signature = data.get('signature')
    plan_id = data.get('plan_id', 'premium_monthly')
    reported_amount = data.get('amount')

    # Route A: Full cryptographic verification (Razorpay SDK checkout flow)
    # Requires order_id + payment_id + signature
    if order_id and signature:
        try:
            res = BillingService.verify_payment(g.user['id'], order_id, payment_id, signature, plan_id, reported_amount)
            return jsonify(res)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": f"Payment activation error: {str(e)}"}), 500

    # Route B: Payment-link claim flow (razorpay.me / external UPI)
    # User paid externally and enters their payment ID from UPI/email receipt
    if payment_id:
        try:
            res = BillingService.claim_payment_link(g.user['id'], payment_id, plan_id)
            return jsonify(res)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": f"Payment claim error: {str(e)}"}), 500

    return jsonify({"error": "Provide either (order_id + signature) for SDK checkout or (payment_id) for payment-link claim."}), 400

@app.route('/api/billing/sandbox-sign', methods=['POST'])
@auth_required
def api_billing_sandbox_sign():
    """Helper for testing checkout: signs (order_id, payment_id) with backend test secret."""
    data = request.get_json(silent=True) or {}
    order_id = data.get('order_id')
    payment_id = data.get('payment_id')
    if not order_id or not payment_id:
        return jsonify({"error": "Missing order_id or payment_id"}), 400
    sig = BillingService.generate_sandbox_signature(order_id, payment_id)
    return jsonify({"success": True, "signature": sig})

@app.route('/api/billing/webhook/razorpay', methods=['POST'])
def api_billing_webhook():
    raw_payload = request.get_data()
    sig_header = request.headers.get('X-Razorpay-Signature')
    try:
        res = BillingService.handle_razorpay_webhook(raw_payload, sig_header)
        return jsonify(res)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/history', methods=['GET'])
@auth_required
def api_billing_history():
    history = BillingService.list_transactions(g.user['id'])
    return jsonify({"success": True, "transactions": history})

# ----------------- CONTACT & COMMUNICATION -----------------

@app.route('/api/contact/status', methods=['GET'])
def api_contact_status():
    from config import CONTACT_RECIPIENT
    return jsonify({
        "success": True,
        "configured": EmailService.is_configured(),
        "recipient": CONTACT_RECIPIENT
    })

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '')
    email = data.get('email', '')
    message = data.get('message', '')
    bot_field = data.get('website_bot', '')

    try:
        result = EmailService.send_contact_message(name, email, message, bot_field=bot_field)
        return jsonify(result), 200
    except EmailValidationError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except EmailNotConfiguredError as e:
        from config import CONTACT_RECIPIENT
        return jsonify({
            "success": False,
            "unconfigured": True,
            "error": str(e),
            "direct_email": CONTACT_RECIPIENT
        }), 503
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to send message: {str(e)}"}), 500

# ----------------- SECURE DOWNLOAD & JOB MANAGEMENT -----------------

@app.route('/api/jobs/<job_id>', methods=['GET'])
@app.route('/api/jobs/<job_id>/status', methods=['GET'])
def get_job_status(job_id):
    if not SAFE_ID_REGEX.match(job_id):
        return jsonify({"error": "Invalid job identifier.", "job_id": job_id}), 400
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found or expired.", "job_id": job_id}), 404
    return jsonify({
        "success": True,
        "job_id": job["job_id"],
        "operation": job.get("operation", "unknown"),
        "status": job.get("status", "completed"),
        "filename": job.get("output_filename"),
        "download_url": f"/api/jobs/{job_id}/download"
    })

@app.route('/api/jobs/<job_id>/download', methods=['GET'])
@app.route('/api/jobs/<job_id>/download/<path:filename>', methods=['GET'])
def download_job_file(job_id, filename=None):
    if not SAFE_ID_REGEX.match(job_id):
        app.logger.warning(f"Download rejected: invalid job identifier '{job_id}'")
        return jsonify({"error": "Invalid job identifier.", "job_id": job_id}), 400

    mimetypes_map = {
        '.pdf': 'application/pdf',
        '.zip': 'application/zip',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.txt': 'text/plain',
        '.md': 'text/markdown'
    }

    # 1. Check Job Registry directly
    job = get_job(job_id)
    if job and job.get("output_path"):
        cand = Path(job["output_path"]).resolve()
        from config import STORAGE_DIR
        if cand.is_relative_to(STORAGE_DIR.resolve()) and cand.is_file():
            output_filename = filename or job.get("output_filename") or cand.name
            mimetype = job.get("file_type") or mimetypes_map.get(cand.suffix.lower(), 'application/octet-stream')
            return send_file(
                str(cand),
                as_attachment=True,
                download_name=output_filename,
                mimetype=mimetype
            )

    job_dir = (OUTPUT_FOLDER / job_id).resolve()
    if not job_dir.is_relative_to(OUTPUT_FOLDER.resolve()):
        app.logger.warning(f"Download access denied: directory traversal attempt '{job_dir}'")
        return jsonify({"error": "Access denied.", "job_id": job_id}), 403

    if not job_dir.exists():
        app.logger.warning(f"Download 404: job directory '{job_dir}' does not exist")
        return jsonify({"error": "Job not found or expired.", "job_id": job_id}), 404
    file_path = None
    output_filename = filename

    if job and job.get("output_path"):
        cand = Path(job["output_path"]).resolve()
        if cand.is_relative_to(job_dir) and cand.is_file():
            file_path = cand
            if not output_filename:
                output_filename = job.get("output_filename") or cand.name

    # 2. If not resolved via job registry, locate file in job_dir
    if not file_path:
        if filename:
            cand = (job_dir / filename).resolve()
            if cand.is_relative_to(job_dir) and cand.is_file():
                file_path = cand
            else:
                safe_name = secure_filename(filename)
                cand = (job_dir / safe_name).resolve()
                if cand.is_relative_to(job_dir) and cand.is_file():
                    file_path = cand

        if not file_path:
            valid_files = [f for f in job_dir.iterdir() if f.is_file() and f.name != "job.json"]
            if valid_files:
                valid_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                file_path = valid_files[0]
                if not output_filename:
                    output_filename = file_path.name

    if not file_path or not file_path.exists():
        app.logger.warning(f"Download 404: output file not found in '{job_dir}' for job '{job_id}'")
        return jsonify({"error": "Generated output file not found or expired.", "job_id": job_id}), 404

    if not output_filename:
        output_filename = file_path.name

    mimetypes_map = {
        '.pdf': 'application/pdf',
        '.zip': 'application/zip',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.txt': 'text/plain',
        '.md': 'text/markdown'
    }
    mimetype = mimetypes_map.get(file_path.suffix.lower(), 'application/octet-stream')

    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=output_filename,
        mimetype=mimetype
    )

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    print(f"PaperPilot Document Platform listening on http://0.0.0.0:{port} (Local: http://127.0.0.1:{port})")
    app.run(host="0.0.0.0", port=port, debug=False)
