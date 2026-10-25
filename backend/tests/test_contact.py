import pytest
import json
from app import app
from services.email_service import EmailService, EmailValidationError, EmailNotConfiguredError

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_contact_status(client):
    res = client.get('/api/contact/status')
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert 'configured' in data
    assert 'recipient' in data
    assert data['recipient'] == 'patilnishant787@gmail.com'

def test_contact_validation_missing_fields(client):
    res = client.post('/api/contact', json={})
    assert res.status_code == 400
    assert "full name" in res.get_json()['error'].lower()

def test_contact_validation_invalid_email(client):
    res = client.post('/api/contact', json={
        "name": "Nishant Test",
        "email": "invalid-email-format",
        "message": "Hello PaperPilot team!"
    })
    assert res.status_code == 400
    assert "valid email" in res.get_json()['error'].lower()

def test_contact_honeypot_rejection(client):
    res = client.post('/api/contact', json={
        "name": "Spam Bot",
        "email": "bot@spam.com",
        "message": "Buy cheap viagra",
        "website_bot": "http://spam.org"
    })
    assert res.status_code == 400
    assert "automated submission" in res.get_json()['error'].lower()

def test_contact_unconfigured_smtp_graceful(client, monkeypatch):
    monkeypatch.setattr(EmailService, "is_configured", classmethod(lambda cls: False))
    res = client.post('/api/contact', json={
        "name": "Nishant Patil",
        "email": "visitor@example.com",
        "message": "Great job on PaperPilot platform!"
    })
    assert res.status_code == 503
    data = res.get_json()
    assert data['success'] is False
    assert data['unconfigured'] is True
    assert "patilnishant787@gmail.com" in data['direct_email']
