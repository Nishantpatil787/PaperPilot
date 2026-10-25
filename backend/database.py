import sqlite3
import os
from pathlib import Path
from config import BASE_DIR, STORAGE_DIR

DB_PATH = STORAGE_DIR / "paperpilot.db"

def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

get_db = get_db_connection

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Users Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        country TEXT DEFAULT 'IN',
        timezone TEXT DEFAULT 'Asia/Kolkata',
        avatar_url TEXT,
        role TEXT DEFAULT 'user',
        is_verified INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 2. Sessions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 3. Password Resets
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 4. Teams Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teams (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        plan TEXT DEFAULT 'free',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 5. Team Members Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member', -- owner, admin, member
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(team_id, user_id),
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 6. Team Invitations Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS team_invitations (
        id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL,
        email TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        token TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        accepted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
    )
    """)

    # 7. Workflows Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workflows (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        team_id TEXT,
        name TEXT NOT NULL,
        description TEXT,
        steps_json TEXT NOT NULL, -- JSON list of ordered steps
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 8. Workflow Executions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workflow_executions (
        id TEXT PRIMARY KEY,
        workflow_id TEXT,
        user_id TEXT NOT NULL,
        status TEXT NOT NULL, -- running, completed, failed
        input_filename TEXT,
        output_filename TEXT,
        download_url TEXT,
        steps_completed INTEGER DEFAULT 0,
        total_steps INTEGER DEFAULT 0,
        log_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 9. Saved Signatures Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS saved_signatures (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        sig_type TEXT NOT NULL, -- draw, type, upload
        data_url TEXT NOT NULL,
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 10. Document Signing Audit Records
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS document_signings (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        original_file TEXT NOT NULL,
        signed_file TEXT,
        status TEXT DEFAULT 'signed', -- draft, sent, signed
        document_hash TEXT,
        audit_log_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 11. Subscriptions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id TEXT PRIMARY KEY,
        user_id TEXT UNIQUE NOT NULL,
        plan TEXT NOT NULL DEFAULT 'free', -- free, premium_monthly, premium_annual
        status TEXT NOT NULL DEFAULT 'active', -- active, past_due, cancelled
        gateway TEXT DEFAULT 'razorpay',
        gateway_subscription_id TEXT,
        current_period_end TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # 12. Payment Transactions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        payment_id TEXT,
        plan_id TEXT DEFAULT 'premium_monthly',
        amount INTEGER NOT NULL, -- amount in paise / cents
        currency TEXT DEFAULT 'INR',
        status TEXT NOT NULL, -- created, success, failed, refunded
        gateway TEXT DEFAULT 'razorpay',
        signature TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    # Ensure plan_id column exists for existing databases
    cursor.execute("PRAGMA table_info(transactions)")
    col_names = [col[1] for col in cursor.fetchall()]
    if "plan_id" not in col_names:
        cursor.execute("ALTER TABLE transactions ADD COLUMN plan_id TEXT DEFAULT 'premium_monthly'")

    # 13. Webhook Events Table (Idempotency)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS webhook_events (
        id TEXT PRIMARY KEY,
        event_id TEXT UNIQUE NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        processed INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 14. Daily Usage Tracking Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_usage (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        usage_date TEXT NOT NULL,
        operation_count INTEGER DEFAULT 0,
        UNIQUE(user_id, usage_date),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully at", DB_PATH)
