"""Gemeinsames SQLite-Schema für Ingestion (scripts/sync_rvk.py) und Laufzeitzugriff (app/services)."""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notation TEXT UNIQUE NOT NULL,
    label TEXT,
    hierarchy_path TEXT,
    parent_notation TEXT,
    level INTEGER,
    see_also TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_notation_notation ON notation(notation);

CREATE TABLE IF NOT EXISTS register_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notation_id INTEGER NOT NULL REFERENCES notation(id),
    term TEXT NOT NULL,
    entry_type TEXT,
    gnd_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_register_notation_id ON register_entry(notation_id);
CREATE INDEX IF NOT EXISTS idx_register_term ON register_entry(term);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    notation_id UNINDEXED,
    text
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    source_file TEXT,
    notation_count INTEGER,
    register_count INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS expansion_cache (
    query_normalized TEXT PRIMARY KEY,
    expansion_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    day TEXT PRIMARY KEY,
    claude_calls INTEGER NOT NULL DEFAULT 0
);
"""

# MARC-Feldtyp -> menschenlesbarer Registerbegriff-Typ (siehe RVK-MarcXML-Doku, Felder 70X-75X)
REGISTER_ENTRY_TYPES = {
    "700": "Person",
    "710": "Koerperschaft",
    "711": "Konferenz",
    "730": "Werk",
    "750": "Sachschlagwort",
    "751": "Ort",
}


@dataclass
class Notation:
    id: int
    notation: str
    label: str | None
    hierarchy_path: str | None
    parent_notation: str | None
    level: int | None
    see_also: str | None = None
    note: str | None = None


@dataclass
class RegisterEntry:
    id: int
    notation_id: int
    term: str
    entry_type: str | None
    gnd_id: str | None = None


@dataclass
class SearchHit:
    notation: Notation
    score: float
    matched_terms: list[str] = field(default_factory=list)
