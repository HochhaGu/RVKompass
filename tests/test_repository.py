"""Smoke-Tests gegen die lokal synchronisierte RVK-Datenbank.

Werden übersprungen, wenn noch kein Sync gelaufen ist (siehe README: fetch_marcxml_dump.py
+ sync_rvk.py), damit ein frischer Checkout nicht mit rotem Testlauf startet.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import get_config
from app.models import SCHEMA_SQL
from app.services import rvk_repository

DB_PATH = get_config().DATABASE_PATH


@pytest.fixture
def conn():
    if not Path(DB_PATH).exists():
        pytest.skip(
            "Keine lokale RVK-Datenbank vorhanden - erst scripts/sync_rvk.py ausführen."
        )
    connection = rvk_repository.get_connection(DB_PATH)
    yield connection
    connection.close()


def test_keyword_search_finds_known_term(conn):
    hits = rvk_repository.keyword_search(conn, "Bibliografien", limit=10)
    assert hits, "Erwartete mindestens einen Treffer für 'Bibliografien'"


def test_get_notations_by_ids_roundtrip(conn):
    hits = rvk_repository.keyword_search(conn, "Bibliografien", limit=5)
    ids = [nid for nid, _ in hits]
    notations = rvk_repository.get_notations_by_ids(conn, ids)
    assert set(notations.keys()) == set(ids)
    for notation in notations.values():
        assert notation.notation


def test_get_notation_ids_for_term(conn):
    row = conn.execute("SELECT term FROM register_entry LIMIT 1").fetchone()
    assert row is not None, "Keine Registereinträge vorhanden"
    results = rvk_repository.get_notation_ids_for_term(conn, row["term"])
    assert results


@pytest.fixture
def memory_conn():
    """Leere In-Memory-Datenbank mit dem App-Schema, damit schreibende Tests nie data/ ändern."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_SQL)
    yield connection
    connection.close()


def test_expansion_cache_roundtrip(memory_conn):
    rvk_repository.set_expansion_cache(memory_conn, "testbegriff", '["a", "b"]')
    assert (
        rvk_repository.get_expansion_cache(memory_conn, "testbegriff") == '["a", "b"]'
    )


def _insert_register(conn, rows):
    for nid, term, gnd_id in rows:
        conn.execute(
            "INSERT OR IGNORE INTO notation (id, notation) VALUES (?, ?)",
            (nid, f"ZZ {nid}"),
        )
        conn.execute(
            "INSERT INTO register_entry (notation_id, term, entry_type, gnd_id) "
            "VALUES (?, ?, 'Sachschlagwort', ?)",
            (nid, term, gnd_id),
        )


def test_get_notations_by_gnd_ids_empty_input(memory_conn):
    assert rvk_repository.get_notations_by_gnd_ids(memory_conn, []) == {}
    assert rvk_repository.get_notations_by_gnd_ids(memory_conn, [None, ""]) == {}


def test_get_notations_by_gnd_ids_several_ids(memory_conn):
    _insert_register(
        memory_conn,
        [
            (1, "Mutter", "0000001-1"),
            (2, "Schraube", "0000011-1"),
            (3, "Mutter", "0000001-1"),
            (4, "Ohne", None),
        ],
    )
    result = rvk_repository.get_notations_by_gnd_ids(
        memory_conn, ["0000001-1", "0000011-1", "0000001-1", "fehlt"]
    )
    assert result == {"0000001-1": [1, 3], "0000011-1": [2]}


def test_get_notations_by_gnd_ids_limit_per_id(memory_conn):
    _insert_register(
        memory_conn, [(nid, "Massenbegriff", "0000099-1") for nid in range(1, 16)]
    )
    _insert_register(memory_conn, [(20, "Anderer", "0000098-1")])
    result = rvk_repository.get_notations_by_gnd_ids(
        memory_conn, ["0000099-1", "0000098-1"], limit_per_id=4
    )
    assert result == {"0000099-1": [1, 2, 3, 4], "0000098-1": [20]}


def test_get_notations_by_gnd_ids_batches(memory_conn, monkeypatch):
    monkeypatch.setattr(rvk_repository, "GND_ID_BATCH_SIZE", 2)
    _insert_register(memory_conn, [(nid, f"T{nid}", f"g{nid}") for nid in range(1, 6)])
    result = rvk_repository.get_notations_by_gnd_ids(
        memory_conn, [f"g{n}" for n in range(1, 6)]
    )
    assert result == {f"g{n}": [n] for n in range(1, 6)}


def test_gnd_cache_table_is_created_on_demand():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        assert rvk_repository.get_gnd_cache(connection, "k", 30) is None
        rvk_repository.set_gnd_cache(connection, "k", '{"member": []}')
        assert rvk_repository.get_gnd_cache(connection, "k", 30) == '{"member": []}'
    finally:
        connection.close()


def test_gnd_cache_expires_after_max_age(memory_conn):
    rvk_repository.set_gnd_cache(memory_conn, "k", "{}")
    almost = (datetime.now(UTC) - timedelta(days=29)).isoformat()
    memory_conn.execute("UPDATE gnd_cache SET created_at = ?", (almost,))
    assert rvk_repository.get_gnd_cache(memory_conn, "k", 30) == "{}"
    too_old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    memory_conn.execute("UPDATE gnd_cache SET created_at = ?", (too_old,))
    assert rvk_repository.get_gnd_cache(memory_conn, "k", 30) is None


# --- P6: RVK-Stand, Hauptgruppen, Änderungsstand ---------------------------------------------


def _sync(conn, source_file, status="success", run_at="2026-11-10T13:37:59"):
    conn.execute(
        "INSERT INTO sync_log (run_at, source_file, status) VALUES (?, ?, ?)",
        (run_at, source_file, status),
    )


def test_latest_rvk_version_from_last_successful_sync(memory_conn):
    _sync(memory_conn, "rvko_marcxml_2026_1.xml")
    _sync(memory_conn, "rvko_marcxml_2026_2.xml")
    _sync(memory_conn, "rvko_marcxml_2026_3.xml", status="error")
    assert rvk_repository.latest_rvk_version(memory_conn) == "2026/2"


def test_latest_rvk_version_unknown_file_name_shows_date(memory_conn):
    _sync(memory_conn, "eigener_abzug.xml", run_at="2026-11-01T08:00:00")
    assert rvk_repository.latest_rvk_version(memory_conn) == "2026-11-01"


def test_latest_rvk_version_without_success_or_table():
    connection = sqlite3.connect(":memory:")
    try:
        assert rvk_repository.latest_rvk_version(connection) is None
        connection.executescript(SCHEMA_SQL)
        _sync(connection, "rvko_marcxml_2026_1.xml", status="error")
        assert rvk_repository.latest_rvk_version(connection) is None
    finally:
        connection.close()


def _notations(conn, rows):
    for notation, label in rows:
        conn.execute(
            "INSERT INTO notation (notation, label) VALUES (?, ?)", (notation, label)
        )


def test_get_main_group_labels(memory_conn):
    _notations(memory_conn, [("ZN", "Elektrotechnik"), ("ZN 1000", "X"), ("ZL", None)])
    assert rvk_repository.get_main_group_labels(memory_conn, ["ZN", "ZL", "QP"]) == {
        "ZN": "Elektrotechnik"
    }
    assert rvk_repository.get_main_group_labels(memory_conn, []) == {}


def test_list_main_groups_with_labels_and_cache(tmp_path):
    db_path = tmp_path / "rvk.sqlite3"
    connection = rvk_repository.get_connection(str(db_path))
    connection.executescript(SCHEMA_SQL)
    _notations(
        connection, [("ZN", "Elektrotechnik"), ("AA", "Allgemeines"), ("ZN 1", "x")]
    )
    connection.commit()
    try:
        assert rvk_repository.list_main_groups(connection) == [
            ("AA", "Allgemeines"),
            ("ZN", "Elektrotechnik"),
        ]
        # zwischengespeichert: neue Zeile ohne neuen Sync-Lauf ändert nichts
        _notations(connection, [("ZL", "Maschinenbau")])
        assert len(rvk_repository.list_main_groups(connection)) == 2
        # neuer Sync-Lauf -> neu gelesen
        _sync(connection, "rvko_marcxml_2026_2.xml")
        assert len(rvk_repository.list_main_groups(connection)) == 3
    finally:
        connection.close()


def test_list_main_groups_fallback_without_letter_rows(memory_conn):
    _notations(memory_conn, [("ZN 1000", "a"), ("ZN 2000", "b"), ("QP 100", "c")])
    assert rvk_repository.list_main_groups(memory_conn) == [("QP", ""), ("ZN", "")]


def test_list_main_groups_without_table():
    connection = sqlite3.connect(":memory:")
    try:
        assert rvk_repository.list_main_groups(connection) == []
    finally:
        connection.close()


def test_get_change_versions(memory_conn):
    _insert_register(memory_conn, [(1, "A", None), (2, "B", None)])
    memory_conn.executemany(
        "INSERT INTO notation_change (notation_id, notation, version, change_type) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, "A", "2025/4", "new"),
            (1, "A", "2026/1", "changed"),
            (None, "WEG 1", "2026/1", "deleted"),
        ],
    )
    assert rvk_repository.get_change_versions(memory_conn, [1, 2]) == {1: "2026/1"}
    assert rvk_repository.get_change_versions(memory_conn, []) == {}


def test_get_change_versions_ignores_deleted(memory_conn):
    _insert_register(memory_conn, [(1, "A", None)])
    memory_conn.executemany(
        "INSERT INTO notation_change (notation_id, notation, version, change_type) "
        "VALUES (?, ?, ?, ?)",
        [(1, "A", "2026/1", "new"), (1, "A", "2026/2", "deleted")],
    )
    assert rvk_repository.get_change_versions(memory_conn, [1]) == {1: "2026/1"}


def test_get_change_versions_without_table_is_empty():
    connection = sqlite3.connect(":memory:")
    try:
        assert rvk_repository.get_change_versions(connection, [1, 2]) == {}
    finally:
        connection.close()


def test_readonly_connection_does_not_create_file(tmp_path):
    missing = tmp_path / "fehlt.sqlite3"
    assert rvk_repository.get_readonly_connection(str(missing)) is None
    assert not missing.exists()


def test_main_groups_in_real_database_read_only():
    """Prüft nur lesend (mode=ro), ob die echte Datenbank Hauptgruppen mit Bezeichnung hat.

    Stand 12.11.2026: 345 Hauptgruppen, alle mit Bezeichnung (z. B. „ZP Energietechnik“).
    """
    connection = rvk_repository.get_readonly_connection(DB_PATH)
    if connection is None:
        pytest.skip("Keine lokale RVK-Datenbank vorhanden.")
    try:
        groups = rvk_repository.list_main_groups(connection)
        assert groups, "Keine Hauptgruppen gefunden"
        assert any(label for _, label in groups)
        assert rvk_repository.latest_rvk_version(connection)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE schreibtest (x)")
    finally:
        connection.close()
