"""Info-Mail (app/services/mailer.py): HTML im Mailtext, Fallback in Datei, keine Zugangsdaten im Log."""

import logging
from pathlib import Path
from typing import ClassVar

from app.services import mailer

PASSWORD = "streng-geheimes-passwort-123"

CONFIG = {
    "SMTP_HOST": "smtp.example.invalid",
    "SMTP_PORT": 587,
    "SMTP_USER": "absender",
    "SMTP_PASSWORD": PASSWORD,
    "SMTP_STARTTLS": True,
    "SMTP_TIMEOUT_SECONDS": 30,
    "MAIL_FROM": "rvkompass@example.invalid",
    "MAIL_TO": "empfaenger@example.invalid",
}

RESULT = {
    "kind": "rvk",
    "status": "swapped",
    "old_version": "2026/2",
    "new_version": "2026/3",
    "old_count": 783427,
    "new_count": 784001,
    "diff": {"new": 700, "changed": 1200, "deleted": 126},
    "diff_baseline": False,
    "notation_change_rows": 2026,
    "gold_marks": [
        {
            "query": "<script>alert(1)</script>",
            "notation": "ZL 9999",
            "status": "deleted",
            "label": "gelöscht in 2026/3",
        },
        {
            "query": "Fehlt",
            "notation": "XX 1",
            "status": "missing_before",
            "label": "fehlte schon vorher (bleibt in der Messbasis)",
        },
    ],
    "eval_before": {"hit@10": 0.5567, "mrr": 0.3461, "n": 97},
    "eval_after": {"hit@10": 0.5464, "mrr": 0.34, "n": 96},
    "checks": [
        {
            "name": "Notationsanzahl",
            "passed": True,
            "message": "alt 783427, neu 784001",
        },
        {"name": "Evaluation Hit@10", "passed": True, "message": "ok"},
    ],
    "backup_dir": "/app/data/backups/rvk-2026_2-20261202T030000Z",
    "finished_at": "2026-12-02T05:00:00+00:00",
}


class FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.messages, self.logged_in, self.tls = [], None, False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.messages.append(message)


class BrokenSMTP(FakeSMTP):
    def login(self, user, password):
        raise OSError(f"Anmeldung abgelehnt für {user}")


def test_render_contains_all_figures_and_escapes():
    subject, html, text = mailer.render_sync_report(RESULT)
    assert subject == "RVKompass: RVK-Stand 2026/3 ist live"
    for needle in (
        "2026/2",
        "2026/3",
        "783427",
        "784001",
        "700 / 1200 / 126",
        "0.5567",
        "0.5464",
        "0.3461",
        "gelöscht in 2026/3",
        "fehlte schon vorher",
        "rvk-2026_2-20261202T030000Z",
        "Neuer Stand ist live.",
    ):
        assert needle in html, needle
        assert needle in text, needle
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    for content in (html, text):
        assert "Konfidenz" not in content
        assert "%" not in content
    assert "Relevanzmaß" in html


def test_render_error_and_rejected():
    subject, html, _ = mailer.render_sync_report(
        {"kind": "rvk", "status": "error", "error": "ConnectionError: Seite weg"}
    )
    assert "Fehler" in subject
    assert "ConnectionError: Seite weg" in html
    subject, html, _ = mailer.render_sync_report(
        dict(
            RESULT,
            status="rejected",
            checks=[{"name": "Anzahl", "passed": False, "message": "zu viel"}],
        )
    )
    assert "nicht übernommen" in subject
    assert "NICHT bestanden" in html
    assert "alte Datenbank bleibt live" in html


def test_send_is_multipart_alternative_without_attachment():
    FakeSMTP.instances.clear()
    subject, html, text = mailer.render_sync_report(RESULT)
    assert mailer.deliver(CONFIG, subject, html, text, smtp_factory=FakeSMTP) == "sent"
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.invalid", 587, 30)
    assert smtp.tls is True
    assert smtp.logged_in == ("absender", PASSWORD)
    (message,) = smtp.messages
    assert message.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]
    assert not any(p.get_content_disposition() == "attachment" for p in message.walk())
    assert "0.5567" in message.get_body(("html",)).get_content()
    assert "http" not in message.get_body(("html",)).get_content()


def test_fallback_file_without_smtp(tmp_path, caplog):
    config = {"MAIL_FALLBACK_DIR": str(tmp_path)}
    with caplog.at_level(logging.WARNING):
        outcome = mailer.deliver(config, "Betreff", "<p>Hallo</p>", "Hallo")
    assert outcome.startswith("file:")
    files = list(tmp_path.glob("sync-mail-*.html"))
    assert len(files) == 1 and files[0].read_text() == "<p>Hallo</p>"
    assert "Kein SMTP" in caplog.text


def test_send_error_falls_back_and_hides_password(tmp_path, caplog):
    config = dict(CONFIG, MAIL_FALLBACK_DIR=str(tmp_path))
    with caplog.at_level(logging.DEBUG):
        outcome = mailer.deliver(
            config, "Betreff", "<p>x</p>", "x", smtp_factory=BrokenSMTP
        )
    assert outcome.startswith("file:")
    assert "nicht gesendet" in caplog.text
    assert PASSWORD not in caplog.text


def test_gnd_error_mail(tmp_path):
    config = {"MAIL_FALLBACK_DIR": str(tmp_path)}
    outcome = mailer.notify_gnd_error(config, "Download fehlgeschlagen")
    path = outcome.removeprefix("file:")
    content = Path(path).read_text(encoding="utf-8")
    assert "GND-Aktualisierung fehlgeschlagen" in content
    assert "Download fehlgeschlagen" in content
