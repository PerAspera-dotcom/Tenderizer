"""CR-004 F4 — alerts.send_alert: both channels are independently optional
and must never raise, even if partially misconfigured.
"""
import alerts


def test_send_alert_is_a_noop_with_nothing_configured(monkeypatch, caplog):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    alerts.send_alert("subject", "message")  # must not raise


def test_send_alert_sentry_only(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    captured = []
    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level=None: captured.append((msg, level)))
    alerts.send_alert("subject", "message")
    assert captured == [("subject: message", "error")]


def test_send_alert_email_failure_is_swallowed(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid.example")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com")
    alerts.send_alert("subject", "message")  # SMTP connect will fail — must not raise
