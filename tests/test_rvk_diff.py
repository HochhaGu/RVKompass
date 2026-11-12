"""Vergleich alt/neu und notation_change (app/services/rvk_sync/diff.py)."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from app.services import rvk_repository
from app.services.rvk_sync import diff
from tests.rvk_sync_helpers import make_db

ROOT = Path(__file__).resolve().parent.parent


def _swap_in(conn):
    spec = importlib.util.spec_from_file_location(
        "sync_rvk_for_test", ROOT / "scripts" / "sync_rvk.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_rvk_for_test"] = module
    spec.loader.exec_module(module)
    module.swap_in_new_tables(conn)


def test_new_changed_deleted(tmp_path):
    conn = make_db(tmp_path / "rvk.sqlite3")
    result = diff.compute_diff(conn)
    assert result.new == ["ZL 0001"]
    assert result.deleted == ["ZL 9999"]
    assert result.changed == ["ZL 4620", "ZM 1000"]
    assert result.counts == {"new": 1, "changed": 2, "deleted": 1}
    assert not result.baseline
    # Hilfstabellen sind wieder weg
    temp = conn.execute("SELECT name FROM temp.sqlite_master").fetchall()
    assert temp == []


def test_first_run_is_baseline(tmp_path):
    conn = make_db(tmp_path / "rvk.sqlite3", old_rows=[])
    result = diff.compute_diff(conn)
    assert result.baseline
    assert result.counts == {"new": 0, "changed": 0, "deleted": 0}


def test_missing_new_table_raises(tmp_path):
    conn = make_db(tmp_path / "rvk.sqlite3", new_rows=None)
    with pytest.raises(RuntimeError):
        diff.compute_diff(conn)


def test_migration_of_old_schema(tmp_path):
    conn = sqlite3.connect(tmp_path / "alt.sqlite3")
    conn.executescript(
        """
        CREATE TABLE notation (id INTEGER PRIMARY KEY, notation TEXT);
        INSERT INTO notation VALUES (5, 'ZL 4620');
        CREATE TABLE notation_change (
            notation_id INTEGER NOT NULL REFERENCES notation(id),
            version TEXT NOT NULL, change_type TEXT
        );
        CREATE INDEX idx_notation_change_notation_id ON notation_change(notation_id);
        INSERT INTO notation_change VALUES (5, '2026/1', 'geändert');
        """
    )
    assert diff.migrate_notation_change(conn) is True
    rows = conn.execute(
        "SELECT notation_id, notation, version, change_type FROM notation_change"
    ).fetchall()
    assert rows == [(5, "ZL 4620", "2026/1", "changed")]
    columns = {r[1]: r for r in conn.execute("PRAGMA table_info(notation_change)")}
    assert columns["notation_id"][3] == 0  # NULL erlaubt
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO notation_change VALUES (NULL, 'X', '2026/2', 'unsinn')"
        )
    # zweiter Aufruf ändert nichts
    assert diff.migrate_notation_change(conn) is False


def test_apply_diff_maps_ids_after_rebuild(tmp_path):
    conn = make_db(tmp_path / "rvk.sqlite3")
    conn.row_factory = sqlite3.Row
    # ein Eintrag aus einem früheren Stand für ZL 4630 (alte ID)
    old_id = conn.execute(
        "SELECT id FROM notation WHERE notation = 'ZL 4630'"
    ).fetchone()[0]
    diff.migrate_notation_change(conn)
    conn.execute(
        "INSERT INTO notation_change VALUES (?, 'ZL 4630', '2026/1', 'changed')",
        (old_id,),
    )
    conn.commit()
    result = diff.compute_diff(conn)
    _swap_in(conn)
    assert diff.apply_diff(conn, result, "2026/3") == 4
    ids = {
        r["notation"]: r["id"]
        for r in conn.execute("SELECT id, notation FROM notation")
    }
    assert ids["ZL 4630"] != old_id  # neuer Aufbau, neue ID
    rows = {
        (r["notation"], r["version"]): (r["notation_id"], r["change_type"])
        for r in conn.execute("SELECT * FROM notation_change")
    }
    assert rows[("ZL 4630", "2026/1")] == (ids["ZL 4630"], "changed")
    assert rows[("ZL 0001", "2026/3")] == (ids["ZL 0001"], "new")
    assert rows[("ZL 4620", "2026/3")] == (ids["ZL 4620"], "changed")
    assert rows[("ZL 9999", "2026/3")] == (None, "deleted")

    versions = rvk_repository.get_change_versions(conn, list(ids.values()))
    assert versions[ids["ZL 0001"]] == "2026/3"
    assert versions[ids["ZL 4620"]] == "2026/3"
    assert versions[ids["ZL 4630"]] == "2026/1"
    assert ids["ZL"] not in versions
