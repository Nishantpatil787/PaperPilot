import io
import os
import uuid
import zipfile
import pytest
import pymupdf as fitz
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app import app
from database import get_db, init_db
from services.pdf_convert import PDFConvertService
from services.pdf_edit import PDFEditService
from services.billing_service import BillingService, canonicalize_plan_id
from services.team_service import TeamService
from services.workflow_service import WorkflowService

@pytest.fixture
def client():
    app.config['TESTING'] = True
    init_db()
    with app.test_client() as c:
        yield c

def create_sample_pdf(num_pages=2, text="PaperPilot Test Page"):
    doc = fitz.open()
    for i in range(num_pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text(fitz.Point(72, 100), f"{text} {i+1}", fontsize=14)
    bio = io.BytesIO()
    doc.save(bio)
    doc.close()
    bio.seek(0)
    return bio

def test_pdf_to_text_and_jpg_conversions(tmp_path):
    pdf_bytes = create_sample_pdf(num_pages=2, text="Extractable Content")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(pdf_bytes.getvalue())

    # 1. Text extraction
    txt_path = tmp_path / "extracted.txt"
    PDFConvertService.pdf_to_text(pdf_path, txt_path)
    assert txt_path.exists()
    content = txt_path.read_text(encoding="utf-8")
    assert "--- Page 1 ---" in content
    assert "Extractable Content 1" in content
    assert "--- Page 2 ---" in content

    # 2. JPG ZIP conversion
    zip_path = tmp_path / "pages.zip"
    PDFConvertService.pdf_to_jpg(pdf_path, zip_path)
    assert zip_path.exists()
    with zipfile.ZipFile(str(zip_path), 'r') as z:
        names = z.namelist()
        assert "page_1.jpg" in names
        assert "page_2.jpg" in names

def test_watermark_layering_and_rotation(tmp_path):
    pdf_bytes = create_sample_pdf(num_pages=1, text="Foreground Document Text")
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(pdf_bytes.getvalue())

    # Background layer watermark
    out_bg = tmp_path / "doc_bg.pdf"
    PDFEditService.watermark_pdf(pdf_path, out_bg, text="SECRET_BG", rotation=45, overlay=False)
    assert out_bg.exists()
    doc_bg = fitz.open(str(out_bg))
    assert len(doc_bg) == 1
    text_bg = doc_bg[0].get_text()
    assert "SECRET_BG" in text_bg
    doc_bg.close()

    # Foreground layer watermark
    out_fg = tmp_path / "doc_fg.pdf"
    PDFEditService.watermark_pdf(pdf_path, out_fg, text="CONFIDENTIAL_FG", rotation=0, overlay=True)
    assert out_fg.exists()
    doc_fg = fitz.open(str(out_fg))
    text_fg = doc_fg[0].get_text()
    assert "CONFIDENTIAL_FG" in text_fg
    doc_fg.close()

def test_sign_pdf_coordinate_mapping(tmp_path):
    pdf_bytes = create_sample_pdf(num_pages=1, text="Agreement Page")
    pdf_path = tmp_path / "agreement.pdf"
    pdf_path.write_bytes(pdf_bytes.getvalue())

    # Create dummy signature data URI
    import base64
    from PIL import Image as PILImage
    sig_img = PILImage.new("RGBA", (100, 40), color=(0, 0, 128, 255))
    bio = io.BytesIO()
    sig_img.save(bio, format="PNG")
    b64_sig = "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode()

    out_signed = tmp_path / "agreement_signed.pdf"
    PDFEditService.sign_pdf(pdf_path, out_signed, b64_sig, page_num=1, x=150, y=300, width=120, height=50)
    assert out_signed.exists()
    doc = fitz.open(str(out_signed))
    assert len(doc) == 1
    imgs = doc[0].get_images()
    assert len(imgs) >= 1
    doc.close()

def test_team_seat_limits_enforced(client):
    tag = uuid.uuid4().hex[:6]
    owner_email = f"owner_{tag}@paperpilot.local"
    m1_email = f"member1_{tag}@paperpilot.local"
    m2_email = f"member2_{tag}@paperpilot.local"
    pwd = "StrongPassword123!"

    # Register owner & members
    client.post("/api/auth/register", json={"email": owner_email, "password": pwd, "display_name": "Free Owner"})
    client.post("/api/auth/register", json={"email": m1_email, "password": pwd, "display_name": "Member 1"})
    client.post("/api/auth/register", json={"email": m2_email, "password": pwd, "display_name": "Member 2"})

    login_owner = client.post("/api/auth/login", json={"email": owner_email, "password": pwd}).get_json()
    owner_token = login_owner["user"]["token"]

    # Create team
    res = client.post("/api/teams", json={"name": "Limited Squad"}, headers={"Authorization": f"Bearer {owner_token}"})
    assert res.status_code == 201
    team_id = res.get_json()["team"]["team_id"]

    # First invite should succeed (reaches 2 seats: owner + 1 invite)
    inv1 = client.post(f"/api/teams/{team_id}/invite", json={"email": m1_email}, headers={"Authorization": f"Bearer {owner_token}"})
    assert inv1.status_code == 201

    # Second invite should be rejected because Free tier trial allows 2 seats max
    inv2 = client.post(f"/api/teams/{team_id}/invite", json={"email": m2_email}, headers={"Authorization": f"Bearer {owner_token}"})
    assert inv2.status_code == 400
    assert "seat limit reached" in inv2.get_json()["error"].lower()

def test_subscription_expiry_autodowngrade(client):
    tag = uuid.uuid4().hex[:6]
    user_email = f"expiring_{tag}@paperpilot.local"
    pwd = "StrongPassword123!"

    reg = client.post("/api/auth/register", json={"email": user_email, "password": pwd, "display_name": "Expiring User"}).get_json()
    assert "user" in reg
    user_id = reg["user"]["user_id"]

    # Artificially set subscription to expired premium_monthly
    past_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    conn = get_db()
    conn.execute("""
        UPDATE subscriptions
        SET plan = 'premium_monthly', status = 'active', current_period_end = ?
        WHERE user_id = ?
    """, (past_date, user_id))
    conn.commit()
    conn.close()

    # Query subscription via BillingService
    sub = BillingService.get_user_subscription(user_id)
    # Should have auto-downgraded to free!
    assert sub["plan_id"] == "free"
    assert sub["is_premium"] is False
    assert sub["current_period_end"] is None

def test_daily_usage_and_size_limits():
    # 1. Size check: 30MB exceeds free tier 25MB limit
    thirty_mb = 30 * 1024 * 1024
    allowed, err_msg, code = BillingService.check_and_record_usage(user_id=None, file_size_bytes=thirty_mb)
    assert allowed is False
    assert code == 413
    assert "exceeds" in err_msg.lower()

    # 2. Daily conversion counter for fresh free user
    conn = get_db()
    uid = f"test_quota_{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO users (id, email, password_hash, display_name) VALUES (?, ?, 'hash', 'Quota User')", (uid, f"{uid}@local.test"))
    conn.execute("INSERT INTO subscriptions (id, user_id, plan, status) VALUES (?, ?, 'free', 'active')", (f"sub_{uid}", uid))
    conn.commit()
    conn.close()

    # Perform 15 allowed operations
    for _ in range(15):
        ok, _, _ = BillingService.check_and_record_usage(user_id=uid, file_size_bytes=1024)
        assert ok is True

    # 16th operation must fail with 429
    ok, err_msg, code = BillingService.check_and_record_usage(user_id=uid, file_size_bytes=1024)
    assert ok is False
    assert code == 429
    assert "daily limit reached" in err_msg.lower()

def test_workflow_execution_and_download_with_spaces(client):
    tag = uuid.uuid4().hex[:6]
    user_email = f"wf_tester_{tag}@paperpilot.local"
    pwd = "StrongPassword123!"

    client.post("/api/auth/register", json={"email": user_email, "password": pwd, "display_name": "WF Tester"})
    login = client.post("/api/auth/login", json={"email": user_email, "password": pwd}).get_json()
    token = login["user"]["token"]

    # PDF file with spaces and parentheses in filename
    pdf_bytes = create_sample_pdf(num_pages=2, text="Workflow Content")
    data = {
        'file': (pdf_bytes, 'quarterly report (final draft).pdf'),
        'steps': '[{"type": "rotate", "params": {"rotation": 90, "pages": "all"}}, {"type": "watermark", "params": {"text": "AUDITED"}}]'
    }

    res = client.post('/api/workflows/execute', data=data, content_type='multipart/form-data', headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    res_data = res.get_json()
    assert res_data["status"] == "completed"
    exec_id = res_data["execution_id"]
    download_url = res_data["download_url"]

    # Download via canonical route
    down1 = client.get(download_url)
    assert down1.status_code == 200
    assert len(down1.data) > 0

    # Download via job registry endpoint
    down2 = client.get(f"/api/jobs/{exec_id}/download")
    assert down2.status_code == 200
    assert len(down2.data) > 0
