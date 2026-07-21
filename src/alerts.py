"""CR-004 F4 — alerting for backup failures and escalated source failures.

Both channels are optional and inert if unconfigured, same convention as
SENTRY_DSN elsewhere in this codebase: an ops team that hasn't wired up
Sentry/SMTP yet gets a log line, not a crash.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_alert(subject, message):
    logger.warning("ALERT: %s — %s", subject, message)
    _send_sentry(subject, message)
    _send_email(subject, message)


def _send_sentry(subject, message):
    if not os.getenv("SENTRY_DSN"):
        return
    import sentry_sdk
    sentry_sdk.capture_message(f"{subject}: {message}", level="error")


def _send_email(subject, message):
    host = os.getenv("SMTP_HOST")
    to_addr = os.getenv("ALERT_EMAIL_TO")
    if not host or not to_addr:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("ALERT_EMAIL_FROM", "alerts@tender-izer.com")
    msg["To"] = to_addr
    msg.set_content(message)
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)
    except Exception:
        logger.exception("alert email failed to send")
