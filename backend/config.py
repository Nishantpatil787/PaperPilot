import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env in project root or backend
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")

STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_FOLDER = STORAGE_DIR / "uploads"
OUTPUT_FOLDER = STORAGE_DIR / "outputs"

# Ensure directories exist
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.getenv("SECRET_KEY", "paperpilot_production_key_4f89d31b8a92")
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB max request
FILE_RETENTION_SECONDS = 3600  # 1 hour retention for temporary files

# Razorpay Configuration
RAZORPAY_PAYMENT_LINK = os.getenv("RAZORPAY_PAYMENT_LINK", "https://razorpay.me/@nishantrajeshpatil").strip()
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip() or RAZORPAY_KEY_SECRET

# Outgoing SMTP / Contact Email Configuration
MAIL_SERVER = os.getenv("MAIL_SERVER", "").strip()
MAIL_PORT = int(os.getenv("MAIL_PORT", "587").strip() or 587)
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").strip().lower() in ("true", "1", "yes")
MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").strip().lower() in ("true", "1", "yes")
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").strip()
MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "").strip() or MAIL_USERNAME
CONTACT_RECIPIENT = os.getenv("CONTACT_RECIPIENT", "patilnishant787@gmail.com").strip()

ALLOWED_EXTENSIONS = {
    'pdf': {'application/pdf'},
    'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
    'pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
    'jpg': {'image/jpeg'},
    'jpeg': {'image/jpeg'},
    'png': {'image/png'}
}
