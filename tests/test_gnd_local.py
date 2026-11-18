"""Lokaler GND-Abzug: Einlesen (app/services/gnd_local.py) und LocalGndSource."""

import io
import json
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from app.services import gnd_local
from app.services.expansion import gnd

FIXTURE = Path(__file__).parent / "fixtures" / "gnd_sachbegriff_nachgebaut.jsonld"

NT = """\
<https://d-nb.info/standards/vocab/gnd/gnd-sc#31.6> <http://www.w3.org/2004/02/skos/core#notation> "31.6" .
<https://d-nb.info/standards/vocab/gnd/gnd-sc#31.6> <http://www.w3.org/2004/02/skos/core#prefLabel> "Mechanical engineering"@en .
<https://d-nb.info/standards/vocab/gnd/gnd-sc#31.6> <http://www.w3.org/2004/02/skos/core#prefLabel> "Maschinenbau"@de .
<https://d-nb.info/standards/vocab/gnd/gnd-sc#99.9> <http://www.w3.org/2004/02/skos/core#prefLabel> "Familie \\u0026 Erziehung"@de .
"""


def test_normalize_key_and_values():
    assert (
        gnd_local.normalize_key(
            "https://d-nb.info/standards/elementset/gnd#preferredNameForTheSubjectHeading"
        )
        == "preferredNameForTheSubjectHeading"
    )
    assert gnd_local.normalize_key("gnd:relatedTerm") == "relatedTerm"
    assert gnd_local.normalize_key("plain") == "plain"
    assert gnd_local.values([{"@value": "a"}, {"@id": "b"}, "c", {"x": 1}]) == [
        "a",
        "b",
        "c",
    ]
    assert gnd_local.values({"@value": " d "}) == ["d"]
    assert gnd_local.values(None) == []


def test_text_is_nfc():
    nfd = unicodedata.normalize("NFD", "Mütter")
    assert nfd != "Mütter"
    node = {
        "@id": "https://d-nb.info/gnd/1-1",
        "https://d-nb.info/standards/elementset/gnd#preferredNameForTheSubjectHeading": [
            {"@value": nfd}
        ],
    }
    record = gnd_local.record_from_jsonld(node)
    assert record.preferred_name == "Mütter"
    assert gnd_local.name_norm(nfd) == gnd_local.name_norm("Mütter")


def test_records_from_nested_array_fixture():
    with FIXTURE.open(encoding="utf-8") as fh:
        records = list(gnd_local.iter_records(fh))
    assert [r.gnd_id for r in records] == [
        "9000001-1",
        "9000002-1",
        "9000003-1",
        "9000004-1",
        "9000005-1",
    ]
    mutter = records[0]
    assert mutter.preferred_name == "Mutter <Technik>"
    assert mutter.variant_names == ("Schraubenmutter", "Mutter <Maschinenelement>")
    assert mutter.broader_ids == ("9000003-1",)
    assert mutter.related_ids == ("9000004-1",)
    assert mutter.category_codes == ("31.6",)


def test_streaming_with_small_chunks_and_ndjson():
    text = FIXTURE.read_text(encoding="utf-8")
    small = list(gnd_local.iter_json_objects(io.StringIO(text), chunk_size=17))
    full = list(gnd_local.iter_json_objects(io.StringIO(text)))
    assert small == full
    assert len(full) == 7  # 5 Hauptknoten, 1 Hilfsknoten, 1 /about-Knoten
    ndjson = "\n".join(json.dumps(o) for o in full) + "\n"
    assert list(gnd_local.iter_json_objects(io.StringIO(ndjson), chunk_size=10)) == full


def test_broken_input_raises():
    with pytest.raises(ValueError):
        list(gnd_local.iter_json_objects(io.StringIO('[ {"a": 1}, x ]')))


def test_gnd_sc_ntriples():
    labels = gnd_local.parse_gnd_sc_ntriples(NT.splitlines())
    assert labels == {"31.6": "Maschinenbau", "99.9": "Familie & Erziehung"}


def _build_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    gnd_local.create_schema(conn)
    with FIXTURE.open(encoding="utf-8") as fh:
        stats = gnd_local.insert_records(conn, gnd_local.iter_records(fh))
    gnd_local.insert_category_labels(
        conn, gnd_local.parse_gnd_sc_ntriples(NT.splitlines())
    )
    gnd_local.write_meta(conn, "20261013", FIXTURE.name, stats.record_count)
    conn.close()
    return path


@pytest.fixture
def gnd_db(tmp_path):
    return _build_db(tmp_path / "gnd.sqlite3")


def test_database_tables(gnd_db):
    conn = sqlite3.connect(gnd_db)
    assert conn.execute("SELECT COUNT(*) FROM gnd_subject").fetchone()[0] == 5
    names = conn.execute(
        "SELECT name_norm, is_preferred FROM gnd_name WHERE gnd_id = '9000001-1' "
        "ORDER BY is_preferred DESC, name_norm"
    ).fetchall()
    # Vorzugsname und Variante <Maschinenelement> haben ohne Zusatz denselben Schlüssel
    assert names == [("mutter", 1), ("schraubenmutter", 0)]
    assert gnd_local.read_meta(conn)["record_count"] == 5
    conn.close()


class FakeFallback:
    def __init__(self):
        self.calls = []
        self.cache_hits = 7
        self.lookups = 9

    def search_subject_headings(self, query, size):
        self.calls.append(("search", query))
        return [gnd.GndRecord("lobid", "0-0", "Aus lobid")]

    def find_narrower(self, record_id, limit):
        self.calls.append(("narrower", record_id))
        return []


def test_local_search_order_and_fields(gnd_db):
    source = gnd.LocalGndSource(str(gnd_db), fallback=FakeFallback())
    records = source.search_subject_headings("Mutter", 10)
    # Vorzugsname ohne Zusatz zuerst, dann mit Zusatz
    assert [r.gnd_id for r in records] == ["9000002-1", "9000001-1"]
    technik = records[1]
    assert technik.id == "https://d-nb.info/gnd/9000001-1"
    assert technik.broader == (("9000003-1", "Schraubenverbindung"),)
    assert technik.related == (("9000004-1", "Gewinde"),)
    assert technik.subject_categories == (("31.6", "Maschinenbau"),)
    assert technik.variant_names == ("Schraubenmutter", "Mutter <Maschinenelement>")
    # Variante findet ebenfalls; Oberbegriff außerhalb des Abzugs fällt ohne Bezeichnung weg
    assert [r.gnd_id for r in source.search_subject_headings("schraubenmutter", 5)] == [
        "9000001-1"
    ]
    (verbindung,) = source.search_subject_headings("Schraubenverbindung", 5)
    assert verbindung.broader == ()
    assert source.search_subject_headings("Mutter", 1)[0].gnd_id == "9000002-1"
    source.close()


def test_local_narrower(gnd_db):
    source = gnd.LocalGndSource(str(gnd_db))
    children = source.find_narrower("https://d-nb.info/gnd/9000001-1", 10)
    assert [c.preferred_name for c in children] == ["Hutmutter"]
    assert source.find_narrower("https://d-nb.info/gnd/9000001-1", 0) == []


def test_expand_gnd_with_local_source(gnd_db):
    source = gnd.LocalGndSource(str(gnd_db))
    senses = gnd.expand_gnd(source, "Mutter")
    assert [s.display_name for s in senses] == ["Mutter", "Mutter <Technik>"]
    terms = {(t.relation, t.term) for t in senses[1].terms}
    assert ("broader", "Schraubenverbindung") in terms
    assert ("narrower", "Hutmutter") in terms
    assert ("related", "Gewinde") in terms


def test_empty_result_does_not_fall_back(gnd_db):
    fallback = FakeFallback()
    source = gnd.LocalGndSource(str(gnd_db), fallback=fallback)
    assert source.search_subject_headings("Gibtesnicht", 5) == []
    assert fallback.calls == []
    assert source.cache_hits == 7
    # Der lokale Abzug hat keinen Cache: gezählt werden nur Abfragen der Rückfallebene
    assert source.lookups == 9


@pytest.mark.parametrize("kind", ["missing", "empty", "broken"])
def test_falls_back_to_lobid(tmp_path, kind):
    path = tmp_path / "gnd.sqlite3"
    if kind == "empty":
        conn = sqlite3.connect(path)
        gnd_local.create_schema(conn)
        conn.close()
    elif kind == "broken":
        path.write_bytes(b"kein sqlite")
    fallback = FakeFallback()
    source = gnd.LocalGndSource(str(path), fallback=fallback)
    assert [r.gnd_id for r in source.search_subject_headings("Mutter", 5)] == ["0-0"]
    source.find_narrower("https://d-nb.info/gnd/1-1", 3)
    assert [c[0] for c in fallback.calls] == ["search", "narrower"]
    assert source.used_fallback == 2


def test_sqlite_error_during_query_falls_back(gnd_db):
    fallback = FakeFallback()
    source = gnd.LocalGndSource(str(gnd_db), fallback=fallback)
    assert source._connection() is not None
    source._conn.close()  # Verbindung kaputt -> sqlite3.ProgrammingError (ein sqlite3.Error)
    assert [r.gnd_id for r in source.search_subject_headings("Mutter", 5)] == ["0-0"]
