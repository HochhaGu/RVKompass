"""Parst den RVK-MarcXML-Gesamtabzug und befüllt die lokale SQLite-Datenbank.

Ablauf: MarcXML-Datei (data/raw/rvko_marcxml_*.xml, siehe fetch_marcxml_dump.py) wird
per Streaming-Parse (lxml.etree.iterparse) gelesen, um bei ~1.3 GB Dateigröße mit wenig
RAM auszukommen. Aufbau erfolgt in _new-Tabellen, die am Ende atomar eingetauscht werden
(idempotenter Rebuild statt Delta-Merge, siehe Plan).
"""

from __future__ import annotations

import glob
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import REGISTER_ENTRY_TYPES, SCHEMA_SQL

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rvk.sqlite3"

MARC_NS = "{http://www.loc.gov/MARC21/slim}"
REGISTER_TAGS = set(REGISTER_ENTRY_TYPES.keys())


def find_dump_file() -> Path:
    candidates = sorted(
        p
        for p in glob.glob(str(RAW_DIR / "rvko_marcxml_*.xml"))
        if not p.endswith(".update.xml")
    )
    if not candidates:
        raise FileNotFoundError(
            f"Kein MarcXML-Gesamtabzug in {RAW_DIR} gefunden. Erst scripts/fetch_marcxml_dump.py ausführen."
        )
    return Path(candidates[-1])


def _subfields(datafield):
    """Liefert (code, value) für jedes <subfield> eines <datafield> in Dokumentreihenfolge."""
    for sub in datafield.findall(f"{MARC_NS}subfield"):
        yield sub.get("code"), (sub.text or "").strip()


def parse_153(datafield):
    """Extrahiert eigene Notation(-sspanne)/Label sowie die Hierarchie-Kette (Wurzel -> direkter Elternteil).

    Reihenfolge im MarcXML: erst $a (Notation, Spannenanfang), optional $c (Spannenende) und
    $j (Label), danach wiederholte Gruppen $e [$f] $h für jede Vorfahren-Ebene, von der Wurzel
    absteigend zum direkten Elternteil.

    WICHTIG (verifiziert an echten Datensätzen des Gesamtabzugs, nicht wie die Kurzdoku
    vermuten lässt): $c ist NICHT die Benennung, sondern das MARC21-Klassifikations-Subfeld
    "end of span" - viele Notationen sind Bereiche (z.B. $a=AH 20000, $c=AH 26498 für
    "Romanische Sprachen"). Ohne $c einzubeziehen kollidieren zehntausende Notationen, da
    derselbe Bereichsanfang mehrfach mit unterschiedlichem Ende und Label vorkommt.
    """
    own_start = None
    own_end = None
    own_label = None
    ancestor_labels = []
    ancestor_notations = []
    pending_notation_parts = []

    for code, value in _subfields(datafield):
        if code == "a":
            own_start = value
        elif code == "c":
            own_end = value
        elif code == "j" and own_label is None:
            own_label = value
        elif code in ("e", "f"):
            pending_notation_parts.append(value)
        elif code == "h":
            ancestor_labels.append(value)
            ancestor_notations.append(
                pending_notation_parts[0] if pending_notation_parts else None
            )
            pending_notation_parts = []

    own_notation = f"{own_start} - {own_end}" if own_start and own_end else own_start
    return own_notation, own_label, ancestor_notations, ancestor_labels


def parse_record(record) -> dict | None:
    datafields = record.findall(f"{MARC_NS}datafield")
    field_153 = next((df for df in datafields if df.get("tag") == "153"), None)
    if field_153 is None:
        return None

    notation, label, ancestor_notations, ancestor_labels = parse_153(field_153)
    if not notation:
        return None

    see_also = None
    note = None
    register_entries = []

    for df in datafields:
        tag = df.get("tag")
        if tag == "253" and see_also is None:
            see_also = next((v for c, v in _subfields(df) if c == "i"), None)
        elif tag == "684" and note is None:
            note = next((v for c, v in _subfields(df) if c == "i"), None)
        elif tag in REGISTER_TAGS:
            subs = dict(_subfields(df))
            term = subs.get("a")
            if term:
                register_entries.append(
                    {
                        "term": term,
                        "entry_type": REGISTER_ENTRY_TYPES[tag],
                        "gnd_id": (subs.get("0") or "").replace("(DE-588)", "") or None,
                    }
                )

    return {
        "notation": notation,
        "label": label,
        "parent_notation": ancestor_notations[-1] if ancestor_notations else None,
        "level": len(ancestor_notations),
        "hierarchy_path": " > ".join(ancestor_labels) if ancestor_labels else None,
        "see_also": see_also,
        "note": note,
        "register_entries": register_entries,
    }


def rebuild_tables(conn: sqlite3.Connection, dump_path: Path) -> tuple[int, int]:
    conn.executescript(
        """
        DROP TABLE IF EXISTS notation_new;
        DROP TABLE IF EXISTS register_entry_new;
        DROP TABLE IF EXISTS search_index_new;

        CREATE TABLE notation_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notation TEXT UNIQUE NOT NULL,
            label TEXT,
            hierarchy_path TEXT,
            parent_notation TEXT,
            level INTEGER,
            see_also TEXT,
            note TEXT
        );

        CREATE TABLE register_entry_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notation_id INTEGER NOT NULL,
            term TEXT NOT NULL,
            entry_type TEXT,
            gnd_id TEXT
        );

        CREATE VIRTUAL TABLE search_index_new USING fts5(notation_id UNINDEXED, text);
        """
    )

    notation_count = 0
    register_count = 0
    duplicate_count = 0
    context = etree.iterparse(str(dump_path), tag=f"{MARC_NS}record")

    for _, record in context:
        parsed = parse_record(record)
        if parsed:
            # Eine sehr kleine Zahl an Notationen (~0.05% im Gesamtabzug) ist echt doppelt
            # (identische Notation+Spannenende) - wird ignoriert statt den Sync abzubrechen.
            cur = conn.execute(
                "INSERT OR IGNORE INTO notation_new (notation, label, hierarchy_path, parent_notation, level, see_also, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    parsed["notation"],
                    parsed["label"],
                    parsed["hierarchy_path"],
                    parsed["parent_notation"],
                    parsed["level"],
                    parsed["see_also"],
                    parsed["note"],
                ),
            )
            if cur.rowcount == 0:
                duplicate_count += 1
            else:
                notation_id = cur.lastrowid
                notation_count += 1

                terms = [e["term"] for e in parsed["register_entries"]]
                for entry in parsed["register_entries"]:
                    conn.execute(
                        "INSERT INTO register_entry_new (notation_id, term, entry_type, gnd_id) VALUES (?, ?, ?, ?)",
                        (
                            notation_id,
                            entry["term"],
                            entry["entry_type"],
                            entry["gnd_id"],
                        ),
                    )
                    register_count += 1

                search_text = " ".join(
                    filter(None, [parsed["label"], parsed["hierarchy_path"], *terms])
                )
                conn.execute(
                    "INSERT INTO search_index_new (notation_id, text) VALUES (?, ?)",
                    (notation_id, search_text),
                )

        # Speicher freigeben (wichtig bei ~1.3 GB Datei)
        record.clear()
        while record.getprevious() is not None:
            del record.getparent()[0]

        if notation_count % 5000 == 0 and notation_count:
            conn.commit()
            print(f"  ... {notation_count} Notationen verarbeitet")

    conn.commit()
    if duplicate_count:
        print(
            f"  ({duplicate_count} doppelte Notation+Spannenende-Kombinationen übersprungen)"
        )
    return notation_count, register_count


def swap_in_new_tables(conn: sqlite3.Connection):
    conn.executescript(
        """
        DROP TABLE IF EXISTS register_entry;
        DROP TABLE IF EXISTS notation;
        DROP TABLE IF EXISTS search_index;

        ALTER TABLE notation_new RENAME TO notation;
        ALTER TABLE register_entry_new RENAME TO register_entry;
        ALTER TABLE search_index_new RENAME TO search_index;

        CREATE INDEX IF NOT EXISTS idx_notation_notation ON notation(notation);
        CREATE INDEX IF NOT EXISTS idx_register_notation_id ON register_entry(notation_id);
        CREATE INDEX IF NOT EXISTS idx_register_term ON register_entry(term);
        CREATE INDEX IF NOT EXISTS idx_register_gnd_id ON register_entry(gnd_id);
        """
    )
    conn.commit()


def main():
    dump_path = find_dump_file()
    print(f"Verwende Gesamtabzug: {dump_path}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        SCHEMA_SQL
    )  # stellt sicher, dass sync_log/expansion_cache existieren

    try:
        notation_count, register_count = rebuild_tables(conn, dump_path)
        conn.execute("BEGIN")
        swap_in_new_tables(conn)
        conn.execute(
            "INSERT INTO sync_log (run_at, source_file, notation_count, register_count, status) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                dump_path.name,
                notation_count,
                register_count,
                "success",
            ),
        )
        conn.commit()
        print(
            f"Fertig: {notation_count} Notationen, {register_count} Registereinträge."
        )
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "INSERT INTO sync_log (run_at, source_file, notation_count, register_count, status) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(UTC).isoformat(), dump_path.name, 0, 0, f"error: {exc}"),
        )
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
