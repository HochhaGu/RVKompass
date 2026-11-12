"""Lesezugriff auf die lokal synchronisierten RVK-Daten (SQLite, siehe scripts/sync_rvk.py)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import Notation


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_readonly_connection(db_path: str) -> sqlite3.Connection | None:
    """Nur lesende Verbindung (Startseite, Footer); legt keine Datei an. Fehlt sie: None."""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_notation(row: sqlite3.Row) -> Notation:
    return Notation(
        id=row["id"],
        notation=row["notation"],
        label=row["label"],
        hierarchy_path=row["hierarchy_path"],
        parent_notation=row["parent_notation"],
        level=row["level"],
        see_also=row["see_also"],
        note=row["note"],
    )


def get_notations_by_ids(
    conn: sqlite3.Connection, notation_ids: list[int]
) -> dict[int, Notation]:
    if not notation_ids:
        return {}
    placeholders = ",".join("?" for _ in notation_ids)
    rows = conn.execute(
        f"SELECT * FROM notation WHERE id IN ({placeholders})", notation_ids
    ).fetchall()
    return {row["id"]: _row_to_notation(row) for row in rows}


def keyword_search(
    conn: sqlite3.Connection, query: str, limit: int = 50
) -> list[tuple[int, float]]:
    """FTS5-Volltextsuche über Klartext, Hierarchie-Pfad und Registerbegriffe je Notation.

    Gibt (notation_id, bm25_score) zurück, aufsteigend nach bm25 (kleiner = relevanter laut FTS5).
    """
    terms = [t.replace('"', "") for t in query.split() if t.strip('"')]
    if not terms:
        return []
    match_expr = " ".join(f'"{t}"*' for t in terms)
    rows = conn.execute(
        "SELECT notation_id, bm25(search_index) AS score FROM search_index "
        "WHERE search_index MATCH ? ORDER BY score LIMIT ?",
        (match_expr, limit),
    ).fetchall()
    return [(row["notation_id"], row["score"]) for row in rows]


def get_notation_ids_for_term(
    conn: sqlite3.Connection, term: str, limit: int = 10
) -> list[tuple[int, str | None, str | None]]:
    """Liefert Notationen zu einem Registerbegriff, begrenzt auf `limit` Treffer.

    Manche Begriffe (z.B. "Regionale Geografie") sind an zehntausende Notationen
    angehängt (RVK-Länder-/Regionenschema) - ohne Limit würde ein einzelner
    Embedding-Treffer auf so einen Begriff die Ranking-Fusion mit irrelevanten
    Massenhits fluten.
    """
    rows = conn.execute(
        "SELECT notation_id, entry_type, gnd_id FROM register_entry WHERE term = ? LIMIT ?",
        (term, limit),
    ).fetchall()
    return [(row["notation_id"], row["entry_type"], row["gnd_id"]) for row in rows]


def find_register_terms(
    conn: sqlite3.Connection, variants
) -> list[tuple[str, str | None]]:
    """Registerbegriffe, die genau einer der Schreibweisen in `variants` entsprechen (P4).

    Liefert (Begriff, GND-ID) je unterschiedlichem Paar. `term IN (...)` nutzt den Index
    idx_register_term; eine Suche mit COLLATE NOCASE würde ihn nicht nutzen und Umlaute nicht
    gleichsetzen. Deshalb übergibt der Aufrufer die Schreibweisen selbst.
    """
    unique = list(dict.fromkeys(v for v in variants if v))
    if not unique:
        return []
    placeholders = ",".join("?" for _ in unique)
    rows = conn.execute(
        "SELECT DISTINCT term, gnd_id FROM register_entry "
        f"WHERE term IN ({placeholders}) LIMIT 50",
        unique,
    ).fetchall()
    return [(row["term"], row["gnd_id"] or None) for row in rows]


GND_ID_BATCH_SIZE = 500


def get_notations_by_gnd_ids(
    conn: sqlite3.Connection, gnd_ids, limit_per_id: int = 10
) -> dict[str, list[int]]:
    """Notationen, deren Registereintrag eine der GND-IDs trägt (ohne Präfix, z. B. "4123456-7").

    Pro GND-ID höchstens `limit_per_id` Notationen – derselbe Schutz vor Massentreffern wie in
    `get_notation_ids_for_term`. Liefert nur Notation-IDs; die Evidence-Objekte baut das Ranking,
    weil nur dort bekannt ist, über welchen Suchbegriff die GND-ID gefunden wurde.
    """
    unique_ids = list(dict.fromkeys(g for g in gnd_ids if g))
    result: dict[str, list[int]] = {}
    for start in range(0, len(unique_ids), GND_ID_BATCH_SIZE):
        batch = unique_ids[start : start + GND_ID_BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            "SELECT gnd_id, notation_id FROM register_entry "
            f"WHERE gnd_id IN ({placeholders}) ORDER BY id",
            batch,
        ).fetchall()
        for row in rows:
            bucket = result.setdefault(row["gnd_id"], [])
            if len(bucket) < limit_per_id and row["notation_id"] not in bucket:
                bucket.append(row["notation_id"])
    return result


def ensure_gnd_cache_table(conn: sqlite3.Connection) -> None:
    """Legt die Laufzeit-Tabelle gnd_cache an, falls der Datenbestand sie noch nicht hat."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gnd_cache (request_key TEXT PRIMARY KEY, "
        "response_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    )


def get_gnd_cache(
    conn: sqlite3.Connection, request_key: str, max_age_days: int
) -> str | None:
    """Gecachte lobid-Antwort; Einträge älter als `max_age_days` gelten als nicht vorhanden."""
    ensure_gnd_cache_table(conn)
    row = conn.execute(
        "SELECT response_json, created_at FROM gnd_cache WHERE request_key = ?",
        (request_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    if datetime.now(UTC) - created > timedelta(days=max_age_days):
        return None
    return row["response_json"]


def set_gnd_cache(
    conn: sqlite3.Connection, request_key: str, response_json: str
) -> None:
    ensure_gnd_cache_table(conn)
    conn.execute(
        "INSERT INTO gnd_cache (request_key, response_json, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(request_key) DO UPDATE SET response_json = excluded.response_json, "
        "created_at = excluded.created_at",
        (request_key, response_json, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def get_register_terms(
    conn: sqlite3.Connection, notation_id: int, limit: int = 3
) -> list[str]:
    rows = conn.execute(
        "SELECT term FROM register_entry WHERE notation_id = ? LIMIT ?",
        (notation_id, limit),
    ).fetchall()
    return [row["term"] for row in rows]


_VERSION_RE = re.compile(r"rvko_marcxml_(\d{4})_(\d+)")


def latest_rvk_version(conn: sqlite3.Connection) -> str | None:
    """RVK-Stand des letzten erfolgreichen Syncs, z. B. "2026/2" (aus `sync_log.source_file`).

    Passt der Dateiname nicht zum Muster von scripts/fetch_marcxml_dump.py, wird das Datum des
    Laufs angezeigt. Ohne Tabelle, ohne erfolgreichen Lauf oder bei Fehlern: None. Nur lesend.
    """
    try:
        row = conn.execute(
            "SELECT run_at, source_file FROM sync_log WHERE status = 'success' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    run_at, source_file = row[0], row[1]
    match = _VERSION_RE.search(source_file or "")
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return (run_at or "")[:10] or None


def get_main_group_labels(
    conn: sqlite3.Connection, codes: Iterable[str]
) -> dict[str, str]:
    """Bezeichnungen der RVK-Hauptgruppen (Zeilen nur aus Buchstaben, z. B. "ZN")."""
    unique = list(dict.fromkeys(c for c in codes if c))
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    try:
        rows = conn.execute(
            f"SELECT notation, label FROM notation WHERE notation IN ({placeholders})",
            unique,
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {row[0]: row[1] for row in rows if row[1]}


# (Datenbankpfad, letzte sync_log-ID) -> Liste der Hauptgruppen
_main_group_cache: dict[tuple[str, int | None], list[tuple[str, str]]] = {}


def _database_path(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return ""
    return (row[2] if row else "") or ""


def _last_sync_id(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(id) FROM sync_log").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def list_main_groups(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Alle RVK-Hauptgruppen als (Code, Bezeichnung), nach Code sortiert.

    Grundlage sind die Zeilen aus genau zwei Großbuchstaben. Fehlen sie, kommen die Codes aus
    den führenden zwei Buchstaben aller Notationen (Bezeichnung leer). Zwischengespeichert je
    Datenbank und Sync-Lauf; In-Memory-Datenbanken werden nicht gespeichert.
    """
    path = _database_path(conn)
    key = (path, _last_sync_id(conn))
    if path and key in _main_group_cache:
        return _main_group_cache[key]
    try:
        rows = conn.execute(
            "SELECT notation, label FROM notation WHERE notation GLOB '[A-Z][A-Z]' "
            "ORDER BY notation"
        ).fetchall()
        groups = [(row[0], row[1] or "") for row in rows]
        if not groups:
            rows = conn.execute(
                "SELECT DISTINCT substr(notation, 1, 2) FROM notation "
                "WHERE notation GLOB '[A-Z][A-Z]*' ORDER BY 1"
            ).fetchall()
            groups = [(row[0], "") for row in rows]
    except sqlite3.Error:
        return []
    if path:
        _main_group_cache[key] = groups
    return groups


def get_change_versions(
    conn: sqlite3.Connection, notation_ids: Iterable[int]
) -> dict[int, str]:
    """Jüngster RVK-Stand mit Änderung je Notation (Tabelle `notation_change`).

    Zählt nur „neu“ und „geändert“; gelöschte Einträge haben keine gültige Notation-ID.
    Fehlt die Tabelle (Datenbestand vor P6), ist das Ergebnis still leer. Nur lesend.
    """
    ids = list(dict.fromkeys(notation_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            "SELECT notation_id, version FROM notation_change "
            f"WHERE notation_id IN ({placeholders}) AND notation_id IS NOT NULL "
            "AND (change_type IS NULL OR change_type != 'deleted') ORDER BY rowid",
            ids,
        ).fetchall()
    except sqlite3.Error:
        return {}
    versions: dict[int, str] = {}
    for row in rows:
        versions[row[0]] = row[1]
    return versions


def get_expansion_cache(conn: sqlite3.Connection, query_normalized: str) -> str | None:
    row = conn.execute(
        "SELECT expansion_json FROM expansion_cache WHERE query_normalized = ?",
        (query_normalized,),
    ).fetchone()
    return row["expansion_json"] if row else None


def set_expansion_cache(
    conn: sqlite3.Connection, query_normalized: str, expansion_json: str
) -> None:
    conn.execute(
        "INSERT INTO expansion_cache (query_normalized, expansion_json, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(query_normalized) DO UPDATE SET expansion_json = excluded.expansion_json, "
        "created_at = excluded.created_at",
        (query_normalized, expansion_json, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def get_and_increment_daily_claude_calls(conn: sqlite3.Connection) -> int:
    """Erhöht den heutigen Claude-API-Call-Zähler und gibt den neuen Stand zurück."""
    today = datetime.now(UTC).date().isoformat()
    conn.execute(
        "INSERT INTO daily_usage (day, claude_calls) VALUES (?, 1) "
        "ON CONFLICT(day) DO UPDATE SET claude_calls = claude_calls + 1",
        (today,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT claude_calls FROM daily_usage WHERE day = ?", (today,)
    ).fetchone()
    return row["claude_calls"]
