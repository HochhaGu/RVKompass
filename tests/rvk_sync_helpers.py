"""Hilfen für die Tests des RVK-Abgleichs: kleine Datenbanken mit altem und neuem Stand."""

import sqlite3
from pathlib import Path

from app.models import SCHEMA_SQL

NEW_TABLES_SQL = """
CREATE TABLE notation_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT, notation TEXT UNIQUE NOT NULL, label TEXT,
    hierarchy_path TEXT, parent_notation TEXT, level INTEGER, see_also TEXT, note TEXT
);
CREATE TABLE register_entry_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT, notation_id INTEGER NOT NULL, term TEXT NOT NULL,
    entry_type TEXT, gnd_id TEXT
);
CREATE VIRTUAL TABLE search_index_new USING fts5(notation_id UNINDEXED, text);
"""


def insert_notations(conn, table, register_table, rows):
    """rows: (notation, label, [(term, entry_type, gnd_id), …])"""
    for notation, label, register in rows:
        cur = conn.execute(
            f"INSERT INTO {table} (notation, label, hierarchy_path, level) "
            "VALUES (?, ?, ?, ?)",
            (notation, label, "Pfad", 1),
        )
        for term, entry_type, gnd_id in register:
            conn.execute(
                f"INSERT INTO {register_table} (notation_id, term, entry_type, gnd_id) "
                "VALUES (?, ?, ?, ?)",
                (cur.lastrowid, term, entry_type, gnd_id),
            )
    conn.commit()


OLD_ROWS = [
    ("ZL", "Maschinenbau", []),
    ("ZM", "Werkstoffe", []),
    ("ZL 4620", "Muttern", [("Mutter", "Sachschlagwort", "1-1")]),
    ("ZL 4630", "Schrauben", [("Schraube", "Sachschlagwort", "2-2")]),
    ("ZL 9999", "Veraltet", []),
    ("ZM 1000", "Stahl", [("Stahl", "Sachschlagwort", "3-3")]),
]

NEW_ROWS = [
    ("ZL", "Maschinenbau", []),
    ("ZM", "Werkstoffe", []),
    # neue Reihenfolge: andere IDs als im alten Stand
    ("ZL 0001", "Neu hier", []),
    # Benennung geändert
    ("ZL 4620", "Muttern und Scheiben", [("Mutter", "Sachschlagwort", "1-1")]),
    ("ZL 4630", "Schrauben", [("Schraube", "Sachschlagwort", "2-2")]),
    # Register geändert
    (
        "ZM 1000",
        "Stahl",
        [("Stahl", "Sachschlagwort", "3-3"), ("Baustahl", None, None)],
    ),
]


def make_db(path: Path, old_rows=OLD_ROWS, new_rows=NEW_ROWS, sync_file=None):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    if old_rows:
        insert_notations(conn, "notation", "register_entry", old_rows)
    if sync_file:
        conn.execute(
            "INSERT INTO sync_log (run_at, source_file, notation_count, register_count, "
            "status) VALUES ('2026-10-01T00:00:00', ?, ?, 0, 'success')",
            (sync_file, len(old_rows or [])),
        )
    if new_rows is not None:
        conn.executescript(NEW_TABLES_SQL)
        insert_notations(conn, "notation_new", "register_entry_new", new_rows)
    conn.commit()
    return conn
