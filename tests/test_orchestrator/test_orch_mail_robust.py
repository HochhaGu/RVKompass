"""Ein fehlgeschlagener Mailversand darf das Steuerprogramm nie anhalten."""

import json

from orchestrator.notify import Mail
from orchestrator.outbox import Outbox
from orchestrator.state import FERTIG, PAUSIERT, MemoryStore
from orchestrator.status_page import render_status

from .conftest import FakeMailer


def _queue(eng):
    return Outbox(eng.s.outbox_dir).summary()


def test_failing_mail_does_not_stop_and_is_logged_and_queued(make_engine):
    eng = make_engine(mailer=FakeMailer(fail=True))
    assert eng.run(forever=False) == PAUSIERT  # regulär am Haltepunkt, nicht abgestürzt
    st = eng.store.load()
    assert st["phases"]["P0"]["status"] == "fertig"
    q = _queue(eng)
    assert len(q) == 1 and "Haltepunkt nach P0" in q[0]["subject"]
    assert "timed out" in q[0]["last_error"]
    assert any("Mail nicht gesendet" in line for line in eng.logged)  # Protokoll
    assert any("Mail nicht gesendet" in m["text"] for m in st["messages"])  # Meldung


def test_all_phases_finish_although_every_mail_fails(make_engine, settings):
    ids = [p.id for p in settings.phases]
    store = MemoryStore(ids[0], ids)
    with store.update() as st:
        st["phase"] = ids[-1]
    eng = make_engine(
        store=store, mailer=FakeMailer(fail=True, exc=ConnectionResetError("weg"))
    )
    assert eng.run(forever=False) == FERTIG
    assert len(_queue(eng)) == 1


def test_queued_mail_is_retried_after_ten_minutes(make_engine):
    mailer = FakeMailer(fail=True)
    eng = make_engine(mailer=mailer)
    eng.run(forever=False)
    mailer.fail = False
    eng.clock.sleep(300)
    eng.run(forever=False)  # noch nicht fällig
    assert mailer.sent == [] and len(_queue(eng)) == 1
    eng.clock.sleep(400)
    eng.run(forever=False)
    assert len(mailer.sent) == 1 and _queue(eng) == []
    assert any(
        "Wartende Mail gesendet" in m["text"] for m in eng.store.load()["messages"]
    )


def test_retry_stops_after_first_failure_and_counts_attempts(make_engine):
    mailer = FakeMailer(fail=True)
    eng = make_engine(mailer=mailer)
    for i in range(3):
        eng.outbox.add(
            Mail(f"Mail {i}", "<p>x</p>", "x"), "erster Fehler", now=eng.clock.now()
        )
    eng.clock.sleep(601)
    with eng.store.update() as st:
        st["status"] = PAUSIERT
    eng.run(forever=False)
    assert mailer.attempts == 1  # nicht dreimal 30 s Zeitüberschreitung abwarten
    q = _queue(eng)
    assert [m["attempts"] for m in q] == [2, 1, 1]


def test_broken_mail_template_does_not_crash(make_engine):
    eng = make_engine()

    def broken(*a, **kw):
        raise KeyError("vorlage kaputt")

    eng._mail_pause = broken
    assert eng.run(forever=False) == PAUSIERT
    assert any(
        "Mail konnte nicht erstellt werden" in m["text"]
        for m in eng.store.load()["messages"]
    )


def test_unwritable_outbox_does_not_crash(make_engine, tmp_path):
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    eng = make_engine(mailer=FakeMailer(fail=True))
    eng.outbox = Outbox(
        blocker / "warteschlange", log=eng.logged.append
    )  # Ordner unmöglich
    assert eng.run(forever=False) == PAUSIERT
    assert any("nicht beschreibbar" in line for line in eng.logged)


def test_legacy_outbox_in_state_is_moved(make_engine, settings):
    ids = [p.id for p in settings.phases]
    store = MemoryStore(ids[0], ids)
    with store.update() as st:
        st["status"] = PAUSIERT
        st["outbox"] = [{"subject": "Alt", "html": "<p/>", "text": ""}]
    eng = make_engine(store=store, mailer=FakeMailer(fail=True))
    eng.run(forever=False)
    assert "outbox" not in eng.store.load()
    assert [m["subject"] for m in _queue(eng)] == ["Alt"]


def test_status_page_shows_queue(settings):
    st = MemoryStore("P0", [p.id for p in settings.phases]).load()
    page = render_status(
        st,
        settings,
        [
            {
                "subject": "Eingreifen nötig: P3",
                "attempts": 4,
                "last_error": "SMTP-Fehler: timed out",
                "queued_at": "2026-11-11T10:00:00+00:00",
                "last_try": "2026-11-11T10:30:00+00:00",
            }
        ],
    )
    assert "Mails warten auf Versand (1)" in page
    assert "Eingreifen nötig: P3" in page and "timed out" in page
    assert (
        "11.11.2026 11:40" in page
    )  # nächster Versuch = letzter Versuch + 10 min (Ortszeit)
    assert "Mails warten" not in render_status(st, settings, [])


def test_failed_weekly_letter_goes_to_queue(make_engine, monkeypatch, settings):
    from orchestrator import cli

    eng = make_engine(mailer=FakeMailer(fail=True))
    with eng.store.update() as st:
        st["digest"] = [
            {"phase": "P1", "title": "Evaluationsbasis", "finished_at": "t1"}
        ]
    monkeypatch.setattr(cli, "build_engine", lambda *a, **kw: eng)
    logged = []
    assert cli.cmd_send_digest(settings, logged.append) == 0
    assert [m["subject"] for m in _queue(eng)] == [
        "[RVKompass] Wochenbrief: 1 Phase(n) fertig"
    ]
    assert "Warteschlange" in logged[-1]
    sent = json.loads((settings.logs_dir / "wochenbrief_gesendet.json").read_text())
    assert sent == ["P1@t1"]  # nicht nächste Woche doppelt
