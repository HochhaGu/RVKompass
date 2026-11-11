"""Smoke-Tests gegen die lokal synchronisierte RVK-Datenbank.

Werden übersprungen, wenn noch kein Sync gelaufen ist (siehe README: fetch_marcxml_dump.py
+ sync_rvk.py), damit ein frischer Checkout nicht mit rotem Testlauf startet.
"""

import sqlite3
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
