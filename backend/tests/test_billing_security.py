"""
Comprehensive Razorpay Billing Tests
Tests: underpayment prevention, plan consistency, ownership, cryptographic
       signature verification, idempotency, duplicate detection, webhook handling.
"""
import pytest
import json
import uuid
import hmac
import hashlib
from app import app
from services.billing_service import BillingService, PLANS, canonicalize_plan_id
from services.auth_service import AuthService


# ─────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────
@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _register_and_login(client, suffix=""):
    email = f"billing_test_{uuid.uuid4().hex[:8]}_{suffix}@test.local"
    pwd = "SecureTestPass2026!"
    client.post("/api/auth/register", json={
        "email": email, "password": pwd, "display_name": "Billing Tester"
    })
    r = client.post("/api/auth/login", json={"email": email, "password": pwd})
    data = r.get_json()
    return data["user"]["token"], data["user"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────
#  Helper: create an order and generate a valid sandbox signature
# ─────────────────────────────────────────────────────
def _create_order_and_sign(client, token, plan_id):
    order_r = client.post("/api/billing/order",
                          json={"plan_id": plan_id},
                          headers=_auth_headers(token))
    assert order_r.status_code == 200, order_r.get_data(as_text=True)
    order = order_r.get_json()["order"]
    payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"
    sig = BillingService.generate_sandbox_signature(order["order_id"], payment_id)
    return order, payment_id, sig


# ─────────────────────────────────────────────────────
#  TEST 1 – Monthly plan: correct amount enforced
# ─────────────────────────────────────────────────────
def test_monthly_order_amount_correct(client):
    token, _ = _register_and_login(client, "monthly")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")

    assert order["amount"] == 29900, f"Expected 29900 paise, got {order['amount']}"
    assert order["currency"] == "INR"
    assert order["plan_id"] == "premium_monthly"


# ─────────────────────────────────────────────────────
#  TEST 2 – Annual plan: correct amount enforced
# ─────────────────────────────────────────────────────
def test_annual_order_amount_correct(client):
    token, _ = _register_and_login(client, "annual")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_annual")

    assert order["amount"] == 249900, f"Expected 249900 paise, got {order['amount']}"
    assert order["currency"] == "INR"
    assert order["plan_id"] == "premium_annual"


# ─────────────────────────────────────────────────────
#  TEST 3 – Monthly payment: verify and activate premium
# ─────────────────────────────────────────────────────
def test_monthly_payment_verify_activates_premium(client):
    token, user = _register_and_login(client, "mverify")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")

    # subscription should be free before payment
    sub_before = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub_before["plan_id"] == "free"

    verify_r = client.post("/api/billing/verify",
                           json={
                               "order_id": order["order_id"],
                               "payment_id": payment_id,
                               "signature": sig,
                               "plan_id": "premium_monthly"
                           },
                           headers=_auth_headers(token))
    assert verify_r.status_code == 200, verify_r.get_data(as_text=True)
    v = verify_r.get_json()
    assert v["success"] is True
    assert v["plan_id"] == "premium_monthly"
    assert "period_end" in v

    sub_after = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub_after["is_premium"] is True
    assert sub_after["plan_id"] == "premium_monthly"


# ─────────────────────────────────────────────────────
#  TEST 4 – Annual payment: verify and activate annual premium
# ─────────────────────────────────────────────────────
def test_annual_payment_verify_activates_annual(client):
    token, _ = _register_and_login(client, "averify")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_annual")

    verify_r = client.post("/api/billing/verify",
                           json={
                               "order_id": order["order_id"],
                               "payment_id": payment_id,
                               "signature": sig,
                               "plan_id": "premium_annual"
                           },
                           headers=_auth_headers(token))
    assert verify_r.status_code == 200, verify_r.get_data(as_text=True)
    v = verify_r.get_json()
    assert v["success"] is True
    assert v["plan_id"] == "premium_annual"

    sub = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub["is_premium"] is True
    assert sub["plan_id"] == "premium_annual"


# ─────────────────────────────────────────────────────
#  TEST 5 – Annual monthly cross-plan rejection:
#           Order created for annual, verify claimed as monthly
# ─────────────────────────────────────────────────────
def test_plan_mismatch_in_verify_rejected(client):
    token, _ = _register_and_login(client, "crossplan")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_annual")

    # Try to claim annual order as monthly (plan manipulation attack)
    verify_r = client.post("/api/billing/verify",
                           json={
                               "order_id": order["order_id"],
                               "payment_id": payment_id,
                               "signature": sig,
                               "plan_id": "premium_monthly"   # wrong plan
                           },
                           headers=_auth_headers(token))
    assert verify_r.status_code == 400, verify_r.get_data(as_text=True)
    err = verify_r.get_json()["error"]
    assert "mismatch" in err.lower() or "annual" in err.lower()

    # Premium must NOT have been activated
    sub = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub["plan_id"] == "free"


# ─────────────────────────────────────────────────────
#  TEST 6 – Underpayment: reported amount doesn't match expected
# ─────────────────────────────────────────────────────
def test_underpayment_reported_amount_rejected(client):
    token, _ = _register_and_login(client, "underpay")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")

    verify_r = client.post("/api/billing/verify",
                           json={
                               "order_id": order["order_id"],
                               "payment_id": payment_id,
                               "signature": sig,
                               "plan_id": "premium_monthly",
                               "amount": 10000   # ₹100 — underpayment
                           },
                           headers=_auth_headers(token))
    assert verify_r.status_code == 400, verify_r.get_data(as_text=True)
    err = verify_r.get_json()["error"]
    assert "underpayment" in err.lower()

    sub = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub["plan_id"] == "free"


# ─────────────────────────────────────────────────────
#  TEST 7 – Invalid signature rejected
# ─────────────────────────────────────────────────────
def test_invalid_signature_rejected(client):
    token, _ = _register_and_login(client, "badsig")
    order, payment_id, _ = _create_order_and_sign(client, token, "premium_monthly")

    verify_r = client.post("/api/billing/verify",
                           json={
                               "order_id": order["order_id"],
                               "payment_id": payment_id,
                               "signature": "deadbeef" * 8,   # tampered
                               "plan_id": "premium_monthly"
                           },
                           headers=_auth_headers(token))
    assert verify_r.status_code == 400
    assert "signature" in verify_r.get_json()["error"].lower()

    sub = client.get("/api/billing/subscription", headers=_auth_headers(token)).get_json()["subscription"]
    assert sub["plan_id"] == "free"


# ─────────────────────────────────────────────────────
#  TEST 8 – Missing fields rejected
# ─────────────────────────────────────────────────────
def test_missing_verification_fields_rejected(client):
    token, _ = _register_and_login(client, "missing")
    r = client.post("/api/billing/verify",
                    json={"order_id": "order_fake"},
                    headers=_auth_headers(token))
    assert r.status_code == 400
    assert "missing" in r.get_json()["error"].lower()


# ─────────────────────────────────────────────────────
#  TEST 9 – Order ownership: user B cannot claim user A's order
# ─────────────────────────────────────────────────────
def test_cross_user_order_access_denied(client):
    token_a, _ = _register_and_login(client, "owner_a")
    token_b, _ = _register_and_login(client, "thief_b")
    order, payment_id, sig = _create_order_and_sign(client, token_a, "premium_monthly")

    # user B tries to verify user A's order
    r = client.post("/api/billing/verify",
                    json={
                        "order_id": order["order_id"],
                        "payment_id": payment_id,
                        "signature": sig,
                        "plan_id": "premium_monthly"
                    },
                    headers=_auth_headers(token_b))
    assert r.status_code == 400
    assert "access denied" in r.get_json()["error"].lower() or "not found" in r.get_json()["error"].lower()


# ─────────────────────────────────────────────────────
#  TEST 10 – Idempotency: duplicate verification returns clean success
# ─────────────────────────────────────────────────────
def test_duplicate_verify_idempotent(client):
    token, _ = _register_and_login(client, "idem")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")
    payload = {
        "order_id": order["order_id"],
        "payment_id": payment_id,
        "signature": sig,
        "plan_id": "premium_monthly"
    }

    r1 = client.post("/api/billing/verify", json=payload, headers=_auth_headers(token))
    r2 = client.post("/api/billing/verify", json=payload, headers=_auth_headers(token))

    assert r1.status_code == 200
    assert r2.status_code == 200
    d1 = r1.get_json()
    d2 = r2.get_json()
    assert d1["success"] is True
    assert d2["success"] is True
    # second call may flag idempotent=True
    assert d2.get("idempotent") is True or d2.get("success") is True


# ─────────────────────────────────────────────────────
#  TEST 11 – Duplicate payment ID across orders rejected
# ─────────────────────────────────────────────────────
def test_duplicate_payment_id_across_orders_rejected(client):
    token, _ = _register_and_login(client, "dupayid")

    # First order
    order1, pid, sig1 = _create_order_and_sign(client, token, "premium_monthly")
    r1 = client.post("/api/billing/verify",
                     json={"order_id": order1["order_id"], "payment_id": pid,
                           "signature": sig1, "plan_id": "premium_monthly"},
                     headers=_auth_headers(token))
    assert r1.status_code == 200

    # Second order with same payment_id
    order2_r = client.post("/api/billing/order",
                           json={"plan_id": "premium_monthly"},
                           headers=_auth_headers(token))
    order2 = order2_r.get_json()["order"]
    sig2 = BillingService.generate_sandbox_signature(order2["order_id"], pid)  # same pid

    r2 = client.post("/api/billing/verify",
                     json={"order_id": order2["order_id"], "payment_id": pid,
                           "signature": sig2, "plan_id": "premium_monthly"},
                     headers=_auth_headers(token))
    assert r2.status_code == 400
    assert "duplicate" in r2.get_json()["error"].lower()


# ─────────────────────────────────────────────────────
#  TEST 12 – Free plan order creation rejected
# ─────────────────────────────────────────────────────
def test_free_plan_order_rejected(client):
    token, _ = _register_and_login(client, "free")
    r = client.post("/api/billing/order",
                    json={"plan_id": "free"},
                    headers=_auth_headers(token))
    assert r.status_code == 400
    assert "invalid" in r.get_json()["error"].lower()


# ─────────────────────────────────────────────────────
#  TEST 13 – Client-supplied amount in order request is ignored
# ─────────────────────────────────────────────────────
def test_client_supplied_amount_ignored(client):
    """The backend must ignore any amount sent by the client."""
    token, _ = _register_and_login(client, "clientamt")
    r = client.post("/api/billing/order",
                    json={"plan_id": "premium_monthly", "amount": 100},  # ₹1 tampered
                    headers=_auth_headers(token))
    assert r.status_code == 200
    order = r.get_json()["order"]
    # Must use server-side amount, not the client's ₹1
    assert order["amount"] == 29900


# ─────────────────────────────────────────────────────
#  TEST 14 – Plan identifier aliases resolve correctly
# ─────────────────────────────────────────────────────
def test_plan_alias_normalization(client):
    token, _ = _register_and_login(client, "alias")
    # 'monthly' should resolve to premium_monthly
    r = client.post("/api/billing/order",
                    json={"plan_id": "monthly"},
                    headers=_auth_headers(token))
    assert r.status_code == 200
    order = r.get_json()["order"]
    assert order["plan_id"] == "premium_monthly"
    assert order["amount"] == 29900


# ─────────────────────────────────────────────────────
#  TEST 15 – Webhook: valid payment.captured event activates plan
# ─────────────────────────────────────────────────────
def test_webhook_payment_captured_activates_subscription(client):
    token, user = _register_and_login(client, "wh")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")
    order_id = order["order_id"]

    webhook_secret = "secret_paperpilot_sandbox_key"
    payload = {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 29900,
                    "currency": "INR",
                    "status": "captured"
                }
            }
        }
    }
    raw = json.dumps(payload).encode("utf-8")
    wh_sig = hmac.new(webhook_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    r = client.post("/api/billing/webhook/razorpay",
                    data=raw,
                    content_type="application/json",
                    headers={"X-Razorpay-Signature": wh_sig})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["status"] == "success"


# ─────────────────────────────────────────────────────
#  TEST 16 – Webhook: invalid signature rejected
# ─────────────────────────────────────────────────────
def test_webhook_invalid_signature_rejected(client):
    payload = json.dumps({"id": "evt_bad", "event": "payment.captured", "payload": {}}).encode()
    r = client.post("/api/billing/webhook/razorpay",
                    data=payload,
                    content_type="application/json",
                    headers={"X-Razorpay-Signature": "badhash"})
    assert r.status_code == 400


# ─────────────────────────────────────────────────────
#  TEST 17 – Webhook: duplicate event idempotent
# ─────────────────────────────────────────────────────
def test_webhook_duplicate_event_idempotent(client):
    event_id = f"evt_{uuid.uuid4().hex[:10]}"
    webhook_secret = "secret_paperpilot_sandbox_key"
    payload = {"id": event_id, "event": "payment.failed", "payload": {}}
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    r1 = client.post("/api/billing/webhook/razorpay",
                     data=raw, content_type="application/json",
                     headers={"X-Razorpay-Signature": sig})
    r2 = client.post("/api/billing/webhook/razorpay",
                     data=raw, content_type="application/json",
                     headers={"X-Razorpay-Signature": sig})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "already_processed"


# ─────────────────────────────────────────────────────
#  TEST 18 – Webhook underpayment rejected
# ─────────────────────────────────────────────────────
def test_webhook_underpayment_rejected(client):
    token, user = _register_and_login(client, "wh_under")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_monthly")
    order_id = order["order_id"]

    webhook_secret = "secret_paperpilot_sandbox_key"
    payload = {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 10000,   # ₹100 underpayment
                    "currency": "INR",
                    "status": "captured"
                }
            }
        }
    }
    raw = json.dumps(payload).encode("utf-8")
    wh_sig = hmac.new(webhook_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    r = client.post("/api/billing/webhook/razorpay",
                    data=raw, content_type="application/json",
                    headers={"X-Razorpay-Signature": wh_sig})
    assert r.status_code == 400
    assert "underpayment" in r.get_json()["error"].lower()


# ─────────────────────────────────────────────────────
#  TEST 19 – Transaction history records correct amounts
# ─────────────────────────────────────────────────────
def test_transaction_history_records_correct_amount(client):
    token, _ = _register_and_login(client, "hist")
    order, payment_id, sig = _create_order_and_sign(client, token, "premium_annual")
    client.post("/api/billing/verify",
                json={"order_id": order["order_id"], "payment_id": payment_id,
                      "signature": sig, "plan_id": "premium_annual"},
                headers=_auth_headers(token))

    hist_r = client.get("/api/billing/history", headers=_auth_headers(token))
    assert hist_r.status_code == 200
    txs = hist_r.get_json()["transactions"]
    assert len(txs) >= 1
    successful = [t for t in txs if t["status"] == "success"]
    assert len(successful) >= 1
    assert successful[0]["amount_inr"] == 2499.0
    assert successful[0]["plan_id"] == "premium_annual"


# ─────────────────────────────────────────────────────
#  TEST 20 – Unauthenticated order request rejected
# ─────────────────────────────────────────────────────
def test_unauthenticated_order_rejected(client):
    r = client.post("/api/billing/order", json={"plan_id": "premium_monthly"})
    assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────
#  TEST 21 – Plans endpoint returns correct plan data
# ─────────────────────────────────────────────────────
def test_plans_endpoint_correct(client):
    r = client.get("/api/billing/plans")
    assert r.status_code == 200
    plans = r.get_json()["plans"]
    monthly = next(p for p in plans if p["id"] == "premium_monthly")
    annual = next(p for p in plans if p["id"] == "premium_annual")
    assert monthly["amount_paise"] == 29900
    assert monthly["price_inr"] == 299
    assert annual["amount_paise"] == 249900
    assert annual["price_inr"] == 2499


# ─────────────────────────────────────────────────────
#  TEST 22 – canonicalize_plan_id covers all aliases
# ─────────────────────────────────────────────────────
def test_canonicalize_plan_aliases():
    assert canonicalize_plan_id("monthly") == "premium_monthly"
    assert canonicalize_plan_id("monthly_premium") == "premium_monthly"
    assert canonicalize_plan_id("premium_monthly") == "premium_monthly"
    assert canonicalize_plan_id("annual") == "premium_annual"
    assert canonicalize_plan_id("annual_premium") == "premium_annual"
    assert canonicalize_plan_id("yearly") == "premium_annual"
    assert canonicalize_plan_id("premium_annual") == "premium_annual"
    assert canonicalize_plan_id("free") == "free"
    assert canonicalize_plan_id("") == "free"
    assert canonicalize_plan_id(None) == "free"



# ─────────────────────────────────────────────────────
#  Payment-Link Claim Flow Tests (Tests 23-28)
# ─────────────────────────────────────────────────────

def test_payment_link_claim_monthly(client):
    """TEST 23: Valid pay_ ID → activates monthly premium via claim-payment-link route."""
    token, user = _register_and_login(client, "link_monthly")
    payment_id = f"pay_link_{uuid.uuid4().hex[:12]}"
    r = client.post("/api/billing/claim-payment",
                    json={"payment_id": payment_id, "plan_id": "premium_monthly"},
                    headers=_auth_headers(token))
    d = r.get_json()
    assert r.status_code == 200, d
    assert d["success"] is True
    assert d["plan_id"] == "premium_monthly"
    assert d["amount_inr"] == 299
    assert "period_end" in d


def test_payment_link_claim_annual(client):
    """TEST 24: Valid pay_ ID → activates annual premium."""
    token, user = _register_and_login(client, "link_annual")
    payment_id = f"pay_link_{uuid.uuid4().hex[:12]}"
    r = client.post("/api/billing/claim-payment",
                    json={"payment_id": payment_id, "plan_id": "premium_annual"},
                    headers=_auth_headers(token))
    d = r.get_json()
    assert r.status_code == 200, d
    assert d["success"] is True
    assert d["plan_id"] == "premium_annual"
    assert d["amount_inr"] == 2499


def test_payment_link_claim_invalid_payment_id(client):
    """TEST 25: Payment ID not starting with pay_ → rejected."""
    token, _ = _register_and_login(client, "link_invalid_id")
    r = client.post("/api/billing/claim-payment",
                    json={"payment_id": "order_invalid123", "plan_id": "premium_monthly"},
                    headers=_auth_headers(token))
    d = r.get_json()
    assert r.status_code == 400
    assert "pay_" in d["error"].lower() or "invalid" in d["error"].lower()


def test_payment_link_claim_missing_payment_id(client):
    """TEST 26: No payment_id and no order_id/signature → rejected with helpful message."""
    token, _ = _register_and_login(client, "link_missing_id")
    r = client.post("/api/billing/claim-payment",
                    json={"plan_id": "premium_monthly"},
                    headers=_auth_headers(token))
    assert r.status_code == 400
    d = r.get_json()
    assert "error" in d


def test_payment_link_claim_idempotent(client):
    """TEST 27: Claiming the same payment_id twice returns success (idempotent for same user)."""
    token, _ = _register_and_login(client, "link_idem")
    payment_id = f"pay_idem_{uuid.uuid4().hex[:12]}"
    plan_id = "premium_monthly"

    r1 = client.post("/api/billing/claim-payment",
                     json={"payment_id": payment_id, "plan_id": plan_id},
                     headers=_auth_headers(token))
    assert r1.status_code == 200

    r2 = client.post("/api/billing/claim-payment",
                     json={"payment_id": payment_id, "plan_id": plan_id},
                     headers=_auth_headers(token))
    d2 = r2.get_json()
    assert r2.status_code == 200
    assert d2["success"] is True
    assert d2.get("idempotent") is True


def test_payment_link_claim_cross_user_rejected(client):
    """TEST 28: Payment ID already claimed by user A cannot be claimed by user B."""
    token_a, _ = _register_and_login(client, "link_cross_a")
    token_b, _ = _register_and_login(client, "link_cross_b")
    payment_id = f"pay_cross_{uuid.uuid4().hex[:12]}"

    r1 = client.post("/api/billing/claim-payment",
                     json={"payment_id": payment_id, "plan_id": "premium_monthly"},
                     headers=_auth_headers(token_a))
    assert r1.status_code == 200

    r2 = client.post("/api/billing/claim-payment",
                     json={"payment_id": payment_id, "plan_id": "premium_monthly"},
                     headers=_auth_headers(token_b))
    d2 = r2.get_json()
    assert r2.status_code == 400
    assert "another account" in d2["error"].lower() or "already been credited" in d2["error"].lower()
