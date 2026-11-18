"""scripts/monthly_report.py: Vormonat, --month, --dry-run, Versand über den Mailer (gemockt)."""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import monthly_report

from app.services import usage_stats


@pytest.fixture
def config(tmp_path):
    usage = tmp_path / "usage.sqlite3"
    usage_stats.record_search(str(usage), 2, day="2026-10-04")
    return SimpleNamespace(
        USAGE_DATABASE_PATH=str(usage),
        DATABASE_PATH=str(tmp_path / "rvk-fehlt.sqlite3"),
        USAGE_STATS_URL="https://example.invalid/status/statistik",
        MAIL_FALLBACK_DIR=str(tmp_path / "logs"),
    )


def test_default_month_is_previous_month():
    assert monthly_report.default_month(date(2026, 11, 1)) == "2026-10"
    assert monthly_report.default_month(date(2027, 1, 1)) == "2026-12"


def test_dry_run_writes_file_and_sends_nothing(config, tmp_path, capsys):
    calls = []
    code = monthly_report.main(
        ["--month", "2026-10", "--dry-run", "--report-dir", str(tmp_path / "reports")],
        config=config,
        deliver=lambda *a, **k: calls.append(a) or "sent",
    )
    assert code == 0
    assert calls == []
    target = tmp_path / "reports" / "monatsstatistik-2026-10.html"
    html = target.read_text(encoding="utf-8")
    assert "Oktober 2026" in html
    assert "RVK-Stand: unbekannt" in html
    assert "RVKompass · Nutzung Oktober 2026" in capsys.readouterr().out


def test_send_uses_mailer_with_subject(config, tmp_path):
    calls = []

    def fake_deliver(cfg, subject, html, text, **kwargs):
        calls.append((subject, html, text, kwargs))
        return "sent"

    code = monthly_report.main(
        ["--month", "2026-10"], config=config, deliver=fake_deliver
    )
    assert code == 0
    ((subject, html, text, kwargs),) = calls
    assert subject == "RVKompass · Nutzung Oktober 2026"
    assert "https://example.invalid/status/statistik" in html
    assert "Alle Daten: https://example.invalid/status/statistik" in text
    assert kwargs == {"fallback_prefix": "usage-mail"}


def test_fallback_file_counts_as_failure(config):
    code = monthly_report.main(
        ["--month", "2026-10"],
        config=config,
        deliver=lambda *a, **k: "file:/tmp/x.html",
    )
    assert code == 1


def test_bad_month_is_rejected(config):
    with pytest.raises(SystemExit):
        monthly_report.main(["--month", "2026-13"], config=config)


def test_does_not_write_usage_file(config):
    usage = Path(config.USAGE_DATABASE_PATH)
    before = usage.read_bytes()
    monthly_report.build_report(config, "2026-10", today=date(2026, 11, 1))
    assert usage.read_bytes() == before
    assert not Path(config.DATABASE_PATH).exists()
