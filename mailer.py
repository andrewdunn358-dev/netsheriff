"""Minimal SMTP sender for password-reset emails.

Config via environment variables (set these in /opt/netsheriff/.env):
  SMTP_HOST=smtp.office365.com
  SMTP_PORT=587
  SMTP_USER=reports@yourmsp.co.uk
  SMTP_PASS=...
  SMTP_FROM=reports@yourmsp.co.uk   (optional, defaults to SMTP_USER)

If SMTP_HOST isn't set, send_reset_email() raises — the caller should catch
this and show a sensible error rather than pretending the email went out.
"""
import os, smtplib
from email.message import EmailMessage


def send_reset_email(to_addr, reset_link, brand):
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST not configured — password reset emails can't be sent")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = f"{brand} — reset your password"
    msg["From"], msg["To"] = from_addr, to_addr
    msg.set_content(
        f"Hi,\n\nSomeone requested a password reset for your {brand} portal login. "
        f"If this was you, click the link below (valid for 1 hour):\n\n{reset_link}\n\n"
        f"If you didn't request this, you can ignore this email — your password "
        f"hasn't been changed.\n\nRegards,\n{brand}")
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
