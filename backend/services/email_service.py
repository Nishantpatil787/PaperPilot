import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import (
    MAIL_SERVER,
    MAIL_PORT,
    MAIL_USE_TLS,
    MAIL_USE_SSL,
    MAIL_USERNAME,
    MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER,
    CONTACT_RECIPIENT
)

class EmailNotConfiguredError(Exception):
    """Raised when outgoing mail server credentials are not configured."""
    pass

class EmailValidationError(ValueError):
    """Raised when contact input validation fails."""
    pass

class EmailService:
    EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

    @classmethod
    def is_configured(cls) -> bool:
        """Checks if SMTP credentials are set in environment."""
        return bool(MAIL_SERVER and MAIL_USERNAME and MAIL_PASSWORD)

    @classmethod
    def sanitize_header(cls, text: str) -> str:
        """Strip CRLF characters to prevent header injection."""
        return re.sub(r"[\r\n]+", " ", (text or "").strip())

    @classmethod
    def send_contact_message(cls, name: str, email: str, message: str, bot_field: str = "") -> dict:
        """
        Validates contact form inputs and sends message to CONTACT_RECIPIENT via SMTP.
        Raises EmailValidationError for bad input or spam bot.
        Raises EmailNotConfiguredError if SMTP settings are missing.
        """
        if bot_field and bot_field.strip():
            raise EmailValidationError("Automated submission detected.")

        name = cls.sanitize_header(name)
        email = cls.sanitize_header(email)
        message = (message or "").strip()

        if not name or len(name) < 2 or len(name) > 100:
            raise EmailValidationError("Please provide your full name (2-100 characters).")

        if not email or not cls.EMAIL_REGEX.match(email) or len(email) > 120:
            raise EmailValidationError("Please provide a valid email address.")

        if not message or len(message) < 5:
            raise EmailValidationError("Please enter a message with at least 5 characters.")
        if len(message) > 5000:
            raise EmailValidationError("Message cannot exceed 5,000 characters.")

        if not cls.is_configured():
            raise EmailNotConfiguredError(
                f"Email delivery service is currently not configured on the server. "
                f"Please reach out directly to {CONTACT_RECIPIENT}."
            )

        recipient = CONTACT_RECIPIENT or "patilnishant787@gmail.com"
        sender = MAIL_DEFAULT_SENDER or MAIL_USERNAME
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[PaperPilot Contact] Message from {name}"
        msg["From"] = sender
        msg["To"] = recipient
        msg["Reply-To"] = email

        plain_text = f"""PaperPilot Contact Form Submission
========================================
From: {name} ({email})
Submitted At: {timestamp}
Recipient: {recipient}

Message:
----------------------------------------
{message}
----------------------------------------
To reply directly to the sender, reply to this email.
"""

        html_text = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #173B4F; }}
    .container {{ max-width: 580px; margin: 20px auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 16px; background-color: #ffffff; }}
    .header {{ border-bottom: 2px solid #8AC8F9; padding-bottom: 12px; margin-bottom: 20px; }}
    .badge {{ display: inline-block; background-color: #B8F2DC; color: #173B4F; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: bold; }}
    .meta {{ font-size: 12px; color: #64748b; margin-top: 8px; }}
    .content {{ background-color: #f8fafc; border-radius: 12px; padding: 16px; margin: 16px 0; border-left: 4px solid #173B4F; white-space: pre-wrap; font-size: 13px; }}
    .footer {{ font-size: 11px; color: #94a3b8; margin-top: 24px; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">PaperPilot Contact</span>
      <h2 style="margin: 8px 0 0 0; font-size: 18px; color: #0D1B24;">New Message Received</h2>
      <div class="meta">
        <strong>Sender:</strong> {name} &lt;{email}&gt;<br>
        <strong>Time:</strong> {timestamp}
      </div>
    </div>
    <div class="content">{message}</div>
    <div class="footer">
      This email was dispatched via PaperPilot secure contact system.<br>
      You can reply directly to this email to contact the visitor at <strong>{email}</strong>.
    </div>
  </div>
</body>
</html>
"""

        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_text, "html", "utf-8"))

        try:
            if MAIL_USE_SSL or MAIL_PORT == 465:
                server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=10)
            else:
                server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10)
                if MAIL_USE_TLS:
                    server.starttls()

            if MAIL_USERNAME and MAIL_PASSWORD:
                server.login(MAIL_USERNAME, MAIL_PASSWORD)

            server.send_message(msg)
            server.quit()
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f"SMTP authentication failed: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Failed to deliver email through mail server: {str(e)}")

        return {
            "success": True,
            "message": "Thank you! Your message has been sent successfully. We will respond to your email shortly.",
            "recipient": recipient,
            "timestamp": timestamp
        }

    @classmethod
    def send_team_invitation(cls, to_email: str, inviter_name: str, team_name: str, invite_link: str) -> bool:
        """
        Sends an authentic team invitation email via SMTP.
        Returns True if delivered, False if SMTP is unconfigured or fails gracefully.
        """
        to_email = cls.sanitize_header(to_email)
        if not to_email or not cls.EMAIL_REGEX.match(to_email):
            raise EmailValidationError("Please provide a valid recipient email address.")

        if not cls.is_configured():
            return False

        sender = MAIL_DEFAULT_SENDER or MAIL_USERNAME
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Invitation: Join {team_name} on PaperPilot"
        msg["From"] = sender
        msg["To"] = to_email

        plain_text = f"""Hello,

{inviter_name} has invited you to collaborate in team '{team_name}' on PaperPilot.

To accept your invitation, open the link below:
{invite_link}

This invitation link expires in 7 days.

Best regards,
The PaperPilot Team
"""
        html_text = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #173B4F; padding: 20px;">
  <div style="max-width: 540px; margin: 0 auto; border: 1px solid #E2E8F0; border-radius: 12px; padding: 24px; background: #fff;">
    <h2 style="color: #102C3C; margin-top: 0;">Team Collaboration Invitation</h2>
    <p><strong>{inviter_name}</strong> has invited you to join the team <strong>{team_name}</strong> on PaperPilot.</p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{invite_link}" style="background-color: #173B4F; color: #ffffff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block;">Accept Invitation</a>
    </div>
    <p style="font-size: 12px; color: #64748B;">This invitation link will remain valid for 7 days. If you didn't expect this invitation, you can safely ignore this email.</p>
  </div>
</body>
</html>"""

        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_text, "html", "utf-8"))

        try:
            if MAIL_USE_SSL or MAIL_PORT == 465:
                server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=10)
            else:
                server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10)
                if MAIL_USE_TLS:
                    server.starttls()

            if MAIL_USERNAME and MAIL_PASSWORD:
                server.login(MAIL_USERNAME, MAIL_PASSWORD)

            server.send_message(msg)
            server.quit()
            return True
        except Exception:
            return False
