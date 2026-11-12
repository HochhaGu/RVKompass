"""Lokaler GND-Abzug (P8): Einlesen des DNB-Abzugs „Sachbegriffe“ und Aufbau von gnd.sqlite3.

Reine Funktionen ohne Netz. Der Download steht in scripts/fetch_gnd_dump.py, der Aufbau samt
Prüfung und Tausch in scripts/sync_gnd.py, die Abfrage zur Laufzeit in
app/services/expansion/gnd.py (`LocalGndSource`).

Format des Abzugs (am echten Abzug 20261013 geprüft, 12.11.2026):
- JSON-LD in erweiterter Form: Schlüssel sind volle URIs
  („https://d-nb.info/standards/elementset/gnd#preferredNameForTheSubjectHeading“), Werte
  Listen aus {"@value": …} oder {"@id": …}.
- Die Datei ist ein großes Array aus Arrays: je Datensatz ein inneres Array mit dem Hauptknoten
  (`@id` = https://d-nb.info/gnd/<id>) und Hilfsknoten (`@id` = "_:…", z. B. Quellenangaben zu
  Namensvarianten). Gelesen wird schrittweise, ohne die Datei ganz in den Speicher zu laden.
- Texte sind „UTF-8 decomposed“ (NFD) und werden hier auf NFC umgestellt, damit „Mütter“ aus
  dem Abzug und „Mütter“ aus der Eingabe gleich sind.

Feldnamen laut GND-Ontologie (https://d-nb.info/standards/elementset/gnd): gndIdentifier,
preferredNameForTheSubjectHeading, variantNameForTheSubjectHeading,
broaderTermGeneral/-Generic/-Partitive/-Instantial, relatedTerm, gndSubjectCategory.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TextIO

from app.services.query_expansion import normalize_query

BROADER_KEYS = (
    "broaderTermGeneral",
    "broaderTermGeneric",
    "broaderTermPartitive",
    "broaderTermInstantial",
)
GND_URI_PREFIX = "https://d-nb.info/gnd/"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gnd_subject (
    gnd_id TEXT PRIMARY KEY,
    uri TEXT NOT NULL,
    preferred_name TEXT NOT NULL,
    categories_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS gnd_name (
    gnd_id TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    is_preferred INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gnd_name_norm ON gnd_name(name_norm);
CREATE TABLE IF NOT EXISTS gnd_relation (
    gnd_id TEXT NOT NULL,
    target_gnd_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('broader', 'related')),
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gnd_relation_gnd_id ON gnd_relation(gnd_id);
CREATE INDEX IF NOT EXISTS idx_gnd_relation_target ON gnd_relation(target_gnd_id);
CREATE TABLE IF NOT EXISTS gnd_variant (
    gnd_id TEXT NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gnd_variant_gnd_id ON gnd_variant(gnd_id);
CREATE TABLE IF NOT EXISTS gnd_category_label (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gnd_meta (
    version TEXT NOT NULL,
    source_file TEXT,
    record_count INTEGER NOT NULL,
    loaded_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class GndRecordRow:
    """Ein Sachbegriff aus dem Abzug, reduziert auf das, was die Erweiterung braucht."""

    gnd_id: str
    uri: str
    preferred_name: str
    variant_names: tuple[str, ...] = ()
    broader_ids: tuple[str, ...] = ()
    related_ids: tuple[str, ...] = ()
    category_codes: tuple[str, ...] = ()


# --- JSON-LD-Hilfen ------------------------------------------------------------------------


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip()


def normalize_key(key: str) -> str:
    """Namensraum entfernen: alles bis zum letzten "#", "/" oder ":"."""
    return re.split(r"[#/:]", str(key))[-1]


def values(value) -> list[str]:
    """Werte eines JSON-LD-Felds: {"@value"}, {"@id"}, Zeichenkette oder Liste davon."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    result = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("@value", item.get("@id"))
        if item is None:
            continue
        text = nfc(item)
        if text:
            result.append(text)
    return result


def gnd_id_from_uri(uri: str) -> str:
    return str(uri or "").rstrip("/").rsplit("/", 1)[-1].strip()


def _first(values_list: list[str]) -> str:
    return values_list[0] if values_list else ""


def name_norm(name: str) -> str:
    """Vergleichsschlüssel wie bei `matches_query`: Zusatz <…> ab, dann normalize_query."""
    # Import hier, damit app.services.expansion.gnd dieses Modul ohne Zyklus nutzen kann
    from app.services.expansion.gnd import split_qualifier

    return normalize_query(split_qualifier(nfc(name))[0])


def record_from_jsonld(node: dict) -> GndRecordRow | None:
    """Hauptknoten eines Sachbegriffs -> GndRecordRow; Hilfsknoten und Unpassendes -> None."""
    if not isinstance(node, dict):
        return None
    uri = str(node.get("@id") or "")
    if not uri.startswith(GND_URI_PREFIX):
        return None
    fields: dict[str, list[str]] = {}
    for key, raw in node.items():
        if key.startswith("@"):
            continue
        fields.setdefault(normalize_key(key), []).extend(values(raw))
    preferred = _first(fields.get("preferredNameForTheSubjectHeading", []))
    if not preferred:
        return None
    gnd_id = _first(fields.get("gndIdentifier", [])) or gnd_id_from_uri(uri)
    variants = tuple(dict.fromkeys(fields.get("variantNameForTheSubjectHeading", [])))
    broader: list[str] = []
    for key in BROADER_KEYS:
        for target in fields.get(key, []):
            target_id = gnd_id_from_uri(target)
            if target_id and target_id not in broader:
                broader.append(target_id)
    related = tuple(
        dict.fromkeys(gnd_id_from_uri(t) for t in fields.get("relatedTerm", []) if t)
    )
    categories = tuple(
        dict.fromkeys(
            c.rsplit("#", 1)[-1] for c in fields.get("gndSubjectCategory", []) if c
        )
    )
    return GndRecordRow(
        gnd_id=gnd_id,
        uri=uri,
        preferred_name=preferred,
        variant_names=variants,
        broader_ids=tuple(broader),
        related_ids=related,
        category_codes=categories,
    )


# --- schrittweises Lesen -------------------------------------------------------------------

_SKIP = set(" \t\r\n,[]")


def iter_json_objects(stream: TextIO, chunk_size: int = 1 << 20) -> Iterator[dict]:
    """Alle JSON-Objekte aus (beliebig tief verschachtelten) Arrays oder NDJSON, schrittweise.

    Klammern "[", "]", Kommas und Leerraum zwischen den Objekten werden übersprungen; jedes
    Objekt wird per `json.JSONDecoder.raw_decode` gelesen. Reicht der Puffer nicht, wird
    nachgeladen. So passen NDJSON, ein Array und ein Array aus Arrays.
    """
    decoder = json.JSONDecoder()
    buffer = ""
    pos = 0
    eof = False
    while True:
        while pos < len(buffer) and buffer[pos] in _SKIP:
            pos += 1
        if pos >= len(buffer):
            if eof:
                return
            buffer = stream.read(chunk_size)
            pos = 0
            if not buffer:
                return
            continue
        if buffer[pos] != "{":
            raise ValueError(f"Unerwartetes Zeichen im Abzug: {buffer[pos]!r}")
        try:
            obj, end = decoder.raw_decode(buffer, pos)
        except json.JSONDecodeError:
            if eof:
                raise
            more = stream.read(chunk_size)
            if not more:
                eof = True
            buffer = buffer[pos:] + more
            pos = 0
            continue
        yield obj
        pos = end
        if pos > chunk_size:
            buffer = buffer[pos:]
            pos = 0


def iter_records(stream: TextIO) -> Iterator[GndRecordRow]:
    for obj in iter_json_objects(stream):
        record = record_from_jsonld(obj)
        if record is not None:
            yield record


# --- Sachgruppen-Vokabular -----------------------------------------------------------------

_NT_RE = re.compile(
    r'^<([^>]+)>\s+<([^>]+)>\s+"((?:[^"\\]|\\.)*)"@([A-Za-z-]+)\s*\.\s*$'
)
PREF_LABEL = "http://www.w3.org/2004/02/skos/core#prefLabel"


def _unescape_nt(text: str) -> str:
    return json.loads(f'"{text}"')


def parse_gnd_sc_ntriples(lines: Iterable[str], lang: str = "de") -> dict[str, str]:
    """Sachgruppen-Code -> deutsche Bezeichnung (skos:prefLabel @de) aus gnd-sc.nt."""
    labels: dict[str, str] = {}
    for line in lines:
        match = _NT_RE.match(line.strip())
        if not match:
            continue
        subject, predicate, literal, language = match.groups()
        if predicate != PREF_LABEL or language.lower() != lang:
            continue
        code = subject.rsplit("#", 1)[-1]
        if code and code not in labels:
            labels[code] = nfc(_unescape_nt(literal))
    return labels


# --- Datenbank ------------------------------------------------------------------------------


@dataclass
class BuildStats:
    record_count: int = 0
    name_count: int = 0
    relation_count: int = 0
    skipped: int = 0
    extra: dict = field(default_factory=dict)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def insert_records(
    conn: sqlite3.Connection, records: Iterable[GndRecordRow], commit_every: int = 20000
) -> BuildStats:
    stats = BuildStats()
    for record in records:
        cur = conn.execute(
            "INSERT OR IGNORE INTO gnd_subject (gnd_id, uri, preferred_name, categories_json) "
            "VALUES (?, ?, ?, ?)",
            (
                record.gnd_id,
                record.uri,
                record.preferred_name,
                json.dumps(list(record.category_codes)),
            ),
        )
        if cur.rowcount == 0:
            stats.skipped += 1
            continue
        stats.record_count += 1
        names = {name_norm(record.preferred_name): 1}
        for variant in record.variant_names:
            names.setdefault(name_norm(variant), 0)
        for norm, preferred in names.items():
            if norm:
                conn.execute(
                    "INSERT INTO gnd_name (gnd_id, name_norm, is_preferred) VALUES (?, ?, ?)",
                    (record.gnd_id, norm, preferred),
                )
                stats.name_count += 1
        for position, variant in enumerate(record.variant_names):
            conn.execute(
                "INSERT INTO gnd_variant (gnd_id, name, position) VALUES (?, ?, ?)",
                (record.gnd_id, variant, position),
            )
        for relation, targets in (
            ("broader", record.broader_ids),
            ("related", record.related_ids),
        ):
            for position, target in enumerate(targets):
                conn.execute(
                    "INSERT INTO gnd_relation (gnd_id, target_gnd_id, relation, position) "
                    "VALUES (?, ?, ?, ?)",
                    (record.gnd_id, target, relation, position),
                )
                stats.relation_count += 1
        if stats.record_count % commit_every == 0:
            conn.commit()
    conn.commit()
    return stats


def insert_category_labels(conn: sqlite3.Connection, labels: dict[str, str]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO gnd_category_label (code, label) VALUES (?, ?)",
        sorted(labels.items()),
    )
    conn.commit()


def write_meta(
    conn: sqlite3.Connection, version: str, source_file: str, record_count: int
) -> None:
    conn.execute("DELETE FROM gnd_meta")
    conn.execute(
        "INSERT INTO gnd_meta (version, source_file, record_count, loaded_at) "
        "VALUES (?, ?, ?, ?)",
        (version, source_file, record_count, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def read_meta(conn: sqlite3.Connection) -> dict | None:
    try:
        row = conn.execute(
            "SELECT version, source_file, record_count, loaded_at FROM gnd_meta "
            "ORDER BY loaded_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return {
        "version": row[0],
        "source_file": row[1],
        "record_count": row[2],
        "loaded_at": row[3],
    }
