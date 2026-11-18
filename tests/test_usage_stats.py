"""Anonyme Nutzungsstatistik (app/services/usage_stats.py)."""

import sqlite3

from app.services import usage_stats


def test_counts_requests_and_zero_hits(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    usage_stats.record_search(path, 5, day="2026-11-12")
    usage_stats.record_search(path, 0, day="2026-11-12")
    usage_stats.record_search(path, 0, day="2026-11-12")
    usage_stats.record_search(path, 3, day="2026-11-12")
    stats = usage_stats.stats_for_day(path, "2026-11-12")
    assert stats["requests"] == 4
    assert stats["zero_hit_requests"] == 2
    assert stats["zero_hit_rate"] == 0.5


def test_cache_hits_and_day_change(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    usage_stats.record_cache_hits(path, gnd=2, llm=1, day="2026-11-12")
    usage_stats.record_cache_hits(path, gnd=1, day="2026-11-13")
    usage_stats.record_cache_hits(path, day="2026-11-13")  # nichts zu zählen
    assert usage_stats.stats_for_day(path, "2026-11-12")["gnd_cache_hits"] == 2
    assert usage_stats.stats_for_day(path, "2026-11-12")["llm_cache_hits"] == 1
    day2 = usage_stats.stats_for_day(path, "2026-11-13")
    assert (day2["gnd_cache_hits"], day2["requests"]) == (1, 0)
    assert day2["zero_hit_rate"] == 0.0


def test_recent_fills_gaps(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    usage_stats.record_search(path, 1, day="2026-11-10")
    days = usage_stats.recent(path, 3, until="2026-11-12")
    assert [d["day"] for d in days] == ["2026-11-12", "2026-11-11", "2026-11-10"]
    assert [d["requests"] for d in days] == [0, 0, 1]


def test_missing_file_reads_as_zero(tmp_path):
    path = str(tmp_path / "fehlt.sqlite3")
    assert usage_stats.stats_for_day(path, "2026-11-12")["requests"] == 0
    assert not (tmp_path / "fehlt.sqlite3").exists()


def test_schema_has_no_personal_columns(tmp_path):
    path = tmp_path / "usage.sqlite3"
    usage_stats.record_search(str(path), 1)
    conn = sqlite3.connect(path)
    columns = {r[1].lower() for r in conn.execute("PRAGMA table_info(usage_daily)")}
    conn.close()
    assert columns == {
        "day",
        "requests",
        "zero_hit_requests",
        "gnd_cache_hits",
        "llm_cache_hits",
        "gnd_lookups",
        "llm_lookups",
    }
    assert not any(word in c for c in columns for word in ("ip", "query", "agent"))


def test_errors_are_swallowed(tmp_path):
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    # Verzeichnis kann nicht angelegt werden, weil "datei" eine Datei ist
    usage_stats.record_search(str(blocker / "usage.sqlite3"), 0)
    usage_stats.record_cache_hits(str(blocker / "usage.sqlite3"), gnd=1)


OLD_SCHEMA = """
CREATE TABLE usage_daily (
    day TEXT PRIMARY KEY,
    requests INTEGER NOT NULL DEFAULT 0,
    zero_hit_requests INTEGER NOT NULL DEFAULT 0,
    gnd_cache_hits INTEGER NOT NULL DEFAULT 0,
    llm_cache_hits INTEGER NOT NULL DEFAULT 0
)
"""


def _old_file(path):
    conn = sqlite3.connect(path)
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO usage_daily VALUES ('2026-11-12', 4, 1, 2, 3)")
    conn.commit()
    conn.close()


def test_lookups_are_counted(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    usage_stats.record_cache_hits(
        path, gnd=1, llm=2, gnd_lookups=4, llm_lookups=5, day="2026-11-12"
    )
    usage_stats.record_cache_hits(path, gnd_lookups=1, day="2026-11-12")
    stats = usage_stats.stats_for_day(path, "2026-11-12")
    assert (stats["gnd_lookups"], stats["llm_lookups"]) == (5, 5)
    assert (stats["gnd_cache_hits"], stats["llm_cache_hits"]) == (1, 2)


def test_old_file_is_migrated_on_write_and_keeps_values(tmp_path):
    path = tmp_path / "usage.sqlite3"
    _old_file(path)
    usage_stats.record_cache_hits(str(path), gnd_lookups=2, day="2026-11-12")
    usage_stats.record_search(
        str(path), 0, day="2026-11-12"
    )  # zweite Migration: nichts
    stats = usage_stats.stats_for_day(str(path), "2026-11-12")
    assert stats["requests"] == 5
    assert stats["zero_hit_requests"] == 2
    assert (stats["gnd_cache_hits"], stats["llm_cache_hits"]) == (2, 3)
    assert (stats["gnd_lookups"], stats["llm_lookups"]) == (2, 0)


def test_old_file_is_read_without_change(tmp_path):
    path = tmp_path / "usage.sqlite3"
    _old_file(path)
    before = path.read_bytes()
    stats = usage_stats.stats_for_day(str(path), "2026-11-12")
    assert stats["requests"] == 4
    assert (stats["gnd_lookups"], stats["llm_lookups"]) == (0, 0)
    assert path.read_bytes() == before
