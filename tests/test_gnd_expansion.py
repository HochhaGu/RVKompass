"""GND-Erweiterung (P3) – ohne Netz: HTTP wird über ein eingeschleustes http_get ersetzt."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

from app.models import SCHEMA_SQL
from app.services import rvk_repository
from app.services.expansion import gnd

FIXTURE = Path(__file__).parent / "fixtures" / "lobid_gnd_mutter_nachgebaut.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    connection = rvk_repository.get_connection(":memory:")
    connection.executescript(SCHEMA_SQL)
    yield connection
    connection.close()


class FakeResponse:
    def __init__(self, data=None, status=200, bad_json=False):
        self.data = data
        self.status = status
        self.bad_json = bad_json

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"HTTP {self.status}")

    def json(self):
        if self.bad_json:
            raise json.JSONDecodeError("kaputt", "<html>", 0)
        return self.data


class FakeHttp:
    """Merkt sich alle Aufrufe; liefert der Reihe nach die vorgegebenen Antworten/Fehler."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params=None, timeout=None, headers=None):
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        item = self.responses.pop(0) if self.responses else FakeResponse({"member": []})
        if isinstance(item, Exception):
            raise item
        return item


# --- reine Hilfsfunktionen -------------------------------------------------------------------


def test_parse_record_complete():
    record = gnd.parse_record(load_fixture()["member"][0])
    assert record.id == "https://d-nb.info/gnd/0000001-1"
    assert record.gnd_id == "0000001-1"
    assert record.preferred_name == "Mutter <Technik>"
    assert record.variant_names == ("Schraubenmutter",)
    assert record.broader == (("0000010-1", "Schraubverbindung"),)
    assert record.related == (("0000011-1", "Schraube"),)
    assert record.subject_categories == (("31.6", "Maschinenbau"),)


def test_parse_record_missing_fields_and_single_values():
    record = gnd.parse_record(
        {
            "id": "https://d-nb.info/gnd/0000005-1",
            "preferredName": "Feder <Technik>",
            "variantName": "Technische Feder",
            "broaderTermGeneric": {
                "id": "https://d-nb.info/gnd/0000050-1",
                "label": "Maschinenelement",
            },
            "relatedTerm": [{"id": "https://d-nb.info/gnd/0000051-1"}],
        }
    )
    # gndIdentifier fehlt -> letzter Pfadteil der id
    assert record.gnd_id == "0000005-1"
    assert record.variant_names == ("Technische Feder",)
    assert record.broader == (("0000050-1", "Maschinenelement"),)
    # verwandter Begriff ohne Bezeichnung wird übergangen
    assert record.related == ()
    assert record.subject_categories == ()


def test_parse_record_rejects_incomplete():
    assert gnd.parse_record({"id": "https://d-nb.info/gnd/1"}) is None
    assert gnd.parse_record({"preferredName": "X"}) is None
    assert gnd.parse_record("kein Objekt") is None


def test_parse_record_reads_all_four_broader_fields():
    data = {"id": "https://d-nb.info/gnd/0000006-1", "preferredName": "Test"}
    for index, field_name in enumerate(gnd.BROADER_FIELDS):
        data[field_name] = [
            {"id": f"https://d-nb.info/gnd/00001{index}0-1", "label": f"O{index}"}
        ]
    record = gnd.parse_record(data)
    assert [label for _, label in record.broader] == ["O0", "O1", "O2", "O3"]


def test_parse_search_response():
    records = gnd.parse_search_response(load_fixture())
    assert [r.gnd_id for r in records] == ["0000001-1", "0000002-1", "0000003-1"]
    assert gnd.parse_search_response({}) == []
    assert gnd.parse_search_response(None) == []
    assert gnd.parse_search_response({"member": [{"kaputt": True}]}) == []


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Mutter <Technik>", ("Mutter", "Technik")),
        ("Mutter", ("Mutter", None)),
        ("  Bank <Möbel> ", ("Bank", "Möbel")),
        ("<Technik>", ("<Technik>", None)),
    ],
)
def test_split_qualifier(name, expected):
    assert gnd.split_qualifier(name) == expected


def test_subject_code_and_gnd_id_from_uri():
    assert (
        gnd.subject_code("https://d-nb.info/standards/vocab/gnd/gnd-sc#31.6") == "31.6"
    )
    assert gnd.subject_code("30") == "30"
    assert gnd.gnd_id_from_uri("https://d-nb.info/gnd/4040757-5") == "4040757-5"


def test_narrower_query_uses_all_broader_fields_and_unchanged_id():
    query = gnd.narrower_query("https://d-nb.info/gnd/0000001-1")
    assert query == (
        'broaderTermGeneral.id:"https://d-nb.info/gnd/0000001-1" OR '
        'broaderTermGeneric.id:"https://d-nb.info/gnd/0000001-1" OR '
        'broaderTermPartitive.id:"https://d-nb.info/gnd/0000001-1" OR '
        'broaderTermInstantial.id:"https://d-nb.info/gnd/0000001-1"'
    )


# --- LobidGndSource ---------------------------------------------------------------------------


def test_lobid_search_parameters(conn):
    http = FakeHttp(FakeResponse(load_fixture()))
    source = gnd.LobidGndSource(conn, timeout=4, http_get=http)
    records = source.search_subject_headings("Mutter", 10)

    assert len(records) == 3
    (call,) = http.calls
    assert call["url"] == "https://lobid.org/gnd/search"
    assert call["params"] == {
        "q": "Mutter",
        "filter": "type:SubjectHeading",
        "format": "json",
        "size": "10",
    }
    assert call["timeout"] == 4
    assert call["headers"]["User-Agent"] == "RVKompass"


def test_lobid_narrower_parameters(conn):
    http = FakeHttp(FakeResponse({"member": []}))
    source = gnd.LobidGndSource(conn, http_get=http)
    assert source.find_narrower("https://d-nb.info/gnd/0000001-1", 7) == []
    params = http.calls[0]["params"]
    assert params["q"] == gnd.narrower_query("https://d-nb.info/gnd/0000001-1")
    assert params["filter"] == "type:SubjectHeading"
    assert params["size"] == "7"


def test_lobid_cache_hit_avoids_http(conn):
    http = FakeHttp(FakeResponse(load_fixture()))
    gnd.LobidGndSource(conn, http_get=http).search_subject_headings("Mutter", 10)
    second = FakeHttp()
    records = gnd.LobidGndSource(conn, http_get=second).search_subject_headings(
        "Mutter", 10
    )
    assert len(records) == 3
    assert second.calls == []


def test_lobid_empty_valid_answer_is_cached(conn):
    gnd.LobidGndSource(
        conn, http_get=FakeHttp(FakeResponse({"member": []}))
    ).search_subject_headings("Xyz", 10)
    second = FakeHttp()
    assert (
        gnd.LobidGndSource(conn, http_get=second).search_subject_headings("Xyz", 10)
        == []
    )
    assert second.calls == []


def test_lobid_cache_older_than_30_days_is_refetched(conn):
    source = gnd.LobidGndSource(
        conn, cache_days=30, http_get=FakeHttp(FakeResponse(load_fixture()))
    )
    source.search_subject_headings("Mutter", 10)
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    conn.execute("UPDATE gnd_cache SET created_at = ?", (old,))
    http = FakeHttp(FakeResponse({"member": []}))
    assert (
        gnd.LobidGndSource(conn, cache_days=30, http_get=http).search_subject_headings(
            "Mutter", 10
        )
        == []
    )
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("Zeitüberschreitung"),
        requests.ConnectionError("kein Netz"),
        FakeResponse(status=500),
        FakeResponse(bad_json=True),
        FakeResponse(data=["keine", "Liste"]),
    ],
)
def test_lobid_errors_give_empty_list_and_no_cache(conn, failure):
    source = gnd.LobidGndSource(conn, http_get=FakeHttp(failure))
    assert source.search_subject_headings("Mutter", 10) == []
    assert conn.execute("SELECT COUNT(*) FROM gnd_cache").fetchone()[0] == 0


def test_lobid_stops_after_three_consecutive_failures(conn):
    http = FakeHttp(*[requests.Timeout("weg")] * 5)
    source = gnd.LobidGndSource(conn, http_get=http)
    for query in ("a", "b", "c", "d", "e"):
        assert source.search_subject_headings(query, 10) == []
    assert len(http.calls) == 3


def test_lobid_success_resets_failure_counter(conn):
    http = FakeHttp(
        requests.Timeout("1"),
        requests.Timeout("2"),
        FakeResponse({"member": []}),
        requests.Timeout("3"),
        FakeResponse({"member": []}),
    )
    source = gnd.LobidGndSource(conn, http_get=http)
    for query in ("a", "b", "c", "d", "e"):
        source.search_subject_headings(query, 10)
    assert len(http.calls) == 5


# --- expand_gnd über die Schnittstelle --------------------------------------------------------


class FakeSource:
    """Erfüllt nur die GndSource-Schnittstelle – belegt, dass expand_gnd nichts anderes braucht."""

    def __init__(self, records, narrower=None, fail=False):
        self.records = records
        self.narrower = narrower or {}
        self.fail = fail
        self.narrower_calls = []

    def search_subject_headings(self, query, size):
        if self.fail:
            raise RuntimeError("Quelle kaputt")
        return self.records[:size]

    def find_narrower(self, record_id, limit):
        self.narrower_calls.append((record_id, limit))
        return self.narrower.get(record_id, [])


def _records():
    return gnd.parse_search_response(load_fixture())


def _child(gnd_id, name):
    return gnd.GndRecord(
        id=f"https://d-nb.info/gnd/{gnd_id}", gnd_id=gnd_id, preferred_name=name
    )


def test_expand_gnd_homonym_gives_two_senses_with_relations():
    narrower = {
        "https://d-nb.info/gnd/0000001-1": [_child("0000030-1", "Sechskantmutter")]
    }
    senses = gnd.expand_gnd(FakeSource(_records(), narrower), "Mutter")

    # "Mutterschutz" ist kein exakter Treffer und wird keine Bedeutung
    assert [s.display_name for s in senses] == ["Mutter <Technik>", "Mutter"]
    technik = senses[0]
    assert technik.gnd_id == "0000001-1"
    assert technik.qualifier == "Technik"
    assert technik.subject_categories == (("31.6", "Maschinenbau"),)
    assert [(t.term, t.relation, t.gnd_id) for t in technik.terms] == [
        ("Mutter <Technik>", "synonym", "0000001-1"),
        ("Schraubenmutter", "synonym", "0000001-1"),
        ("Schraubverbindung", "broader", "0000010-1"),
        ("Sechskantmutter", "narrower", "0000030-1"),
        ("Schraube", "related", "0000011-1"),
    ]
    assert all(
        t.source == "gnd" and t.parent_term == "Mutter <Technik>" for t in technik.terms
    )


def test_expand_gnd_matches_variant_names_case_insensitive():
    senses = gnd.expand_gnd(FakeSource(_records()), " schraubenMUTTER ")
    assert [s.gnd_id for s in senses] == ["0000001-1"]


def test_expand_gnd_fuzzy_match_gives_nothing():
    assert (
        gnd.expand_gnd(FakeSource(_records()), "Leichte Materialien für Flugzeuge")
        == []
    )
    assert gnd.expand_gnd(FakeSource(_records()), "") == []


def test_expand_gnd_respects_limits():
    many = {
        "https://d-nb.info/gnd/0000001-1": [
            _child(f"00004{i:02d}-1", f"Unter {i}") for i in range(20)
        ]
    }
    source = FakeSource(_records(), many)
    senses = gnd.expand_gnd(source, "Mutter", max_senses=1, narrower_limit=3)
    assert len(senses) == 1
    assert sum(1 for t in senses[0].terms if t.relation == "narrower") == 3
    assert source.narrower_calls == [("https://d-nb.info/gnd/0000001-1", 3)]


def test_expand_gnd_source_failure_gives_empty_list():
    assert gnd.expand_gnd(FakeSource([], fail=True), "Mutter") == []


def test_expand_gnd_with_lobid_source_end_to_end(conn):
    http = FakeHttp(
        FakeResponse(load_fixture()),
        FakeResponse(
            {
                "member": [
                    {
                        "id": "https://d-nb.info/gnd/0000030-1",
                        "preferredName": "Sechskantmutter",
                    }
                ]
            }
        ),
        FakeResponse({"member": []}),
    )
    senses = gnd.expand_gnd(gnd.LobidGndSource(conn, http_get=http), "Mutter")
    assert len(senses) == 2
    assert ("Sechskantmutter", "narrower") in [
        (t.term, t.relation) for t in senses[0].terms
    ]


def test_gnd_senses_for_query_respects_switch(conn):
    http = FakeHttp(FakeResponse(load_fixture()))
    off = {"ENABLE_GND_EXPANSION": False}
    assert gnd.gnd_senses_for_query(conn, off, "Mutter", http_get=http) == []
    assert http.calls == []

    on = {"ENABLE_GND_EXPANSION": True, "GND_NARROWER_LIMIT": 0}
    senses = gnd.gnd_senses_for_query(conn, on, "Mutter", http_get=http)
    assert [s.gnd_id for s in senses] == ["0000001-1", "0000002-1"]


def test_gnd_senses_for_query_without_network_is_empty(conn):
    http = FakeHttp(requests.ConnectionError("kein Netz"))
    assert (
        gnd.gnd_senses_for_query(
            conn, {"ENABLE_GND_EXPANSION": True}, "Mutter", http_get=http
        )
        == []
    )
