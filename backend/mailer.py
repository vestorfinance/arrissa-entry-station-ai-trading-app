"""
Outgoing email — renders the shared HTML template and sends via the SMTP settings
stored on the admin row. Images in the template are stripped (per product spec).
"""
import re
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import db
import auth

DEFAULT_APP_NAME = "EntryStation"
SUPPORT_EMAIL = "arrissa.ai@gmail.com"


def app_name():
    """The brand name — admin-configurable (admin_settings.app_name), else default."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT app_name FROM admin_settings WHERE id = 1").fetchone()
        return (row["app_name"] if row and row["app_name"] else None) or DEFAULT_APP_NAME
    except Exception:
        return DEFAULT_APP_NAME
_TEMPLATE_PATH = Path(__file__).parent.parent / "email-template-sample.html"
_template_cache = None


def _template() -> str:
    """The email template with ALL images removed (the footer download cell and
    any <img> tags)."""
    global _template_cache
    if _template_cache is None:
        html = _TEMPLATE_PATH.read_text()
        html = re.sub(r'<td class="footer-downloads".*?</td>', "", html, flags=re.S)
        html = re.sub(r"<img[^>]*?>", "", html, flags=re.S)
        _template_cache = html
    return _template_cache


def render(**values) -> str:
    html = _template()
    for key, val in values.items():
        html = html.replace("{{" + key + "}}", "" if val is None else str(val))
    # blank out any placeholders the caller didn't supply
    html = re.sub(r"\{\{[a-z_]+\}\}", "", html)
    return html


def _smtp():
    with db.connect() as conn:
        row = conn.execute(
            "SELECT smtp_host, smtp_port, smtp_user, smtp_pass_enc, smtp_from "
            "FROM admin_settings WHERE id = 1"
        ).fetchone()
    if not row or not row["smtp_user"] or not row["smtp_pass_enc"]:
        raise RuntimeError("SMTP is not configured in admin settings")
    return {
        "host": row["smtp_host"] or "smtp.gmail.com",
        "port": int(row["smtp_port"] or 587),
        "user": row["smtp_user"],
        "password": auth.decrypt(row["smtp_pass_enc"]),
        "from": row["smtp_from"] or row["smtp_user"],
    }


def _html_to_text(html: str) -> str:
    """A readable text/plain fallback derived from the HTML body."""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    t = re.sub(r"(?i)<(br|/p|/div|/tr|/h[1-6])\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() or "Please view this message in an HTML-capable email client."


def _wrap_html(html: str) -> str:
    """Ensure the body is a complete HTML document (SpamAssassin flags fragments)."""
    if re.search(r"(?i)<html[ >]", html):
        return html
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body>{html}</body></html>')


def send_email(to: str, subject: str, html: str):
    s = _smtp()
    html = _wrap_html(html)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{app_name()} <{s['from']}>"
    msg["To"] = to
    # text/plain first, then text/html — order matters for multipart/alternative.
    msg.attach(MIMEText(_html_to_text(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    # Loopback relay (e.g. a local ZoneMTA feeder) may present a self-signed cert;
    # verifying it is pointless since the traffic never leaves the machine.
    if s["host"] in ("127.0.0.1", "localhost", "::1"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with smtplib.SMTP(s["host"], s["port"], timeout=25) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.login(s["user"], s["password"])
        server.sendmail(s["from"], [to], msg.as_string())


def send_verification(to: str, code: str, frontend_domain: str = "http://localhost:5173"):
    name = app_name()
    message = (
        f"<p>Welcome to {name}. Enter this code to verify your email and finish "
        "creating your account:</p>"
        f"<p style='font-size:34px;font-weight:700;letter-spacing:8px;margin:6px 0 14px;'>{code}</p>"
        "<p>The code expires in 15 minutes. If you didn’t request this, you can ignore this email.</p>"
    )
    html = render(
        email_subject=f"Verify your email — {name}",
        app_name=name,
        email_title="Verify your email",
        action_url=frontend_domain,
        action_label=f"Open {name}",
        email_message_html=message,
        support_email=SUPPORT_EMAIL,
        frontend_domain=frontend_domain,
    )
    send_email(to, f"Your {name} verification code: {code}", html)
