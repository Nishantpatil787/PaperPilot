import uuid
import time
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, g
from werkzeug.security import check_password_hash
from database import get_db_connection
import argon2
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_argon2_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16
)

def hash_password(password: str) -> str:
    """Hash password using OWASP-recommended Argon2id."""
    return _argon2_hasher.hash(password)

def verify_and_check_rehash(stored_hash: str, password: str) -> tuple[bool, bool]:
    """
    Verifies a password against stored_hash supporting Argon2id, legacy Werkzeug (scrypt/pbkdf2),
    and legacy plain SHA-256.
    Returns (is_valid, needs_rehash).
    """
    if not stored_hash or not password:
        return False, False

    # 1. Argon2 hash
    if stored_hash.startswith("$argon2"):
        try:
            is_valid = _argon2_hasher.verify(stored_hash, password)
            needs_rehash = _argon2_hasher.check_needs_rehash(stored_hash)
            return is_valid, needs_rehash
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False

    # 2. Legacy Werkzeug hash (scrypt or pbkdf2)
    if stored_hash.startswith("scrypt:") or stored_hash.startswith("pbkdf2:"):
        try:
            if check_password_hash(stored_hash, password):
                return True, True  # Valid; upgrade to Argon2id
        except Exception:
            pass

    # 3. Legacy plain SHA-256 (64 hex characters)
    if len(stored_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored_hash):
        if hashlib.sha256(password.encode("utf-8")).hexdigest().lower() == stored_hash.lower():
            return True, True

    # 4. Fallback check for any other Werkzeug format
    try:
        if check_password_hash(stored_hash, password):
            return True, True
    except Exception:
        pass

    return False, False

class AuthService:
    @staticmethod
    def register_user(email, password, display_name=None, country='IN', timezone='Asia/Kolkata'):
        email = email.strip().lower()
        if not email or '@' not in email:
            raise ValueError("Valid email address is required.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        display_name = display_name or email.split('@')[0].capitalize()
        user_id = str(uuid.uuid4())
        pwd_hash = hash_password(password)

        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name, country, timezone) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, pwd_hash, display_name, country, timezone)
            )
            # Default free subscription
            conn.execute(
                "INSERT INTO subscriptions (id, user_id, plan, status) VALUES (?, ?, 'free', 'active')",
                (str(uuid.uuid4()), user_id)
            )
            conn.commit()
        except Exception as e:
            conn.close()
            if "UNIQUE constraint" in str(e):
                raise ValueError("An account with this email already exists.")
            raise e

        conn.close()
        # Automatically generate session token
        token, expires_at = AuthService.create_session(user_id)
        return {
            "user_id": user_id,
            "email": email,
            "display_name": display_name,
            "token": token,
            "expires_at": expires_at,
            "plan": "free"
        }

    @staticmethod
    def login_user(email, password):
        email = email.strip().lower()
        conn = get_db_connection()
        row = conn.execute(
            """SELECT u.id, u.email, u.password_hash, u.display_name, u.avatar_url, s.plan, s.status as sub_status 
               FROM users u 
               LEFT JOIN subscriptions s ON u.id = s.user_id 
               WHERE u.email = ?""",
            (email,)
        ).fetchone()

        if not row:
            conn.close()
            raise ValueError("Invalid email or password.")

        is_valid, needs_rehash = verify_and_check_rehash(row['password_hash'], password)
        if not is_valid:
            conn.close()
            raise ValueError("Invalid email or password.")

        user_id = row['id']
        if needs_rehash:
            try:
                new_hash = hash_password(password)
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
                conn.commit()
            except Exception:
                pass

        token, expires_at = AuthService.create_session(user_id)
        user_data = {
            "user_id": user_id,
            "email": row['email'],
            "display_name": row['display_name'],
            "avatar_url": row['avatar_url'],
            "token": token,
            "expires_at": expires_at,
            "plan": row['plan'] or 'free',
            "subscription_status": row['sub_status'] or 'active'
        }
        conn.close()
        return user_data

    @staticmethod
    def create_session(user_id, days=30):
        token = secrets.token_hex(32)
        session_id = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, token, expires_at)
        )
        conn.commit()
        conn.close()
        return token, expires_at

    @staticmethod
    def get_user_by_token(token):
        if not token:
            return None
        conn = get_db_connection()
        row = conn.execute(
            """SELECT u.id, u.email, u.display_name, u.country, u.timezone, u.avatar_url, u.role, 
                      s.plan, s.status as sub_status, sess.expires_at
               FROM sessions sess
               JOIN users u ON sess.user_id = u.id
               LEFT JOIN subscriptions s ON u.id = s.user_id
               WHERE sess.token = ?""",
            (token,)
        ).fetchone()
        conn.close()

        if not row:
            return None
        
        # Check expiry
        try:
            exp = datetime.fromisoformat(row['expires_at'])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return None
        except Exception:
            return None

        return dict(row)

    @staticmethod
    def logout_token(token):
        conn = get_db_connection()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def update_profile(user_id, display_name=None, country=None, timezone=None, avatar_url=None):
        conn = get_db_connection()
        updates = []
        params = []
        if display_name:
            updates.append("display_name = ?")
            params.append(display_name.strip())
        if country:
            updates.append("country = ?")
            params.append(country.strip())
        if timezone:
            updates.append("timezone = ?")
            params.append(timezone.strip())
        if avatar_url is not None:
            updates.append("avatar_url = ?")
            params.append(avatar_url.strip())

        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        conn.close()
        return True

    @staticmethod
    def change_password(user_id, old_password, new_password):
        if len(new_password) < 6:
            raise ValueError("New password must be at least 6 characters.")
        conn = get_db_connection()
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            conn.close()
            raise ValueError("User not found.")

        is_valid, _ = verify_and_check_rehash(row['password_hash'], old_password)
        if not is_valid:
            conn.close()
            raise ValueError("Current password does not match.")

        new_hash = hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def delete_account(user_id):
        conn = get_db_connection()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = None
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
        elif 'auth_token' in request.args:
            token = request.args.get('auth_token')

        if not token:
            return jsonify({"error": "Authentication required. Please log in."}), 401

        user = AuthService.get_user_by_token(token)
        if not user:
            return jsonify({"error": "Session expired or invalid token. Please log in again."}), 401

        g.user = user
        return f(*args, **kwargs)
    return decorated
