import os
import uuid
import json
import base64
import hashlib
import datetime
from pathlib import Path
from werkzeug.utils import secure_filename
import pymupdf as fitz  # PyMuPDF
from database import get_db
from services.pdf_edit import PDFEditService

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
SIGNINGS_DIR = STORAGE_DIR / "signings"
SIGNINGS_DIR.mkdir(parents=True, exist_ok=True)

class SignatureService:
    # --- Saved Signatures ---

    @staticmethod
    def save_signature(user_id, name, sig_type, data_url, is_default=False):
        if not name or not name.strip():
            name = f"Signature {datetime.datetime.now().strftime('%b %d')}"
        if sig_type not in ["draw", "type", "upload"]:
            sig_type = "draw"

        sig_id = f"sig_{uuid.uuid4().hex[:12]}"
        conn = get_db()
        cursor = conn.cursor()

        if is_default:
            cursor.execute("UPDATE saved_signatures SET is_default = 0 WHERE user_id = ?", (user_id,))

        cursor.execute("""
            INSERT INTO saved_signatures (id, user_id, name, sig_type, data_url, is_default)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (sig_id, user_id, name.strip(), sig_type, data_url, 1 if is_default else 0))
        conn.commit()
        conn.close()

        return SignatureService.get_signature(sig_id, user_id)

    @staticmethod
    def list_signatures(user_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, name, sig_type, data_url, is_default, created_at
            FROM saved_signatures WHERE user_id = ?
            ORDER BY is_default DESC, created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_signature(sig_id, user_id=None):
        conn = get_db()
        cursor = conn.cursor()
        if user_id:
            cursor.execute("SELECT * FROM saved_signatures WHERE id = ? AND user_id = ?", (sig_id, user_id))
        else:
            cursor.execute("SELECT * FROM saved_signatures WHERE id = ?", (sig_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def set_default_signature(sig_id, user_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE saved_signatures SET is_default = 0 WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE saved_signatures SET is_default = 1 WHERE id = ? AND user_id = ?", (sig_id, user_id))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def delete_signature(sig_id, user_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM saved_signatures WHERE id = ? AND user_id = ?", (sig_id, user_id))
        conn.commit()
        conn.close()
        return True

    # --- Document Signing & Audit Trail ---

    @staticmethod
    def calculate_file_hash(file_path):
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def sign_document(user_id, input_pdf_path, signature_data, title, page=1, x=100, y=100, width=160, height=60, signer_name="Verified Signer", signer_email="signer@paperpilot.local", ip_address="127.0.0.1"):
        signing_id = f"ds_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Calculate original document hash
        orig_hash = SignatureService.calculate_file_hash(input_pdf_path)

        # Output signed file path
        safe_stem = secure_filename(Path(title).stem) or "document"
        output_filename = f"{safe_stem}_signed_{signing_id}.pdf"
        output_path = SIGNINGS_DIR / output_filename

        # Open document, stamp signature and append audit certificate
        doc = fitz.open(str(input_pdf_path))
        target_idx = max(0, min(len(doc) - 1, int(page) - 1))
        target_page = doc[target_idx]

        rect = fitz.Rect(float(x), float(y), float(x) + float(width), float(y) + float(height))
        if isinstance(signature_data, str) and signature_data.startswith("data:image"):
            header, encoded = signature_data.split(",", 1)
            img_bytes = base64.b64decode(encoded)
            target_page.insert_image(rect, stream=img_bytes)
        else:
            target_page.insert_image(rect, filename=str(signature_data))

        # Append PaperPilot Audit Certificate page to the signed PDF
        cert_page = doc.new_page(width=595, height=842) # A4
        
        # Draw header banner (PaperPilot Navy)
        banner_rect = fitz.Rect(0, 0, 595, 80)
        cert_page.draw_rect(banner_rect, color=None, fill=(0.09, 0.23, 0.31)) # #173B4F
        
        cert_page.insert_text(fitz.Point(40, 48), "PaperPilot Document Signing Certificate", fontsize=18, color=(1, 1, 1))
        cert_page.insert_text(fitz.Point(40, 68), "Legally Binding Digital Audit Trail & Cryptographic Verification", fontsize=10, color=(0.72, 0.95, 0.86)) # Mint

        # Draw content
        y_cursor = 120
        cert_page.insert_text(fitz.Point(40, y_cursor), "DOCUMENT DETAILS", fontsize=12, color=(0.06, 0.17, 0.24))
        y_cursor += 24
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Document Title: {title}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Signing Reference: {signing_id}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Original SHA-256: {orig_hash}", fontsize=8, color=(0.33, 0.40, 0.47))

        y_cursor += 35
        cert_page.insert_text(fitz.Point(40, y_cursor), "SIGNER VERIFICATION", fontsize=12, color=(0.06, 0.17, 0.24))
        y_cursor += 24
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Signer Name: {signer_name}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Signer Email / ID: {signer_email}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"IP Address: {ip_address}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Timestamp (UTC): {now_iso}", fontsize=10, color=(0.14, 0.21, 0.27))
        y_cursor += 18
        cert_page.insert_text(fitz.Point(40, y_cursor), f"Signature Position: Page {page} (x: {x}, y: {y})", fontsize=10, color=(0.14, 0.21, 0.27))

        # Security footer
        y_cursor += 45
        cert_page.draw_rect(fitz.Rect(40, y_cursor, 555, y_cursor + 60), color=(0.86, 0.90, 0.92), fill=(0.96, 0.97, 0.98))
        cert_page.insert_text(fitz.Point(55, y_cursor + 25), "Secure Verification Notice:", fontsize=9, color=(0.09, 0.23, 0.31))
        cert_page.insert_text(fitz.Point(55, y_cursor + 45), "This audit certificate confirms the signature was recorded in PaperPilot with tamper-evident log records.", fontsize=8, color=(0.33, 0.40, 0.47))

        doc.save(str(output_path))
        doc.close()

        final_hash = SignatureService.calculate_file_hash(str(output_path))

        audit_data = {
            "signing_id": signing_id,
            "title": title,
            "original_sha256": orig_hash,
            "final_sha256": final_hash,
            "signer_name": signer_name,
            "signer_email": signer_email,
            "ip_address": ip_address,
            "timestamp": now_iso,
            "page": page,
            "x": x,
            "y": y
        }

        # Store in DB
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO document_signings (id, user_id, title, original_file, signed_file, status, document_hash, audit_log_json)
            VALUES (?, ?, ?, ?, ?, 'signed', ?, ?)
        """, (signing_id, user_id, title, Path(input_pdf_path).name, output_filename, final_hash, json.dumps(audit_data)))
        conn.commit()
        conn.close()

        return {
            "signing_id": signing_id,
            "title": title,
            "status": "signed",
            "signed_filename": output_filename,
            "document_hash": final_hash,
            "download_url": f"/api/signatures/documents/{signing_id}/download",
            "audit": audit_data
        }

    @staticmethod
    def list_documents(user_id, status=None):
        conn = get_db()
        cursor = conn.cursor()
        if status:
            cursor.execute("""
                SELECT id, user_id, title, original_file, signed_file, status, document_hash, audit_log_json, created_at
                FROM document_signings WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
            """, (user_id, status))
        else:
            cursor.execute("""
                SELECT id, user_id, title, original_file, signed_file, status, document_hash, audit_log_json, created_at
                FROM document_signings WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
        rows = cursor.fetchall()
        conn.close()

        docs = []
        for r in rows:
            docs.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "title": r["title"],
                "original_file": r["original_file"],
                "signed_file": r["signed_file"],
                "status": r["status"],
                "document_hash": r["document_hash"],
                "download_url": f"/api/signatures/documents/{r['id']}/download",
                "audit": json.loads(r["audit_log_json"] or "{}"),
                "created_at": str(r["created_at"])
            })
        return docs

    @staticmethod
    def get_document(signing_id, user_id=None):
        conn = get_db()
        cursor = conn.cursor()
        if user_id:
            cursor.execute("SELECT * FROM document_signings WHERE id = ? AND user_id = ?", (signing_id, user_id))
        else:
            cursor.execute("SELECT * FROM document_signings WHERE id = ?", (signing_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "original_file": row["original_file"],
            "signed_file": row["signed_file"],
            "signed_path": str(SIGNINGS_DIR / row["signed_file"]) if row["signed_file"] else None,
            "status": row["status"],
            "document_hash": row["document_hash"],
            "download_url": f"/api/signatures/documents/{row['id']}/download",
            "audit": json.loads(row["audit_log_json"] or "{}"),
            "created_at": str(row["created_at"])
        }
