"""Info-Mails (P8): RVK-Abgleich und Monatsstatistik, HTML direkt im Mailtext, dazu Textfassung.

Kein Anhang, keine Bilder; die Monatsstatistik enthält genau einen Link („Alle Daten“).
Zugangsdaten ausschließlich aus der Konfiguration (.env); sie werden nie protokolliert. Ohne
SMTP-Konfiguration oder bei einem Sendefehler landet das HTML in
<MAIL_FALLBACK_DIR>/<präfix>-<zeit>.html (sync-mail bzw. usage-mail) und im Log steht eine
Warnung – der Abgleich selbst scheitert daran nicht.

Evaluationskennzahlen heißen „Relevanzmaß“ (Dezimalzahlen 0–1), nie Prozent. Prozent gibt es nur
in der Monatsstatistik für Nutzungsanteile (z. B. Suchen ohne Treffer), nie für Konfidenz.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

OUTCOME_LABELS = {
    "swapped": "Neuer Stand ist live.",
    "rejected": "Prüfungen nicht bestanden – die alte Datenbank bleibt live.",
    "error": "Fehler – die alte Datenbank bleibt live.",
    "dry_run": "Probelauf – nichts getauscht.",
}


def _setting(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_sync_report(result: dict) -> tuple[str, str, str]:
    """(Betreff, HTML, Text) aus dem Ergebnis von run_monthly_sync (siehe pipeline.py)."""
    outcome = result.get("status", "error")
    outcome_text = OUTCOME_LABELS.get(outcome, outcome)
    new_version = result.get("new_version") or "?"
    old_version = result.get("old_version") or "?"
    if outcome == "swapped":
        subject = f"RVKompass: RVK-Stand {new_version} ist live"
    elif outcome == "rejected":
        subject = f"RVKompass: RVK-Stand {new_version} nicht übernommen"
    elif outcome == "dry_run":
        subject = f"RVKompass: Probelauf RVK-Stand {new_version}"
    else:
        subject = "RVKompass: Fehler beim RVK-Abgleich"
    context = dict(result)
    context.update(
        outcome_text=outcome_text,
        old_version=old_version,
        new_version=new_version,
        fmt=_fmt,
    )
    env = _environment()
    html = env.get_template("mail/sync_report.html").render(**context)
    text = env.get_template("mail/sync_report.txt").render(**context)
    return subject, html, text


def render_usage_report(context: dict) -> tuple[str, str]:
    """(HTML, Text) der Monatsstatistik; Kontext aus usage_report.mail_context."""
    env = _environment()
    html = env.get_template("mail/usage_report.html").render(**context)
    text = env.get_template("mail/usage_report.txt").render(**context)
    return html, text


def build_message(config, subject: str, html: str, text: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _setting(config, "MAIL_FROM", "")
    message["To"] = _setting(config, "MAIL_TO", "")
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def smtp_configured(config) -> bool:
    return bool(
        _setting(config, "SMTP_HOST")
        and _setting(config, "MAIL_FROM")
        and _setting(config, "MAIL_TO")
    )


def security_mode(config) -> str:
    """Verbindungsart: "ssl" (direkt verschlüsselt), "starttls" oder "none".

    Gleiche Variable wie beim Steuerprogramm (orchestrator/notify.py): SMTP_SECURITY=ssl|starttls.
    Ohne Angabe: Port 465 → ssl, sonst starttls; SMTP_STARTTLS=false heißt dann unverschlüsselt.
    """
    port = int(_setting(config, "SMTP_PORT", 587))
    value = str(_setting(config, "SMTP_SECURITY", "") or "").strip().lower()
    if value in ("ssl", "smtps", "implicit", "ssl/tls"):
        return "ssl"
    if value in ("starttls", "start_tls"):
        return "starttls"
    if value:
        logger.warning("Unbekannter Wert für SMTP_SECURITY – Voreinstellung nach Port")
    if port == 465:
        return "ssl"
    return "starttls" if _setting(config, "SMTP_STARTTLS", True) else "none"


def send_mail(
    config, subject: str, html: str, text: str, smtp_factory: Callable | None = None
) -> None:
    """Sendet multipart/alternative (Text + HTML im Mailtext). Fehler gehen an den Aufrufer.

    `smtp_factory` (für Tests) ersetzt smtplib.SMTP bzw. smtplib.SMTP_SSL.
    """
    message = build_message(config, subject, html, text)
    mode = security_mode(config)
    context = ssl.create_default_context()
    host = _setting(config, "SMTP_HOST")
    port = int(_setting(config, "SMTP_PORT", 587))
    timeout = int(_setting(config, "SMTP_TIMEOUT_SECONDS", 30))
    if mode == "ssl":
        smtp = (smtp_factory or smtplib.SMTP_SSL)(
            host, port, timeout=timeout, context=context
        )
    else:
        smtp = (smtp_factory or smtplib.SMTP)(host, port, timeout=timeout)
    with smtp:
        if mode == "starttls":
            smtp.starttls(context=context)
        user = _setting(config, "SMTP_USER")
        if user:
            smtp.login(user, _setting(config, "SMTP_PASSWORD", ""))
        smtp.send_message(message)


def write_fallback(
    config, html: str, now: datetime | None = None, prefix: str = "sync-mail"
) -> Path:
    directory = Path(_setting(config, "MAIL_FALLBACK_DIR", "logs"))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{prefix}-{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path


def deliver(
    config,
    subject: str,
    html: str,
    text: str,
    smtp_factory: Callable | None = None,
    fallback_prefix: str = "sync-mail",
) -> str:
    """Sendet oder schreibt ersatzweise eine Datei. Liefert "sent" oder "file:<pfad>"."""
    if smtp_configured(config):
        try:
            send_mail(config, subject, html, text, smtp_factory=smtp_factory)
            logger.info("Info-Mail gesendet: %s", subject)
            return "sent"
        except Exception as exc:  # noqa: BLE001 – Mailfehler darf den Abgleich nicht stoppen
            # Nur Typ und Meldung, keine Zugangsdaten
            logger.warning("Info-Mail nicht gesendet (%s: %s)", type(exc).__name__, exc)
    else:
        logger.warning("Kein SMTP konfiguriert (SMTP_HOST, MAIL_FROM, MAIL_TO).")
    path = write_fallback(config, html, prefix=fallback_prefix)
    logger.warning("Info-Mail ersatzweise gespeichert: %s", path)
    return f"file:{path}"


def notify_gnd_error(config, message: str, smtp_factory: Callable | None = None) -> str:
    """Mail bei Fehlern der GND-Aktualisierung (ein normaler Wechsel steht nur im Log)."""
    result = {
        "status": "error",
        "kind": "gnd",
        "error": message,
    }
    subject, html, text = render_sync_report(result)
    subject = "RVKompass: Fehler bei der GND-Aktualisierung"
    return deliver(config, subject, html, text, smtp_factory=smtp_factory)
