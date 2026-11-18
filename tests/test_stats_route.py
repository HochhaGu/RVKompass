"""GET /status/statistik und /status/statistik.csv (Flask-Test-Client)."""

import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from app import limiter, register_template_context
from app.models import SCHEMA_SQL
from app.routes import stats as stats_route
from app.services import usage_report, usage_stats

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture
def client(tmp_path):
    usage = tmp_path / "usage.sqlite3"
    usage_stats.record_search(str(usage), 3, day="2026-10-30")
    usage_stats.record_search(str(usage), 0, day="2026-10-30")
    usage_stats.record_search(str(usage), 1, day="2026-11-02")
    usage_stats.record_cache_hits(str(usage), gnd=1, gnd_lookups=2, day="2026-11-02")
    rvk = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(rvk)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO daily_usage VALUES ('2026-11-02', 4)")
    conn.commit()
    conn.close()

    app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=None)
    app.add_url_rule("/static/<path:filename>", "static", lambda filename: "")
    app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        DATABASE_PATH=str(rvk),
        USAGE_DATABASE_PATH=str(usage),
    )
    limiter.init_app(app)
    app.register_blueprint(stats_route.bp)
    register_template_context(app)
    return app.test_client()


def test_page_lists_months_with_days(client):
    response = client.get("/status/statistik")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]
    html = response.get_data(as_text=True)
    assert "Nutzungsstatistik" in html
    assert "November 2026" in html and "Oktober 2026" in html
    # neuester Monat zuerst
    assert html.index("November 2026") < html.index("Oktober 2026")
    assert html.count("<details") >= 2
    assert "Tageswerte Oktober 2026" in html
    assert "30.10.2026" in html
    assert 'href="/status/statistik.csv?ebene=tag"' in html
    assert "50,0 %" in html  # Oktober: 1 von 2 Suchen ohne Treffer


def test_page_without_data(tmp_path):
    app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=None)
    app.add_url_rule("/static/<path:filename>", "static", lambda filename: "")
    app.config.update(
        TESTING=True,
        DATABASE_PATH=str(tmp_path / "fehlt.sqlite3"),
        USAGE_DATABASE_PATH=str(tmp_path / "fehlt-auch.sqlite3"),
    )
    app.register_blueprint(stats_route.bp)
    register_template_context(app)
    html = app.test_client().get("/status/statistik").get_data(as_text=True)
    assert "Noch keine Daten." in html
    assert list(tmp_path.iterdir()) == []


def _csv(client, query):
    response = client.get(f"/status/statistik.csv{query}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-store"
    raw = response.get_data()
    assert raw.startswith("﻿".encode())
    return response, raw.decode("utf-8-sig").split("\r\n")


def test_csv_month_level_is_default(client):
    response, lines = _csv(client, "")
    assert "rvkompass-statistik-monat.csv" in response.headers["Content-Disposition"]
    assert lines[0] == ";".join(usage_report.MONTH_CSV_FIELDS)
    assert lines[1].startswith("2026-10;2;")
    assert lines[2].split(";")[0] == "2026-11"
    # November: GND-Quote 1/2, Claude-Aufrufe aus rvk.sqlite3
    november = lines[2].split(";")
    assert november[6] == "0.5000" and november[8] == "4"


def test_csv_day_level(client):
    response, lines = _csv(client, "?ebene=tag")
    assert "rvkompass-statistik-tag.csv" in response.headers["Content-Disposition"]
    assert lines[0] == ";".join(usage_report.DAY_CSV_FIELDS)
    assert lines[1] == "2026-10-30;2;1;0;0;0;0;0"
    assert "2026-11-02;1;0;2;1;0;0;4" in lines
    assert lines[-1] == ""
    # keine Suchbegriffe, keine IP-Adressen: nur Datum und Zahlen
    for line in lines[1:-1]:
        day, *numbers = line.split(";")
        assert len(day) == 10 and all(n.isdigit() for n in numbers)


def test_csv_rejects_unknown_level(client):
    assert client.get("/status/statistik.csv?ebene=xyz").status_code == 400
