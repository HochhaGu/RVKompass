"""Mails per SMTP (HTML direkt im Mailtext) und Aufbau der Mail-Inhalte."""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .envfile import MAIL_KEYS, Secrets

TEMPLATES = Path(__file__).resolve().parent / "templates"
SUBJECT_PREFIX = "[RVKompass]"


def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
    )


@dataclass
class Mail:
    subject: str
    html: str
    text: str

    def as_dict(self) -> dict:
        return {"subject": self.subject, "html": self.html, "text": self.text}


def html_to_text(markup: str) -> str:
    """Einfache Klartext-Fassung für Mailprogramme ohne HTML."""
    s = re.sub(r"(?is)<(style|head)[^>]*>.*?</\1>", "", markup)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|tr)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"(?i)</t[dh]>", "  ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", s).strip() + "\n"


def render_mail(title: str, intro: str, sections: list[dict], status_url: str) -> Mail:
    """sections: [{"heading": str, "paragraphs": [str], "rows": [(k, v)], "pre": str}]"""
    markup = (
        jinja_env()
        .get_template("mail.html")
        .render(title=title, intro=intro, sections=sections, status_url=status_url)
    )
    return Mail(
        subject=f"{SUBJECT_PREFIX} {title}", html=markup, text=html_to_text(markup)
    )


def security_mode(security: str, port: int) -> str:
    """SMTP_SECURITY → 'ssl' (direkt verschlüsselt), 'starttls' oder 'none'."""
    s = (security or "").strip().lower()
    if s in ("ssl", "smtps", "implicit", "ssl/tls"):
        return "ssl"
    if s in ("starttls", "start_tls"):
        return "starttls"
    if s == "tls":
        return "ssl" if port == 465 else "starttls"
    if s in ("none", "plain", "off", "false"):
        return "none"
    return "ssl" if port == 465 else "starttls"  # sichere Voreinstellung


class Mailer:
    def __init__(self, secrets: Secrets, smtp_factory=None):
        self.secrets = secrets
        self._smtp_factory = smtp_factory

    def configured(self) -> list[str]:
        """Liste fehlender Schlüssel (leer = vollständig)."""
        return self.secrets.missing(MAIL_KEYS)

    def send(self, mail: Mail) -> None:
        missing = self.configured()
        if missing:
            raise RuntimeError(
                f"Mail-Zugang unvollständig, es fehlen: {', '.join(missing)}"
            )
        s = self.secrets
        host, port = s.get("SMTP_HOST"), int(s.get("SMTP_PORT"))
        msg = EmailMessage()
        msg["Subject"] = mail.subject
        msg["From"] = s.get("MAIL_FROM")
        msg["To"] = s.get("MAIL_TO")
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(
            domain=s.get("MAIL_FROM").rsplit("@", 1)[-1] or None
        )
        msg.set_content(mail.text)
        msg.add_alternative(mail.html, subtype="html")

        mode = security_mode(s.get("SMTP_SECURITY"), port)
        ctx = ssl.create_default_context()
        factory = self._smtp_factory
        try:
            if mode == "ssl":
                smtp = (factory or smtplib.SMTP_SSL)(
                    host, port, timeout=30, context=ctx
                )
            else:
                smtp = (factory or smtplib.SMTP)(host, port, timeout=30)
            with smtp:
                if mode == "starttls":
                    smtp.starttls(context=ctx)
                smtp.login(s.get("SMTP_USER"), s.get("SMTP_PASSWORD"))
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            # Fehlermeldung ohne Zugangsdaten weitergeben
            raise RuntimeError(self.secrets.redact(f"SMTP-Fehler: {exc}")) from None
