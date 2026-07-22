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


def test_send_alert_uses_ops_recipient_from_env(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com")
    captured = []
    monkeypatch.setattr(alerts, "_send_email", lambda subject, message, to_addr: captured.append(to_addr))
    alerts.send_alert("subject", "message")
    assert captured == ["ops@example.com"]


def test_send_tenant_email_noop_without_to_addr(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    captured = []
    monkeypatch.setattr(alerts, "_send_email", lambda *a, **kw: captured.append(a))
    alerts.send_tenant_email(None, "subject", "message")
    assert captured == []


def test_send_tenant_email_noop_without_smtp_host(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    alerts.send_tenant_email("tenant@example.com", "subject", "message")  # must not raise


def test_send_tenant_email_passes_recipient_through(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    captured = []
    monkeypatch.setattr(alerts, "_send_email", lambda subject, message, to_addr: captured.append((subject, to_addr)))
    alerts.send_tenant_email("tenant@example.com", "Daily digest", "body text")
    assert captured == [("Daily digest", "tenant@example.com")]


def test_send_tenant_email_does_not_touch_sentry(monkeypatch):
    """Tenant mail is routine, not an ops error — must never fire Sentry."""
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid.example")
    import sentry_sdk
    captured = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda *a, **kw: captured.append(a))
    alerts.send_tenant_email("tenant@example.com", "subject", "message")
    assert captured == []
