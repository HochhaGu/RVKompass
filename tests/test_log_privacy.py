"""Logs ohne Suchbegriffe und IP-Adressen: Filter, Fehlerart, Logstellen, gunicorn, Compose."""

import json
import logging
import runpy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app.models import SCHEMA_SQL
from app.services import log_privacy, rvk_repository
from app.services import query_expansion as qe
from app.services.expansion import gnd, llm

ROOT = Path(__file__).resolve().parent.parent
TERM = "Geheimbegriff"


@pytest.fixture
def conn():
    connection = rvk_repository.get_connection(":memory:")
    connection.executescript(SCHEMA_SQL)
    yield connection
    connection.close()


# --- redact / error_kind --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "ratelimit 20 per 1 minute (203.0.113.7) exceeded",
            "ratelimit 20 per 1 minute (<IP>) exceeded",
        ),
        (
            "Invalid request from ip=2001:db8::1: kaputt",
            "Invalid request from ip=<IP>: kaputt",
        ),
        ("von fe80:0:0:0:abcd:1:2:3 her", "von <IP> her"),
        (
            "GET https://lobid.org/gnd/search?q=Getriebe&format=json",
            "GET https://lobid.org/gnd/search?<…>",
        ),
        (
            "Error handling request /search?q=Mutter",
            "Error handling request /search?<…>",
        ),
    ],
)
def test_redact_removes_ips_and_query_strings(text, expected):
    assert log_privacy.redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-12 10:30:45,123 WARNING app: RVK-Stand 2026_10",
        "Tageslimit für Claude-Calls erreicht (12/50)",
        "Version 2.0.3 geladen",
    ],
)
def test_redact_keeps_harmless_text(text):
    assert log_privacy.redact(text) == text


def test_error_kind_never_contains_message():
    assert log_privacy.error_kind(RuntimeError(TERM)) == "RuntimeError"
    exc = requests.HTTPError(f"500 for url ?q={TERM}")
    exc.response = SimpleNamespace(status_code=500)
    assert log_privacy.error_kind(exc) == "HTTPError (HTTP 500)"
    api = RuntimeError(TERM)
    api.status_code = 429
    assert log_privacy.error_kind(api) == "RuntimeError (HTTP 429)"


# --- Filter und Installation ----------------------------------------------------------------


def test_filter_redacts_message_args_and_traceback():
    try:
        raise ValueError("Antwort von 198.51.100.4 für /x?q=Mutter")
    except ValueError:
        record = logging.LogRecord(
            "app",
            logging.ERROR,
            __file__,
            1,
            "Client %s",
            ("192.0.2.1",),
            sys_exc_info(),
        )
    assert log_privacy.RedactingFilter().filter(record) is True
    text = logging.Formatter().format(record)
    assert "192.0.2.1" not in text and "198.51.100.4" not in text
    assert "Mutter" not in text
    assert "Client <IP>" in text and "ValueError" in text


def sys_exc_info():
    import sys

    return sys.exc_info()


def test_install_filters_handlers_once_and_quiets_limiter():
    logger = logging.getLogger("test_log_privacy_install")
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    limiter_logger = logging.getLogger(log_privacy.LIMITER_LOGGER)
    previous = limiter_logger.level
    try:
        log_privacy.install(logger)
        log_privacy.install(logger)
        assert (
            sum(isinstance(f, log_privacy.RedactingFilter) for f in handler.filters)
            == 1
        )
        assert not limiter_logger.isEnabledFor(logging.INFO)
    finally:
        logger.removeHandler(handler)
        limiter_logger.setLevel(previous)


def test_limiter_message_with_ip_is_not_logged(caplog):
    limiter_logger = logging.getLogger(log_privacy.LIMITER_LOGGER)
    previous = limiter_logger.level
    try:
        log_privacy.quiet_limiter_logger()
        with caplog.at_level(logging.DEBUG):
            limiter_logger.setLevel(
                logging.WARNING
            )  # caplog.at_level setzt nur den Root-Logger
            limiter_logger.info(
                "ratelimit %s (%s) exceeded at endpoint: %s",
                "20/min",
                "203.0.113.7",
                "search",
            )
        assert "203.0.113.7" not in caplog.text
    finally:
        limiter_logger.setLevel(previous)


def test_gunicorn_hook_filters_error_logger():
    conf = runpy.run_path(str(ROOT / "docker" / "gunicorn.conf.py"))
    error_logger = logging.getLogger("gunicorn.error")
    limiter_logger = logging.getLogger(log_privacy.LIMITER_LOGGER)
    previous = limiter_logger.level
    try:
        conf["on_starting"](None)
        assert any(
            isinstance(f, log_privacy.RedactingFilter) for f in error_logger.filters
        )
        record = error_logger.makeRecord(
            "gunicorn.error",
            logging.WARNING,
            __file__,
            1,
            "Invalid request from ip={ip}: x".format(ip="203.0.113.9"),
            None,
            None,
        )
        assert error_logger.filter(record)
        assert "203.0.113.9" not in record.getMessage()
    finally:
        for f in list(error_logger.filters):
            error_logger.removeFilter(f)
        limiter_logger.setLevel(previous)


def test_compose_limits_container_log():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "driver: json-file" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "3"' in compose


def test_privacy_page_mentions_error_log():
    page = (ROOT / "app" / "templates" / "datenschutz.html").read_text(encoding="utf-8")
    assert "Fehlerprotokoll" in page and "ohne Suchbegriffe" in page


# --- Logstellen: kein Suchbegriff, nur Fehlerart und Länge ----------------------------------


class FailingSource:
    def search_subject_headings(self, query, size):
        raise RuntimeError(f"lobid kaputt für ?q={query} ({query})")

    def find_narrower(self, record_id, limit):
        return []


def test_lobid_failure_log_has_no_term(conn, caplog):
    def http_get(url, params=None, timeout=None, headers=None):
        raise requests.ConnectionError(
            f"Verbindung zu {url}?q={params['q']} fehlgeschlagen"
        )

    source = gnd.LobidGndSource(conn, http_get=http_get)
    with caplog.at_level(logging.WARNING):
        assert source.search_subject_headings(TERM, 10) == []
    assert TERM not in caplog.text
    assert "ConnectionError" in caplog.text


def test_expand_gnd_failure_log_has_only_kind_and_length(caplog):
    with caplog.at_level(logging.WARNING):
        assert gnd.expand_gnd(FailingSource(), TERM) == []
    assert TERM not in caplog.text
    assert f"{len(TERM)} Zeichen" in caplog.text and "RuntimeError" in caplog.text


def test_llm_gnd_check_failure_log_has_no_term(caplog):
    with caplog.at_level(logging.WARNING):
        assert llm.gnd_match(FailingSource(), TERM) is None
    assert TERM not in caplog.text
    assert f"{len(TERM)} Zeichen" in caplog.text


def test_llm_register_check_failure_log_has_no_term(conn, monkeypatch, caplog):
    def broken(conn, term):
        raise sqlite3.OperationalError(f"kaputt bei {term}")

    monkeypatch.setattr(llm, "register_match", broken)
    raw = [{"begriff": TERM, "beziehung": "verwandt", "begruendung": "weil"}]
    with caplog.at_level(logging.WARNING):
        (suggestion,) = llm.verify_suggestions(conn, raw, None)
    assert suggestion.verified is False
    assert TERM not in caplog.text
    assert "OperationalError" in caplog.text


class FakeClient:
    def __init__(self, exc):
        self.exc = exc
        self.messages = self

    def create(self, **kwargs):
        raise self.exc


def test_claude_failure_log_has_no_term(conn, caplog):
    client = FakeClient(RuntimeError(f"Anfrage zu {TERM} abgelehnt"))
    with caplog.at_level(logging.WARNING):
        result = qe.suggest_terms(
            conn,
            "sk-test",
            TERM,
            client_factory=lambda *a: client,
            use_output_config=True,
        )
    assert result == []
    assert TERM not in caplog.text and TERM.lower() not in caplog.text
    assert "RuntimeError" in caplog.text


def test_unreadable_llm_cache_log_has_no_term(conn, caplog):
    key = qe.CACHE_PREFIX + qe.normalize_query(TERM)
    rvk_repository.set_expansion_cache(conn, key, json.dumps({"kaputt": True}))
    with caplog.at_level(logging.WARNING):
        qe.suggest_terms(conn, "", TERM)
    assert "Unlesbarer LLM-Cache-Eintrag" in caplog.text
    assert TERM not in caplog.text and TERM.lower() not in caplog.text
