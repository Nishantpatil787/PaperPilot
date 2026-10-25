"""
Comprehensive Automated Tests for PaperPilot PDF Processing & Download System
Verifies:
- Merge, Split, Organize, Rotate, Page Numbers, Crop, Compress
- Job registry creation, persistence, and lookup
- Output file existence and integrity verification (parsing downloaded PDFs)
- Filenames containing spaces, parentheses, and special characters
- Download via /api/jobs/<job_id>/download and /api/jobs/<job_id>/download/<filename>
- 404 handling for nonexistent jobs or missing files
- Input validation (crop margins, rotation degrees, invalid PDFs)
"""
import io
import json
import uuid
import zipfile
import pytest
from pathlib import Path
import pymupdf as fitz

from app import app
from utils.file_manager import get_job, create_job, register_job
from config import OUTPUT_FOLDER


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_synthetic_pdf(pages=2, text_prefix="Page"):
    """Creates an in-memory PDF with specified number of pages and text."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # Standard A4
        page.insert_text((72, 72), f"{text_prefix} {i + 1}: Sample PaperPilot document content.")
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────
# 1. Merge PDF & Download
# ─────────────────────────────────────────────────────────────
def test_merge_and_download(client):
    pdf1 = _create_synthetic_pdf(2, "DocA")
    pdf2 = _create_synthetic_pdf(3, "DocB")

    data = {
        'files': [
            (pdf1, 'doc_one.pdf'),
            (pdf2, 'doc_two.pdf'),
        ]
    }
    r = client.post('/api/pdf/merge', data=data, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    assert d.get("status") == "completed"
    job_id = d["job_id"]
    assert job_id

    # Verify job registry
    job = get_job(job_id)
    assert job is not None
    assert job["operation"] == "merge"
    assert Path(job["output_path"]).exists()

    # Verify download via download_url
    dl = client.get(d["download_url"])
    assert dl.status_code == 200
    assert len(dl.data) > 0

    # Parse downloaded PDF
    doc = fitz.open(stream=dl.data, filetype="pdf")
    assert len(doc) == 5  # 2 + 3 pages
    doc.close()


# ─────────────────────────────────────────────────────────────
# 2. Split PDF & Download
# ─────────────────────────────────────────────────────────────
def test_split_and_download(client):
    pdf = _create_synthetic_pdf(3, "SplitDoc")
    r = client.post('/api/pdf/split', data={
        'file': (pdf, 'document_to_split.pdf'),
        'mode': 'all'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    dl = client.get(d["download_url"])
    assert dl.status_code == 200
    # Verify valid zip containing split pages
    zf = zipfile.ZipFile(io.BytesIO(dl.data))
    namelist = zf.namelist()
    assert len(namelist) == 3
    assert all(name.endswith('.pdf') for name in namelist)


# ─────────────────────────────────────────────────────────────
# 3. Organize PDF & Download
# ─────────────────────────────────────────────────────────────
def test_organize_and_download(client):
    pdf = _create_synthetic_pdf(3, "OrganizeDoc")
    # Request reordering: page 3 first, then page 1 (1-based indices 3, 1)
    r = client.post('/api/pdf/organize', data={
        'file': (pdf, 'merged document (1).pdf'),
        'page_order': '3, 1'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    # Download via URL
    dl = client.get(d["download_url"])
    assert dl.status_code == 200

    doc = fitz.open(stream=dl.data, filetype="pdf")
    assert len(doc) == 2
    text0 = doc[0].get_text()
    assert "OrganizeDoc 3" in text0
    text1 = doc[1].get_text()
    assert "OrganizeDoc 1" in text1
    doc.close()


# ─────────────────────────────────────────────────────────────
# 4. Rotate PDF & Download
# ─────────────────────────────────────────────────────────────
def test_rotate_and_download(client):
    pdf = _create_synthetic_pdf(2, "RotateDoc")
    r = client.post('/api/pdf/rotate', data={
        'file': (pdf, 'rotated_merged_document (2).pdf'),
        'rotation': '90',
        'pages': 'all'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    dl = client.get(d["download_url"])
    assert dl.status_code == 200

    doc = fitz.open(stream=dl.data, filetype="pdf")
    assert len(doc) == 2
    assert doc[0].rotation == 90
    assert doc[1].rotation == 90
    doc.close()


# ─────────────────────────────────────────────────────────────
# 5. Page Numbers & Download
# ─────────────────────────────────────────────────────────────
def test_page_numbers_and_download(client):
    pdf = _create_synthetic_pdf(2, "PageNumDoc")
    r = client.post('/api/pdf/page-numbers', data={
        'file': (pdf, 'numbered_merged_document (2).pdf'),
        'position': 'bottom-right',
        'start_num': '1',
        'format': 'Page {n} of {total}'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    dl = client.get(d["download_url"])
    assert dl.status_code == 200

    doc = fitz.open(stream=dl.data, filetype="pdf")
    assert len(doc) == 2
    text0 = doc[0].get_text()
    assert "Page 1 of 2" in text0
    text1 = doc[1].get_text()
    assert "Page 2 of 2" in text1
    doc.close()


# ─────────────────────────────────────────────────────────────
# 6. Crop PDF & Download
# ─────────────────────────────────────────────────────────────
def test_crop_and_download(client):
    pdf = _create_synthetic_pdf(1, "CropDoc")
    r = client.post('/api/pdf/crop', data={
        'file': (pdf, 'cropped_merged_document (2).pdf'),
        'top': '10',
        'bottom': '10',
        'left': '10',
        'right': '10'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    dl = client.get(d["download_url"])
    assert dl.status_code == 200

    doc = fitz.open(stream=dl.data, filetype="pdf")
    page = doc[0]
    crop = page.cropbox
    # Original A4 is 595 x 842. Cropped 10% on each side should be smaller:
    assert crop.width < 590
    assert crop.height < 835
    doc.close()


# ─────────────────────────────────────────────────────────────
# 7. Compress PDF & Download
# ─────────────────────────────────────────────────────────────
def test_compress_and_download(client):
    pdf = _create_synthetic_pdf(3, "CompressDoc")
    r = client.post('/api/pdf/compress', data={
        'file': (pdf, 'compressed_merged_document (1).pdf'),
        'level': 'medium'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    d = r.get_json()
    assert d["success"] is True
    assert "original_size" in d
    assert "compressed_size" in d
    assert "reduction_percent" in d
    job_id = d["job_id"]

    job = get_job(job_id)
    assert job is not None
    assert Path(job["output_path"]).exists()

    dl = client.get(d["download_url"])
    assert dl.status_code == 200

    doc = fitz.open(stream=dl.data, filetype="pdf")
    assert len(doc) == 3
    doc.close()


# ─────────────────────────────────────────────────────────────
# 8. Special Filenames with Spaces, Parentheses, & Characters
# ─────────────────────────────────────────────────────────────
def test_special_filenames_with_spaces_and_parentheses(client):
    test_filenames = [
        "merged document (1).pdf",
        "my report - Q3 (final copy).pdf",
        "doc with spaces and (special) chars.pdf",
    ]

    for fname in test_filenames:
        pdf = _create_synthetic_pdf(1, "SpecialName")
        r = client.post('/api/pdf/rotate', data={
            'file': (pdf, fname),
            'rotation': '180'
        }, content_type='multipart/form-data')
        assert r.status_code == 200
        d = r.get_json()
        job_id = d["job_id"]

        # Download using the returned download_url (with filename)
        dl1 = client.get(d["download_url"])
        assert dl1.status_code == 200, f"Failed download for {fname} via {d['download_url']}"
        assert len(dl1.data) > 0

        # Also download using the canonical /api/jobs/<job_id>/download endpoint
        dl2 = client.get(f"/api/jobs/{job_id}/download")
        assert dl2.status_code == 200, f"Failed canonical download for {fname}"
        assert len(dl2.data) > 0


# ─────────────────────────────────────────────────────────────
# 9. Nonexistent Job ID returns 404
# ─────────────────────────────────────────────────────────────
def test_nonexistent_job_returns_404(client):
    fake_job = str(uuid.uuid4())
    r = client.get(f"/api/jobs/{fake_job}/download")
    assert r.status_code == 404
    d = r.get_json()
    assert "error" in d


# ─────────────────────────────────────────────────────────────
# 10. Missing Output File Returns Clear Error
# ─────────────────────────────────────────────────────────────
def test_missing_output_file_returns_error(client):
    # Manually register a job whose file doesn't exist
    fake_id = str(uuid.uuid4())
    job_dir = OUTPUT_FOLDER / fake_id
    job_dir.mkdir(parents=True, exist_ok=True)
    fake_file = job_dir / "nonexistent.pdf"

    register_job(fake_id, "test_missing", fake_file, "nonexistent.pdf")

    r = client.get(f"/api/jobs/{fake_id}/download")
    assert r.status_code == 404
    d = r.get_json()
    assert "error" in d


# ─────────────────────────────────────────────────────────────
# 11. Invalid PDF Uploads Handled Safely
# ─────────────────────────────────────────────────────────────
def test_invalid_pdf_uploads_handled(client):
    corrupt_data = io.BytesIO(b"This is not a real PDF file content.")
    r = client.post('/api/pdf/rotate', data={
        'file': (corrupt_data, 'corrupt.pdf'),
        'rotation': '90'
    }, content_type='multipart/form-data')
    assert r.status_code in (400, 500)
    d = r.get_json()
    assert "error" in d


# ─────────────────────────────────────────────────────────────
# 12. Input Validation (Crop Margins & Rotation Degrees)
# ─────────────────────────────────────────────────────────────
def test_crop_margin_validation(client):
    pdf = _create_synthetic_pdf(1)
    # Total top + bottom >= 100 must be rejected
    r = client.post('/api/pdf/crop', data={
        'file': (pdf, 'test.pdf'),
        'top': '60',
        'bottom': '50',
        'left': '5',
        'right': '5'
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    d = r.get_json()
    assert "100%" in d["error"]

    # Negative margin must be rejected
    pdf2 = _create_synthetic_pdf(1)
    r2 = client.post('/api/pdf/crop', data={
        'file': (pdf2, 'test.pdf'),
        'top': '-5',
        'bottom': '10',
        'left': '5',
        'right': '5'
    }, content_type='multipart/form-data')
    assert r2.status_code == 400


def test_rotate_degree_validation(client):
    pdf = _create_synthetic_pdf(1)
    r = client.post('/api/pdf/rotate', data={
        'file': (pdf, 'test.pdf'),
        'rotation': '45'  # Not a multiple of 90
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    d = r.get_json()
    assert "multiple of 90" in d["error"]


# ─────────────────────────────────────────────────────────────
# 13. Job Status Endpoint Verification
# ─────────────────────────────────────────────────────────────
def test_job_status_endpoint(client):
    pdf = _create_synthetic_pdf(1, "StatusTest")
    r = client.post('/api/pdf/rotate', data={
        'file': (pdf, 'status_test.pdf'),
        'rotation': '90'
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    job_id = r.get_json()["job_id"]

    st = client.get(f"/api/jobs/{job_id}/status")
    assert st.status_code == 200
    sd = st.get_json()
    assert sd["success"] is True
    assert sd["job_id"] == job_id
    assert sd["status"] == "completed"
    assert sd["operation"] == "rotate"
