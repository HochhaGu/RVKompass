"""Anonyme Nutzungsstatistik (P8): nur Tageszähler, eigene Datei data/usage.sqlite3.

Gespeichert werden je Tag: Zahl der Suchanfragen, Zahl der Anfragen ohne Treffer, die
Cache-Treffer von GND und Sprachmodell und (seit der Monatsstatistik) die Zahl der GND- und
Sprachmodell-Abfragen insgesamt als Nenner der Cache-Quote. Keine IP-Adressen, keine Suchtexte, kein User-Agent,
keine Cookies. Die Datei liegt getrennt von der RVK-Datenbank, damit sie den monatlichen Tausch
übersteht und nicht in die RVK-Backups gerät.

Nicht zu verwechseln mit der Tabelle `daily_usage` in rvk.sqlite3: Das ist der Tageszähler für
Aufrufe des Sprachmodells (Kostenbremse CLAUDE_DAILY_LIMIT).

Fehler werden protokolliert und nie an die Suche weitergereicht.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_daily (
    day TEXT PRIMARY KEY,
    requests INTEGER NOT NULL DEFAULT 0,
    zero_hit_requests INTEGER NOT NULL DEFAULT 0,
    gnd_cache_hits INTEGER NOT NULL DEFAULT 0,
    llm_cache_hits INTEGER NOT NULL DEFAULT 0,
    gnd_lookups INTEGER NOT NULL DEFAULT 0,
    llm_lookups INTEGER NOT NULL DEFAULT 0
)
"""

COUNTERS = (
    "requests",
    "zero_hit_requests",
    "gnd_cache_hits",
    "llm_cache_hits",
    "gnd_lookups",
    "llm_lookups",
)
# Spalten, die nach P8 dazukamen: ältere Dateien bekommen sie beim ersten Schreiben
ADDED_COLUMNS = ("gnd_lookups", "llm_lookups")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Ergänzt fehlende Zählerspalten (idempotent, bestehende Werte bleiben)."""
    present = existing_columns(conn)
    for column in ADDED_COLUMNS:
        if column not in present:
            conn.execute(
                f"ALTER TABLE usage_daily ADD COLUMN {column} "
                "INTEGER NOT NULL DEFAULT 0"
            )
    conn.commit()


def _add(path: str, day: str, increments: dict[str, int]) -> None:
    values = {k: int(increments.get(k, 0) or 0) for k in COUNTERS}
    if not any(values.values()):
        return
    conn = _connect(path)
    try:
        columns = ", ".join(COUNTERS)
        placeholders = ", ".join("?" for _ in COUNTERS)
        updates = ", ".join(f"{k} = {k} + excluded.{k}" for k in COUNTERS)
        conn.execute(
            f"INSERT INTO usage_daily (day, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(day) DO UPDATE SET {updates}",
            (day, *(values[k] for k in COUNTERS)),
        )
        conn.commit()
    finally:
        conn.close()


def record_search(path: str, hit_count: int, day: str | None = None) -> None:
    """Zählt eine Suchanfrage; ohne Treffer zusätzlich als Null-Treffer-Anfrage."""
    try:
        _add(
            path,
            day or today(),
            {"requests": 1, "zero_hit_requests": 1 if hit_count == 0 else 0},
        )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Nutzungsstatistik nicht beschreibbar: %s", exc)


def record_cache_hits(
    path: str,
    gnd: int = 0,
    llm: int = 0,
    day: str | None = None,
    gnd_lookups: int = 0,
    llm_lookups: int = 0,
) -> None:
    """Cache-Treffer und Abfragen insgesamt (Nenner der Cache-Quote) eines Suchlaufs."""
    try:
        _add(
            path,
            day or today(),
            {
                "gnd_cache_hits": gnd,
                "llm_cache_hits": llm,
                "gnd_lookups": gnd_lookups,
                "llm_lookups": llm_lookups,
            },
        )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Nutzungsstatistik nicht beschreibbar: %s", exc)


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    """Spalten von usage_daily; leer, wenn die Tabelle fehlt."""
    return {row[1] for row in conn.execute("PRAGMA table_info(usage_daily)")}


def _row_to_dict(day: str, row) -> dict:
    values = (
        dict(zip(COUNTERS, row, strict=True)) if row else dict.fromkeys(COUNTERS, 0)
    )
    requests = values["requests"]
    values["day"] = day
    # Anteil als Dezimalzahl 0–1, kein Prozent
    values["zero_hit_rate"] = (
        round(values["zero_hit_requests"] / requests, 4) if requests else 0.0
    )
    return values


def stats_for_day(path: str, day: str | None = None) -> dict:
    """Zähler eines Tages (Standard: heute, UTC); fehlt die Datei, sind alle Werte 0."""
    day = day or today()
    if not Path(path).is_file():
        return _row_to_dict(day, None)
    try:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            # Ältere Datei ohne die neuen Spalten: nur lesen, fehlende Zähler gelten als 0
            present = [c for c in COUNTERS if c in existing_columns(conn)]
            found = conn.execute(
                f"SELECT {', '.join(present)} FROM usage_daily WHERE day = ?", (day,)
            ).fetchone()
            row = (
                tuple(
                    dict(zip(present, found, strict=True)).get(c, 0) for c in COUNTERS
                )
                if found
                else None
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Nutzungsstatistik nicht lesbar: %s", exc)
        row = None
    return _row_to_dict(day, row)


def recent(path: str, days: int = 30, until: str | None = None) -> list[dict]:
    """Die letzten `days` Tage bis einschließlich `until` (neuester zuerst), Lücken mit 0."""
    end = datetime.fromisoformat(until).date() if until else datetime.now(UTC).date()
    return [
        stats_for_day(path, (end - timedelta(days=offset)).isoformat())
        for offset in range(max(days, 0))
    ]
