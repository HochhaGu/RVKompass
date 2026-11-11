"""Lesezugriff auf die lokal synchronisierten RVK-Daten (SQLite, siehe scripts/sync_rvk.py)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from app.models import Notation


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
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
