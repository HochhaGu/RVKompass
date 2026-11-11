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
