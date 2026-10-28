"""Alerts (step 6) and optional notifications (step 9).

Console alerts are always printed. E-mail is optional: set these environment
variables or configure them in email_config.json:

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO
"""
import json
import os
from pathlib import Path
import smtplib
import threading
from email.message import EmailMessage

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "email_config.json"


def get_email_config():
    """Load configuration from env vars with fallback to email_config.json."""
    cfg = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", 587)),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "to": os.environ.get("ALERT_EMAIL_TO", ""),
        "enabled": os.environ.get("VISIONGUARD_EMAIL_ENABLED", "0") in ("1", "true", "True"),
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k in ("host", "port", "user", "password", "to"):
                    if k in saved and (not cfg[k] or k == "password"):
                        cfg[k] = saved[k]
                if "enabled" in saved:
                    cfg["enabled"] = bool(saved["enabled"])
        except Exception:
            pass
    return cfg


def save_email_config(new_cfg):
    """Save email configuration to email_config.json and sync os.environ."""
    current = get_email_config()
    for k in ("host", "port", "user", "password", "to", "enabled"):
        if k in new_cfg:
            current[k] = new_cfg[k]

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    if current.get("host"):
        os.environ["SMTP_HOST"] = str(current["host"])
    if current.get("port"):
        os.environ["SMTP_PORT"] = str(current["port"])
    if current.get("user"):
        os.environ["SMTP_USER"] = str(current["user"])
    if current.get("password"):
        os.environ["SMTP_PASSWORD"] = str(current["password"])
    if current.get("to"):
        os.environ["ALERT_EMAIL_TO"] = str(current["to"])
    os.environ["VISIONGUARD_EMAIL_ENABLED"] = "1" if current.get("enabled") else "0"
    return current


def format_alert(alert):
    return (f"🚨 {alert['type'].upper()} DETECTED\n"
            f"Camera: {alert['camera']}\n"
            f"Confidence: {alert['confidence']:.0%}\n"
            f"Time: {alert['time']}\n"
            f"Severity: {alert['severity']}")


def send_email(alert, snapshot_path=None):
    cfg = get_email_config()
    host = cfg.get("host") or os.environ.get("SMTP_HOST")
    to = cfg.get("to") or os.environ.get("ALERT_EMAIL_TO")
    user = cfg.get("user") or os.environ.get("SMTP_USER", "")
    password = cfg.get("password") or os.environ.get("SMTP_PASSWORD", "")
    port = int(cfg.get("port") or os.environ.get("SMTP_PORT", 587))

    if not host or not to:
        return "e-mail not configured (SMTP_HOST / ALERT_EMAIL_TO missing)"

def _send_smtp_message(host, port, user, password, msg, timeout=15):
    """Send SMTP message supporting both STARTTLS (port 587) and SSL (port 465) with auto-fallback."""
    def _try_ssl(p):
        with smtplib.SMTP_SSL(host, p, timeout=timeout) as s:
            if user and password:
                s.login(user, password)
            s.send_message(msg)

    def _try_starttls(p):
        with smtplib.SMTP(host, p, timeout=timeout) as s:
            s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)

    port = int(port or 587)
    if port == 465:
        try:
            _try_ssl(465)
        except (smtplib.SMTPServerDisconnected, ConnectionError):
            _try_starttls(587)
    else:
        try:
            _try_starttls(port)
        except (smtplib.SMTPServerDisconnected, ConnectionError, OSError):
            # Fallback to SSL on 465 if port 587 dropped connection
            _try_ssl(465)


def send_email(alert, snapshot_path=None):
    cfg = get_email_config()
    host = cfg.get("host") or os.environ.get("SMTP_HOST")
    to = cfg.get("to") or os.environ.get("ALERT_EMAIL_TO")
    user = cfg.get("user") or os.environ.get("SMTP_USER", "")
    password = cfg.get("password") or os.environ.get("SMTP_PASSWORD", "")
    port = int(cfg.get("port") or os.environ.get("SMTP_PORT", 587))

    if not host or not to:
        return "e-mail not configured (SMTP_HOST / ALERT_EMAIL_TO missing)"

    def _send():
        try:
            msg = EmailMessage()
            msg["Subject"] = f"VisionGuard: {alert['type'].upper()} detected on {alert['camera']}"
            msg["From"] = user or "visionguard@localhost"
            msg["To"] = to
            msg.set_content(format_alert(alert))
            if snapshot_path and os.path.exists(snapshot_path):
                with open(snapshot_path, "rb") as f:
                    msg.add_attachment(f.read(), maintype="image", subtype="jpeg", filename="detection.jpg")
            _send_smtp_message(host, port, user, password, msg, timeout=20)
        except Exception as e:
            print(f"[VisionGuard Email Error] {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()  # never block the video loop
    return f"e-mail sent to {to}"


def send_test_email(custom_cfg=None):
    """Synchronously send a test email to verify credentials and reachability."""
    cfg = get_email_config()
    if custom_cfg:
        cfg.update({k: v for k, v in custom_cfg.items() if v is not None and v != ""})

    host = cfg.get("host")
    to = cfg.get("to")
    user = cfg.get("user", "")
    password = cfg.get("password", "")
    port = int(cfg.get("port", 587))

    if not host:
        return False, "SMTP Host is missing."
    if not to:
        return False, "Recipient email address is missing."

    try:
        msg = EmailMessage()
        msg["Subject"] = "VisionGuard: Test Alert Notification"
        msg["From"] = user or "visionguard@localhost"
        msg["To"] = to
        msg.set_content(
            "Hello,\n\n"
            "This is a test notification from your VisionGuard Fire & Smoke Monitoring system.\n\n"
            "Your email notifications are successfully configured and active!\n"
            "When fire or smoke is detected, VisionGuard will immediately send an alert with the camera name, timestamp, and incident snapshot to this address.\n"
        )
        _send_smtp_message(host, port, user, password, msg, timeout=15)
        return True, f"Test email sent successfully to {to}!"
    except smtplib.SMTPAuthenticationError as e:
        err_msg = e.smtp_error.decode("utf-8", errors="ignore") if hasattr(e, "smtp_error") else str(e)
        return False, f"SMTP Authentication failed: {err_msg}. Note: For Gmail, please use a 16-character Google App Password (not your normal password)."
    except smtplib.SMTPServerDisconnected as e:
        return False, f"Server disconnected: {str(e)}. Try switching port to 465 (SSL) or 587 (STARTTLS)."
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"


