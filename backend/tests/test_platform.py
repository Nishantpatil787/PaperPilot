import io
import json
import pytest
import fitz
from app import app
from database import init_db
from services.billing_service import BillingService

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    init_db()

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def create_sample_pdf_bytes(text="PaperPilot Platform Test Document"):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(72, 100), text, fontsize=16)
    page.insert_text(fitz.Point(72, 140), "Second paragraph for testing workflows and signatures.", fontsize=11)
    b = doc.tobytes()
    doc.close()
    return b

# 1. AUTH TESTS
def test_auth_register_and_login(client):
    email = "pilot@paperpilot.local"
    pwd = "SecretPassword123!"

    # Register
    reg_resp = client.post("/api/auth/register", json={
        "email": email,
        "password": pwd,
        "display_name": "Captain Pilot",
        "country": "IN",
        "timezone": "Asia/Kolkata"
    })
    # Could be 201 or 400 if already registered from earlier run
    assert reg_resp.status_code in [201, 400]

    # Login
    login_resp = client.post("/api/auth/login", json={
        "email": email,
        "password": pwd
    })
    assert login_resp.status_code == 200
    data = login_resp.get_json()
    assert data["success"] is True
    assert "token" in data["user"]
    token = data["user"]["token"]

    # Profile me
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.get_json()
    assert me_data["user"]["email"] == email
    assert "subscription" in me_data["user"]

    # Update profile
    up_resp = client.put("/api/auth/profile", json={"display_name": "Chief Pilot"}, headers={"Authorization": f"Bearer {token}"})
    assert up_resp.status_code == 200

# 2. TEAMS TESTS
def test_teams_flow(client):
    # Register owner and invitee
    owner_email = "owner_team@paperpilot.local"
    member_email = "member_team@paperpilot.local"
    pwd = "TeamPassword123!"

    client.post("/api/auth/register", json={"email": owner_email, "password": pwd, "display_name": "Team Owner"})
    client.post("/api/auth/register", json={"email": member_email, "password": pwd, "display_name": "Team Member"})

    login_owner = client.post("/api/auth/login", json={"email": owner_email, "password": pwd}).get_json()
    owner_token = login_owner["user"]["token"]

    login_member = client.post("/api/auth/login", json={"email": member_email, "password": pwd}).get_json()
    member_token = login_member["user"]["token"]

    # Owner creates team
    create_resp = client.post("/api/teams", json={"name": "Aviation Document Squad"}, headers={"Authorization": f"Bearer {owner_token}"})
    assert create_resp.status_code == 201
    team_id = create_resp.get_json()["team"]["team_id"]

    # Owner invites member
    inv_resp = client.post(f"/api/teams/{team_id}/invite", json={"email": member_email, "role": "admin"}, headers={"Authorization": f"Bearer {owner_token}"})
    assert inv_resp.status_code == 201
    invite_token = inv_resp.get_json()["invitation"]["invite_token"]

    # Member accepts invitation
    join_resp = client.post("/api/teams/join", json={"token": invite_token}, headers={"Authorization": f"Bearer {member_token}"})
    assert join_resp.status_code == 200

    # Get team members
    members_resp = client.get(f"/api/teams/{team_id}/members", headers={"Authorization": f"Bearer {owner_token}"})
    assert members_resp.status_code == 200
    members_data = members_resp.get_json()
    assert len(members_data["members"]) == 2

# 3. WORKFLOWS TESTS
def test_workflows_chaining(client):
    login = client.post("/api/auth/login", json={"email": "pilot@paperpilot.local", "password": "SecretPassword123!"}).get_json()
    token = login["user"]["token"]

    # Get supported steps
    steps_resp = client.get("/api/workflows/steps")
    assert steps_resp.status_code == 200
    assert "compress" in steps_resp.get_json()["steps"]

    # Create multi-step workflow: Compress -> Rotate -> Convert to Word
    wf_resp = client.post("/api/workflows", json={
        "name": "Optimize, Rotate & Convert Pipeline",
        "description": "Chained processing from PDF to Word doc",
        "steps": [
            {"type": "compress", "params": {"level": "medium"}},
            {"type": "rotate", "params": {"rotation": 90, "pages": "all"}},
            {"type": "pdf_to_word", "params": {}}
        ]
    }, headers={"Authorization": f"Bearer {token}"})
    assert wf_resp.status_code == 201
    wf_id = wf_resp.get_json()["workflow"]["id"]

    # Execute workflow with sample PDF
    pdf_bytes = create_sample_pdf_bytes("Workflow Chaining Test Document")
    exec_resp = client.post("/api/workflows/execute", data={
        "workflow_id": wf_id,
        "file": (io.BytesIO(pdf_bytes), "pipeline_test.pdf")
    }, content_type="multipart/form-data", headers={"Authorization": f"Bearer {token}"})

    assert exec_resp.status_code == 200
    exec_data = exec_resp.get_json()
    assert exec_data["status"] == "completed"
    assert exec_data["steps_completed"] == 3
    assert exec_data["output_filename"].endswith(".docx")

    # Download output
    dl_url = exec_data["download_url"]
    dl_resp = client.get(dl_url)
    assert dl_resp.status_code == 200
    assert len(dl_resp.data) > 0

# 4. SIGNATURES & AUDIT CERTIFICATE TESTS
def test_signatures_and_audit(client):
    login = client.post("/api/auth/login", json={"email": "pilot@paperpilot.local", "password": "SecretPassword123!"}).get_json()
    token = login["user"]["token"]

    # Save a typed/drawn signature
    dummy_data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    sig_resp = client.post("/api/signatures", json={
        "name": "Captain Pilot Primary Signature",
        "sig_type": "draw",
        "data_url": dummy_data_url,
        "is_default": True
    }, headers={"Authorization": f"Bearer {token}"})
    assert sig_resp.status_code == 201
    sig_id = sig_resp.get_json()["signature"]["id"]

    # List saved signatures
    list_resp = client.get("/api/signatures", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    assert len(list_resp.get_json()["signatures"]) >= 1

    # Sign document with audit trail
    pdf_bytes = create_sample_pdf_bytes("Contract Agreement for Aircraft Charter")
    sign_doc_resp = client.post("/api/signatures/sign-document", data={
        "file": (io.BytesIO(pdf_bytes), "charter_agreement.pdf"),
        "title": "Aircraft Charter Contract 2026",
        "signature_id": sig_id,
        "page": 1,
        "x": 100,
        "y": 200,
        "width": 140,
        "height": 50
    }, content_type="multipart/form-data", headers={"Authorization": f"Bearer {token}"})

    assert sign_doc_resp.status_code == 200
    sign_data = sign_doc_resp.get_json()
    assert sign_data["status"] == "signed"
    assert "document_hash" in sign_data
    assert "audit" in sign_data
    assert sign_data["audit"]["original_sha256"] != sign_data["audit"]["final_sha256"]

    # Download signed document and verify audit certificate page was attached
    dl_resp = client.get(sign_data["download_url"])
    assert dl_resp.status_code == 200
    doc = fitz.open(stream=dl_resp.data, filetype="pdf")
    # Original has 1 page; after signing and audit certificate attachment, it should have 2 pages
    assert len(doc) == 2
    # Verify certificate page text
    cert_text = doc[1].get_text()
    assert "PaperPilot Document Signing Certificate" in cert_text
    doc.close()

# 5. BILLING & RAZORPAY TESTS
def test_billing_and_razorpay(client):
    login = client.post("/api/auth/login", json={"email": "pilot@paperpilot.local", "password": "SecretPassword123!"}).get_json()
    token = login["user"]["token"]

    # Get plans
    plans_resp = client.get("/api/billing/plans")
    assert plans_resp.status_code == 200
    plans = plans_resp.get_json()["plans"]
    assert any(p["id"] == "premium_monthly" for p in plans)
    assert any(p["id"] == "premium_annual" for p in plans)

    # Verify plan amounts from server-side config (underpayment prevention)
    monthly = next(p for p in plans if p["id"] == "premium_monthly")
    annual  = next(p for p in plans if p["id"] == "premium_annual")
    assert monthly["amount_paise"] == 29900
    assert monthly["price_inr"]    == 299
    assert annual["amount_paise"]  == 249900
    assert annual["price_inr"]     == 2499

    # Check current subscription
    sub_resp = client.get("/api/billing/subscription", headers={"Authorization": f"Bearer {token}"})
    assert sub_resp.status_code == 200

    # Create Razorpay order for premium_monthly (₹299)
    order_resp = client.post("/api/billing/order", json={"plan_id": "premium_monthly"}, headers={"Authorization": f"Bearer {token}"})
    assert order_resp.status_code == 200
    order_data = order_resp.get_json()["order"]
    order_id = order_data["order_id"]
    assert order_data["amount"] == 29900
    assert order_data["currency"] == "INR"
    assert order_data["plan_id"] == "premium_monthly"

    # Use a unique payment_id to avoid duplicate rejection across test runs
    import uuid as _uuid
    payment_id = f"pay_platform_test_{_uuid.uuid4().hex[:10]}"
    valid_sig = BillingService.generate_sandbox_signature(order_id, payment_id)

    # Verify payment — must succeed and return correct plan
    verify_resp = client.post("/api/billing/verify", json={
        "order_id": order_id,
        "payment_id": payment_id,
        "signature": valid_sig,
        "plan_id": "premium_monthly"
    }, headers={"Authorization": f"Bearer {token}"})
    assert verify_resp.status_code == 200, verify_resp.get_data(as_text=True)
    ver_data = verify_resp.get_json()
    assert ver_data["success"] is True
    assert ver_data["plan_id"] == "premium_monthly"
    assert ver_data["amount_inr"] == 299

    # Subscription should now be active premium
    sub_after = client.get("/api/billing/subscription", headers={"Authorization": f"Bearer {token}"}).get_json()["subscription"]
    assert sub_after["is_premium"] is True
    assert sub_after["plan_id"] == "premium_monthly"

    # Check billing history — should record correct amount
    hist_resp = client.get("/api/billing/history", headers={"Authorization": f"Bearer {token}"})
    assert hist_resp.status_code == 200
    txs = hist_resp.get_json()["transactions"]
    assert len(txs) >= 1
    successful = [t for t in txs if t["status"] == "success"]
    assert len(successful) >= 1
    assert successful[0]["amount_inr"] == 299.0
    assert successful[0]["plan_id"] == "premium_monthly"
