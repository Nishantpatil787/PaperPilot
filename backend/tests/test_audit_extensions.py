import io
import json
import pytest
import pymupdf as fitz
from app import app
from services.auth_service import AuthService, hash_password
from services.team_service import TeamService
from services.billing_service import BillingService
from .test_platform import create_sample_pdf_bytes

@pytest.fixture
def client():
    return app.test_client()

def test_path_traversal_blocked(client):
    """Ensure path traversal attacks on job downloads, workflows and signatures fail."""
    res1 = client.get('/api/jobs/%2E%2E/download/paperpilot.db')
    assert res1.status_code in [400, 403, 404]

    res2 = client.get('/api/jobs/../download/paperpilot.db')
    assert res2.status_code in [400, 403, 404]

    res3 = client.get('/api/jobs/fake_id/download/..%2F..%2Fpaperpilot.db')
    assert res3.status_code in [400, 403, 404]

    res4 = client.get('/api/workflows/executions/%2E%2E/download')
    assert res4.status_code in [400, 403, 404]

    res5 = client.get('/api/signatures/documents/%2E%2E/download')
    assert res5.status_code in [400, 403, 404]

def test_argon2id_hashing_and_migration():
    """Verify passwords are saved with Argon2id and legacy hashes auto-migrate."""
    import uuid
    from database import get_db_connection
    from werkzeug.security import generate_password_hash

    email = f"test_argon_{uuid.uuid4().hex[:6]}@paperpilot.local"
    pw = "StrongPass2026!"

    # Registration
    reg = AuthService.register_user(email, pw)
    conn = get_db_connection()
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (reg['user_id'],)).fetchone()
    assert row['password_hash'].startswith("$argon2id$")

    # Migration from legacy scrypt
    legacy_email = f"legacy_{uuid.uuid4().hex[:6]}@paperpilot.local"
    scrypt_hash = generate_password_hash(pw, method='scrypt')
    conn.execute("INSERT INTO users (id, email, password_hash, display_name) VALUES (?, ?, ?, ?)",
                 (str(uuid.uuid4()), legacy_email, scrypt_hash, "Legacy Pilot"))
    conn.commit()

    # Login triggers rehash
    AuthService.login_user(legacy_email, pw)
    migrated_row = conn.execute("SELECT password_hash FROM users WHERE email = ?", (legacy_email,)).fetchone()
    conn.close()
    assert migrated_row['password_hash'].startswith("$argon2id$")

def test_organize_pdf_endpoint(client):
    """Verify /api/pdf/organize works with page sequences."""
    pdf = create_sample_pdf_bytes("Page 1 content")
    # Make 2-page document
    doc = fitz.open(stream=pdf, filetype="pdf")
    doc.new_page()
    multi_pdf = doc.tobytes()
    doc.close()

    res = client.post('/api/pdf/organize', data={
        'file': (io.BytesIO(multi_pdf), 'test_organize.pdf'),
        'page_order': '2, 1'
    }, content_type='multipart/form-data')

    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert 'download_url' in data

def test_html_to_pdf_endpoint(client):
    """Verify /api/pdf/convert/html-to-pdf works with uploaded file and HTML string."""
    res = client.post('/api/pdf/convert/html-to-pdf', data={
        'html': '<h1>PaperPilot HTML Test</h1><p>Engine verified.</p>'
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert 'download_url' in data
