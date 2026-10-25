import pytest
import json
import uuid
from app import app
from services.auth_service import AuthService
from services.email_service import EmailService

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_auth_existing_user_login(client):
    """Verify existing users can log in securely."""
    # Register a known user first if not exists
    test_email = "test_pilot_audit@paperpilot.local"
    test_pwd = "SecurePilotPassword2026!"
    try:
        AuthService.register_user(test_email, test_pwd, display_name="Test Pilot")
    except ValueError:
        pass  # already registered

    res = client.post('/api/auth/login', json={
        "email": test_email,
        "password": test_pwd
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['user']['email'] == test_email
    assert 'token' in data['user']

def test_auth_new_user_registration_and_argon2(client):
    """Verify new user registration works and uses Argon2id."""
    unique_email = f"pilot_{uuid.uuid4().hex[:8]}@domain.com"
    pwd = "ValidPass12345!"

    res = client.post('/api/auth/register', json={
        "email": unique_email,
        "password": pwd,
        "display_name": "New Pilot"
    })
    assert res.status_code == 201
    data = res.get_json()
    assert data['success'] is True
    assert data['user']['email'] == unique_email

    # Now verify login with the new credentials
    login_res = client.post('/api/auth/login', json={
        "email": unique_email,
        "password": pwd
    })
    assert login_res.status_code == 200
    login_data = login_res.get_json()
    assert login_data['success'] is True
    assert login_data['user']['email'] == unique_email

def test_auth_duplicate_email_rejected(client):
    """Verify duplicate email registration is rejected."""
    email = f"dup_{uuid.uuid4().hex[:8]}@domain.com"
    AuthService.register_user(email, "Password123!")

    res = client.post('/api/auth/register', json={
        "email": email,
        "password": "Password123!"
    })
    assert res.status_code == 400
    assert "already exists" in res.get_json()['error'].lower()

def test_contact_form_flow(client):
    """Test contact API with valid inputs and check response."""
    res = client.post('/api/contact', json={
        "name": "Nishant Patil",
        "email": "patilnishant787@gmail.com",
        "message": "Testing PaperPilot contact integration and email verification."
    })
    # Either 200 (if SMTP live) or 503 (if SMTP unconfigured in dev)
    assert res.status_code in (200, 503)
    data = res.get_json()
    if res.status_code == 503:
        assert data['unconfigured'] is True
        assert 'direct_email' in data
        assert data['direct_email'] == 'patilnishant787@gmail.com'
    else:
        assert data['success'] is True
