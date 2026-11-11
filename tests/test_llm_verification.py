"""Prüfung der LLM-Vorschläge gegen RVK-Register und GND (P4) – ohne Netz."""

import json
from types import SimpleNamespace

import pytest

from app.models import SCHEMA_SQL, LlmSuggestion
from app.services import rvk_repository
from app.services.expansion import llm
from app.services.expansion.gnd import GndRecord


@pytest.fixture
def conn():
    connection = rvk_repository.get_connection(":memory:")
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT INTO notation (id, notation, label) VALUES (1, 'ZL 4140', 'Zahnräder')"
    )
    for term, gnd_id in (
        ("Zahnrad", "4067467-5"),
        ("Zahnrad", None),
        ("Getriebeöl", None),
    ):
        connection.execute(
            "INSERT INTO register_entry (notation_id, term, entry_type, gnd_id) "
            "VALUES (1, ?, 'Sachschlagwort', ?)",
            (term, gnd_id),
        )
    connection.commit()
    yield connection
    connection.close()


class FakeSource:
    def __init__(self, records=(), fail=False):
        self.records = list(records)
        self.fail = fail
        self.queries = []

    def search_subject_headings(self, query, size):
        self.queries.append((query, size))
        if self.fail:
            raise RuntimeError("lobid nicht erreichbar")
        return self.records

    def find_narrower(self, record_id, limit):
        return []


WELLE = GndRecord(
    id="https://d-nb.info/gnd/4189530-9",
    gnd_id="4189530-9",
    preferred_name="Welle <Technik>",
    variant_names=("Antriebswelle",),
)


def _raw(term, relation="verwandt", rationale="weil"):
    return {"begriff": term, "beziehung": relation, "begruendung": rationale}


# --- Register -----------------------------------------------------------------------------


def test_find_register_terms_exact_only(conn):
    rows = rvk_repository.find_register_terms(conn, ["Zahnrad", "zahnrad", ""])
    assert sorted(rows, key=str) == sorted(
        [("Zahnrad", "4067467-5"), ("Zahnrad", None)], key=str
    )
    assert rvk_repository.find_register_terms(conn, []) == []
    assert rvk_repository.find_register_terms(conn, ["Zahn"]) == []


def test_register_variants():
    assert llm.register_variants("  zahnrad <Technik> ")[:3] == [
        "zahnrad <Technik>",
        "Zahnrad <Technik>",
        "zahnrad <technik>",
    ]
    assert "Zahnrad" in llm.register_variants("zahnrad  <Technik>")
    assert "Zahnrad" in llm.register_variants("ZAHNRAD")


@pytest.mark.parametrize("term", ["Zahnrad", "zahnrad", "ZAHNRAD", "Zahnrad <Technik>"])
def test_register_match_exact_case_and_qualifier(conn, term):
    assert llm.register_match(conn, term) == ("Zahnrad", "4067467-5")


def test_register_match_without_gnd_id(conn):
    assert llm.register_match(conn, "getriebeöl") == ("Getriebeöl", None)
    assert llm.register_match(conn, "Kegelrad") is None


# --- GND ----------------------------------------------------------------------------------


def test_gnd_match_via_variant_name():
    source = FakeSource([WELLE])
    assert llm.gnd_match(source, "Antriebswelle", 5) == ("Welle <Technik>", "4189530-9")
    assert source.queries == [("Antriebswelle", 5)]


def test_gnd_match_requires_exact_name():
    assert llm.gnd_match(FakeSource([WELLE]), "Wellenlager") is None


def test_gnd_failure_means_not_verified_without_exception(conn):
    (s,) = llm.verify_suggestions(conn, [_raw("Antriebswelle")], FakeSource(fail=True))
    assert s.verified is False and s.verified_via is None


# --- verify_suggestions -------------------------------------------------------------------


def test_verify_register_first_then_gnd_then_unverified(conn):
    source = FakeSource([WELLE])
    result = llm.verify_suggestions(
        conn,
        [
            _raw("zahnrad", "unterbegriff", "Bauteil"),
            _raw("Antriebswelle", "verwandt"),
            _raw("Kraftübertragungsding", "fachkontext"),
        ],
        source,
    )
    zahnrad, welle, unknown = result
    assert zahnrad == LlmSuggestion(
        "zahnrad",
        "unterbegriff",
        "Bauteil",
        True,
        "rvk_register",
        "Zahnrad",
        "4067467-5",
    )
    assert (welle.verified_via, welle.matched_term, welle.gnd_id) == (
        "gnd",
        "Welle <Technik>",
        "4189530-9",
    )
    assert unknown.verified is False and unknown.matched_term is None
    # Treffer im Register fragen lobid nicht
    assert [q for q, _ in source.queries] == ["Antriebswelle", "Kraftübertragungsding"]


def test_verify_without_source_checks_register_only(conn):
    zahnrad, welle = llm.verify_suggestions(
        conn, [_raw("Zahnrad"), _raw("Antriebswelle")], None
    )
    assert zahnrad.verified and not welle.verified


def test_verified_terms_only_verified_as_llm_verified(conn):
    suggestions = llm.verify_suggestions(
        conn, [_raw("Zahnrad"), _raw("Unbekannt")], None
    )
    (term,) = llm.verified_terms(suggestions, "Getriebe")
    assert (term.term, term.source, term.relation, term.gnd_id, term.parent_term) == (
        "Zahnrad",
        "llm",
        "llm_verified",
        "4067467-5",
        "Getriebe",
    )


def test_suggestions_to_rows():
    rows = llm.suggestions_to_rows(
        [
            LlmSuggestion("Zahnrad", "verwandt", "Bauteil", True, "gnd", "Zahnrad"),
            LlmSuggestion("Dings", "synonym", "unklar"),
        ]
    )
    assert rows[0]["verified_label"] == "gefunden in GND"
    assert rows[1]["verified"] is False and rows[1]["verified_label"] == ""
    assert rows[1]["rationale"] == "unklar"


# --- Einstiegspunkt -----------------------------------------------------------------------


class FakeClient:
    def __init__(self, answer):
        self.calls = 0
        self.answer = answer
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.answer))]
        )


CONFIG = {
    "ENABLE_QUERY_EXPANSION": True,
    "CLAUDE_API_KEY": "sk-test",
    "CLAUDE_MODEL": "claude-haiku-5-5",
    "CLAUDE_DAILY_LIMIT": 10,
    "ENABLE_GND_EXPANSION": False,
}


def _factory(client):
    return lambda api_key, timeout, max_retries: client


@pytest.mark.parametrize(
    "override", [{"ENABLE_QUERY_EXPANSION": False}, {"CLAUDE_API_KEY": ""}]
)
def test_disabled_or_no_key_gives_empty_list(conn, override):
    client = FakeClient({"begriffe": [_raw("Zahnrad")]})
    config = {**CONFIG, **override}
    assert (
        llm.llm_suggestions_for_query(
            conn, config, "Getriebe", client_factory=_factory(client)
        )
        == []
    )
    assert client.calls == 0


def test_entry_point_verifies_and_reuses_cache(conn, monkeypatch):
    monkeypatch.setattr(llm.query_expansion, "sdk_supports_output_config", lambda: True)
    client = FakeClient({"begriffe": [_raw("Zahnrad"), _raw("Dings")]})
    stats = {}
    first = llm.llm_suggestions_for_query(
        conn, CONFIG, "Getriebe", client_factory=_factory(client), stats=stats
    )
    assert [(s.term, s.verified) for s in first] == [
        ("Zahnrad", True),
        ("Dings", False),
    ]

    # Prüfergebnis steht nicht im Cache: nach Registeränderung wird neu geprüft
    conn.execute(
        "INSERT INTO register_entry (notation_id, term, entry_type) VALUES (1, 'Dings', 'x')"
    )
    second = llm.llm_suggestions_for_query(
        conn, CONFIG, "Getriebe", client_factory=_factory(client), stats=stats
    )
    assert [(s.term, s.verified) for s in second] == [
        ("Zahnrad", True),
        ("Dings", True),
    ]
    assert client.calls == 1
    assert stats == {"api_calls": 1, "cache_hits": 1}


def test_entry_point_uses_given_source(conn):
    client = FakeClient({"begriffe": [_raw("Antriebswelle")]})
    source = FakeSource([WELLE])
    (s,) = llm.llm_suggestions_for_query(
        conn, CONFIG, "Getriebe", source=source, client_factory=_factory(client)
    )
    assert s.verified_via == "gnd"
