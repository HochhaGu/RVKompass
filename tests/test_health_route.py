"""GET /health (P8)."""

import sqlite3

from flask import Flask

from app import limiter
from app.models import SCHEMA_SQL
from app.routes import health as health_route
from app.services import gnd_local, usage_stats


def _app(tmp_path, db_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        DATABASE_PATH=str(db_path),
        GND_DATABASE_PATH=str(tmp_path / "gnd.sqlite3"),
        USAGE_DATABASE_PATH=str(tmp_path / "usage.sqlite3"),
    )
    limiter.init_app(app)
    app.register_blueprint(health_route.bp)
    return app.test_client()


def _rvk_db(tmp_path):
    path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT INTO notation (notation, label) VALUES (?, ?)",
        [("ZL", "Maschinenbau"), ("ZL 4620", "Muttern")],
    )
    conn.execute(
        "INSERT INTO sync_log (run_at, source_file, status) "
        "VALUES ('2026-11-10T13:37:59', 'rvko_marcxml_2026_2.xml', 'success')"
    )
    conn.execute(
        "INSERT INTO sync_log (run_at, source_file, status) "
        "VALUES ('2026-11-11T10:00:00', 'x', 'error: kaputt')"
    )
    conn.commit()
    conn.close()
    return path


def test_health_fields(tmp_path):
    db = _rvk_db(tmp_path)
    gconn = sqlite3.connect(tmp_path / "gnd.sqlite3")
    gnd_local.create_schema(gconn)
    gnd_local.write_meta(gconn, "20261013", "dump.jsonld.gz", 3)
    gconn.close()
    usage = str(tmp_path / "usage.sqlite3")
    usage_stats.record_search(usage, 0)
    usage_stats.record_search(usage, 4)
    usage_stats.record_cache_hits(usage, gnd=2, llm=1)

    response = _app(tmp_path, db).get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["rvk_version"] == "2026/2"
    assert data["db_version"] == {"sync_id": 1, "run_at": "2026-11-10T13:37:59"}
    assert data["notation_count"] == 2
    assert data["gnd_version"] == "20261013"
    assert data["cache_hits_today"] == 3
    assert data["requests_today"] == 2
    assert data["zero_hit_rate_today"] == 0.5


def test_health_without_database_is_503(tmp_path):
    response = _app(tmp_path, tmp_path / "fehlt.sqlite3").get("/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"
    assert not (tmp_path / "fehlt.sqlite3").exists()


def test_health_is_exempt_from_rate_limit(tmp_path):
    db = _rvk_db(tmp_path)
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        DATABASE_PATH=str(db),
        USAGE_DATABASE_PATH=str(tmp_path / "usage.sqlite3"),
        RATELIMIT_DEFAULT="2 per hour",
    )
    limiter.init_app(app)
    app.register_blueprint(health_route.bp)
    client = app.test_client()
    assert all(client.get("/health").status_code == 200 for _ in range(5))
