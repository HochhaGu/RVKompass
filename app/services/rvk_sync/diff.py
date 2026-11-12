"""Vergleich alter und neuer RVK-Gesamtabzug und Pflege der Tabelle `notation_change` (P8).

Beide Stände liegen in derselben (Staging-)Datenbank: der bisherige in `notation` /
`register_entry`, der neue in `notation_new` / `register_entry_new` (scripts/sync_rvk.py).
Schlüssel ist der Notationstext, nicht die ID: Die IDs entstehen bei jedem Neuaufbau neu.

- neu:      nur im neuen Stand
- gelöscht: nur im alten Stand
- geändert: Benennung, Hierarchiepfad, übergeordnete Notation, Ebene, Verweis, Bemerkung oder die
            Menge der Registereinträge (Begriff, Art, GND-ID) unterscheiden sich
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

CHANGE_TYPES = ("new", "changed", "deleted")

NOTATION_CHANGE_SQL = """
CREATE TABLE IF NOT EXISTS notation_change (
    notation_id INTEGER,
    notation TEXT NOT NULL,
    version TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('new', 'changed', 'deleted'))
);
CREATE INDEX IF NOT EXISTS idx_notation_change_notation_id ON notation_change(notation_id);
CREATE INDEX IF NOT EXISTS idx_notation_change_notation ON notation_change(notation);
"""


@dataclass
class DiffResult:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    # Erster Lauf ohne bisherigen Stand: kein Vergleich möglich, keine Einträge
    baseline: bool = False

    @property
    def counts(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "changed": len(self.changed),
            "deleted": len(self.deleted),
        }


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _signature_table(conn, target: str, notation_table: str, register_table: str):
    sep = "char(31)"
    conn.executescript(
        f"""
        DROP TABLE IF EXISTS temp.{target}_reg;
        CREATE TEMP TABLE {target}_reg AS
            SELECT notation_id, group_concat(entry, char(30)) AS reg FROM (
                SELECT notation_id,
                       term || {sep} || ifnull(entry_type, '') || {sep} || ifnull(gnd_id, '')
                           AS entry
                FROM {register_table}
                ORDER BY notation_id, entry
            ) GROUP BY notation_id;
        CREATE INDEX temp.idx_{target}_reg ON {target}_reg(notation_id);
        DROP TABLE IF EXISTS temp.{target};
        CREATE TEMP TABLE {target} AS
            SELECT n.notation AS notation,
                   ifnull(n.label, '') || {sep} || ifnull(n.hierarchy_path, '') || {sep} ||
                   ifnull(n.parent_notation, '') || {sep} ||
                   ifnull(CAST(n.level AS TEXT), '') || {sep} ||
                   ifnull(n.see_also, '') || {sep} || ifnull(n.note, '') || {sep} ||
                   ifnull(r.reg, '') AS sig
            FROM {notation_table} n LEFT JOIN {target}_reg r ON r.notation_id = n.id;
        CREATE INDEX temp.idx_{target} ON {target}(notation);
        """
    )


def compute_diff(conn: sqlite3.Connection) -> DiffResult:
    """Vergleicht `notation`/`register_entry` (alt) mit `*_new` (neu)."""
    if not table_exists(conn, "notation_new"):
        raise RuntimeError("Tabelle notation_new fehlt – erst den Import ausführen.")
    if count_rows(conn, "notation") == 0:
        return DiffResult(baseline=True)
    _signature_table(conn, "sig_old", "notation", "register_entry")
    _signature_table(conn, "sig_new", "notation_new", "register_entry_new")
    try:
        new = [
            r[0]
            for r in conn.execute(
                "SELECT notation FROM sig_new WHERE notation NOT IN "
                "(SELECT notation FROM sig_old) ORDER BY notation"
            )
        ]
        deleted = [
            r[0]
            for r in conn.execute(
                "SELECT notation FROM sig_old WHERE notation NOT IN "
                "(SELECT notation FROM sig_new) ORDER BY notation"
            )
        ]
        changed = [
            r[0]
            for r in conn.execute(
                "SELECT n.notation FROM sig_new n JOIN sig_old o ON o.notation = n.notation "
                "WHERE n.sig <> o.sig ORDER BY n.notation"
            )
        ]
    finally:
        conn.executescript(
            "DROP TABLE IF EXISTS temp.sig_old; DROP TABLE IF EXISTS temp.sig_new; "
            "DROP TABLE IF EXISTS temp.sig_old_reg; DROP TABLE IF EXISTS temp.sig_new_reg;"
        )
    return DiffResult(new=new, changed=changed, deleted=deleted)


def migrate_notation_change(conn: sqlite3.Connection) -> bool:
    """Bringt `notation_change` auf das Schema ab P8 (Notationstext, ID darf NULL sein).

    Alte Zeilen werden übernommen; ihr Notationstext kommt aus `notation`. Liefert True, wenn
    umgebaut wurde.
    """
    if not table_exists(conn, "notation_change"):
        conn.executescript(NOTATION_CHANGE_SQL)
        return False
    columns = {r[1] for r in conn.execute("PRAGMA table_info(notation_change)")}
    if "notation" in columns:
        conn.executescript(NOTATION_CHANGE_SQL)
        return False
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_notation_change_notation_id;
        ALTER TABLE notation_change RENAME TO notation_change_alt;
        """
        + NOTATION_CHANGE_SQL
        + """
        INSERT INTO notation_change (notation_id, notation, version, change_type)
            SELECT c.notation_id, n.notation, c.version,
                   CASE WHEN c.change_type IN ('new', 'changed', 'deleted')
                        THEN c.change_type ELSE 'changed' END
            FROM notation_change_alt c JOIN notation n ON n.id = c.notation_id;
        DROP TABLE notation_change_alt;
        """
    )
    conn.commit()
    return True


def apply_diff(conn: sqlite3.Connection, diff: DiffResult, version: str) -> int:
    """Nach dem Tabellentausch: IDs neu zuordnen und die Änderungen dieses Stands anhängen.

    Bestehende Zeilen bekommen über den Notationstext die neue ID; gelöschte Notationen haben
    keine ID mehr (NULL). Liefert die Zahl der neuen Einträge.
    """
    migrate_notation_change(conn)
    conn.execute(
        "UPDATE notation_change SET notation_id = "
        "(SELECT id FROM notation WHERE notation.notation = notation_change.notation)"
    )
    rows = (
        [(n, "new") for n in diff.new]
        + [(n, "changed") for n in diff.changed]
        + [(n, "deleted") for n in diff.deleted]
    )
    conn.executemany(
        "INSERT INTO notation_change (notation_id, notation, version, change_type) "
        "VALUES ((SELECT id FROM notation WHERE notation = ?), ?, ?, ?)",
        [(n, n, version, t) for n, t in rows],
    )
    conn.commit()
    return len(rows)
