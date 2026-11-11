"""Evidence-Datenmodell und Multi-Query in ranking.search – ohne Netz, ohne Modell, ohne echte Daten."""

import numpy as np
import pytest

from app.models import SCHEMA_SQL, Evidence, ExpansionTerm, GndSense, Hit
from app.services import ranking, rvk_repository

MIN_SCORE = 0.1

# Dimensionen: 0 = "Getriebe", 1 = "Zahnrad", 2 = "Kupplung"
TEXT_VECTORS = {
    "Getriebe": [0.8, 0.0, 0.6],
    "Zahnrad": [0.0, 1.0, 0.0],
    "Kupplung": [0.0, 0.0, 1.0],
}

# (id, notation, label, Embedding, FTS-Text)
NOTATIONS = [
    (1, "ZL 4100", "Getriebe", [1.0, 0.0, 0.0], "Getriebe"),
    (2, "ZL 4140", "Zahnräder", [0.0, 1.0, 0.0], "Zahnrad"),
    (3, "ZL 4200", "Schmierung", [0.1, 0.0, 0.0], "Getriebeöl Schmierung"),
    (4, "ZL", "Maschinenelemente", [1.0, 0.0, 0.0], "Maschinenelemente"),
    (5, "ZL 4300", "Kupplungen", [0.0, 0.0, 0.5], "Kupplung"),
]

# Registerbegriff -> (Embedding, Notation-ID, GND-ID)
REGISTER = {"Kupplung": ([0.0, 0.0, 1.0], 5, "gnd-test-1")}


class FakeEmbeddingService:
    def __init__(self):
        self.calls = []
        self.notation_vecs = {nid: np.array(vec) for nid, _, _, vec, _ in NOTATIONS}
        self.term_vecs = {term: np.array(vec) for term, (vec, _, _) in REGISTER.items()}

    def embed_query(self, text):
        self.calls.append(text)
        return np.array(TEXT_VECTORS.get(text, [0.0, 0.0, 0.0]))

    @staticmethod
    def _top(items, vec, top_k):
        scored = [(key, float(v @ vec)) for key, v in items]
        scored = [s for s in scored if s[1] >= MIN_SCORE]
        return sorted(scored, key=lambda s: -s[1])[:top_k]

    def search_notations(self, vec, top_k=20):
        return self._top(self.notation_vecs.items(), vec, top_k)

    def search_register_terms(self, vec, top_k=20):
        return self._top(self.term_vecs.items(), vec, top_k)

    def score_for_notation(self, vec, nid):
        v = self.notation_vecs.get(nid)
        return None if v is None else float(v @ vec)


@pytest.fixture
def conn():
    connection = rvk_repository.get_connection(":memory:")
    connection.executescript(SCHEMA_SQL)
    for nid, notation, label, _, text in NOTATIONS:
        connection.execute(
            "INSERT INTO notation (id, notation, label) VALUES (?, ?, ?)",
            (nid, notation, label),
        )
        connection.execute(
            "INSERT INTO search_index (notation_id, text) VALUES (?, ?)", (nid, text)
        )
    for term, (_, nid, gnd_id) in REGISTER.items():
        connection.execute(
            "INSERT INTO register_entry (notation_id, term, entry_type, gnd_id) "
            "VALUES (?, ?, 'Sachschlagwort', ?)",
            (nid, term, gnd_id),
        )
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def service():
    return FakeEmbeddingService()


def _by_notation(hits):
    return {hit.notation.notation: hit for hit in hits}


def test_without_expansion_scores_follow_previous_formula(conn, service):
    # Cosine zur Anfrage + 0,03 Volltext + 0,015 Register, gedeckelt auf 1.0 (von Hand gerechnet)
    hits = ranking.search(conn, service, "Getriebe", [])

    assert [h.notation.notation for h in hits] == ["ZL 4100", "ZL 4300", "ZL 4200"]
    assert [h.score for h in hits] == pytest.approx([0.83, 0.315, 0.11])
    assert service.calls == ["Getriebe"]


def test_every_hit_has_traceable_evidence(conn, service):
    hits = ranking.search(conn, service, "Getriebe", ["Zahnrad", "Kupplung"])
    search_terms = {"Getriebe", "Zahnrad", "Kupplung"}

    assert hits
    for hit in hits:
        assert isinstance(hit, Hit)
        assert hit.evidences
        for evidence in hit.evidences:
            assert evidence.notation_id == hit.notation.id
            via = evidence.via_term
            if via.source == "vector":
                assert via.relation == "embedding"
                assert via.parent_term in search_terms
            else:
                assert via.term in search_terms


def test_multi_query_embeds_each_term_separately(conn, service):
    ranking.search(conn, service, "Getriebe", ["Zahnrad", "Kupplung"])

    assert service.calls == ["Getriebe", "Zahnrad", "Kupplung"]
    assert "Getriebe Zahnrad Kupplung" not in service.calls


def test_hit_only_from_expansion_term_carries_that_term(conn, service):
    hits = _by_notation(ranking.search(conn, service, "Getriebe", ["Zahnrad"]))

    hit = hits["ZL 4140"]
    assert [
        (e.via_term.term, e.via_term.source, e.match_type) for e in hit.evidences
    ] == [("Zahnrad", "llm", "vector")]
    assert hit.evidences[0].via_term.relation == "llm_unverified"
    assert hit.evidences[0].raw_score == pytest.approx(1.0)
    # Basis bleibt die Ähnlichkeit zur Eingabeanfrage, nicht zum Erweiterungsbegriff
    assert hit.score_components["cosine"] == pytest.approx(0.0)


def test_expansion_does_not_change_scores_of_input_hits(conn, service):
    plain = _by_notation(ranking.search(conn, service, "Getriebe", []))
    expanded = _by_notation(ranking.search(conn, service, "Getriebe", ["Zahnrad"]))

    for notation, hit in plain.items():
        assert expanded[notation].score == hit.score


def test_fts_only_hit_has_label_fts_evidence_and_bonus(conn, service):
    hit = _by_notation(ranking.search(conn, service, "Getriebe", []))["ZL 4200"]

    assert [e.match_type for e in hit.evidences] == ["label_fts"]
    assert hit.evidences[0].via_term.source == "input"
    assert hit.evidences[0].via_term.relation == "direct"
    assert hit.score_components["keyword_bonus"] == ranking.KEYWORD_BONUS
    assert hit.score_components["register_bonus"] == 0.0


def test_register_path_keeps_register_term_and_gnd_id(conn, service):
    hit = _by_notation(ranking.search(conn, service, "Getriebe", []))["ZL 4300"]

    register = [e for e in hit.evidences if e.match_type == "register_exact"]
    assert len(register) == 1
    via = register[0].via_term
    assert (via.term, via.source, via.relation, via.gnd_id, via.parent_term) == (
        "Kupplung",
        "vector",
        "embedding",
        "gnd-test-1",
        "Getriebe",
    )
    assert register[0].raw_score == pytest.approx(0.6)
    assert hit.score_components["register_bonus"] == ranking.TERM_MATCH_BONUS


def test_score_is_capped_sum_of_components_and_sorted(conn, service):
    hits = ranking.search(conn, service, "Getriebe", ["Zahnrad", "Kupplung"])

    for hit in hits:
        assert hit.score == pytest.approx(min(sum(hit.score_components.values()), 1.0))
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_non_assignable_notations_are_excluded(conn, service):
    hits = ranking.search(conn, service, "Getriebe", [])
    assert "ZL" not in {h.notation.notation for h in hits}


def test_result_count_is_limited(conn, service, monkeypatch):
    monkeypatch.setattr(ranking, "MAX_RESULTS", 2)
    assert len(ranking.search(conn, service, "Getriebe", ["Zahnrad"])) == 2


def test_duplicate_and_empty_expansion_terms_are_skipped(conn, service):
    ranking.search(
        conn, service, "Getriebe", ["Zahnrad", " zahnrad ", "", "  ", "getriebe"]
    )
    assert service.calls == ["Getriebe", "Zahnrad"]


def test_no_candidates_returns_empty_list(conn, service):
    assert ranking.search(conn, service, "Unbekannt", []) == []


def test_build_expansion_terms_marks_input_and_llm_terms():
    terms = ranking.build_expansion_terms("Getriebe", ["Zahnrad"])
    assert [(t.term, t.source, t.relation) for t in terms] == [
        ("Getriebe", "input", "direct"),
        ("Zahnrad", "llm", "llm_unverified"),
    ]


def test_hits_to_rows_keeps_previous_display_keys(conn, service):
    hits = ranking.search(conn, service, "Getriebe", [])
    rows = ranking.hits_to_rows(conn, hits)

    assert [r["notation"] for r in rows] == [h.notation.notation for h in hits]
    for row, hit in zip(rows, hits, strict=True):
        assert set(row) == {
            "notation",
            "label",
            "hierarchy_path",
            "related_terms",
            "confidence_pct",
            "rvk_online_url",
        }
        assert row["confidence_pct"] == round(hit.score * 100)
        assert row["rvk_online_url"] == ranking.build_rvk_online_url(
            hit.notation.notation
        )
    assert _by_notation(hits)["ZL 4300"].notation.id == 5
    assert rows[1]["related_terms"] == ["Kupplung"]


# --- P3: GND-Bedeutungen ----------------------------------------------------------------------


def _sense(gnd_id, name, terms, categories=()):
    return GndSense(
        gnd_id=gnd_id,
        preferred_name=name,
        subject_categories=tuple(categories),
        terms=tuple(
            ExpansionTerm(term, "gnd", relation, term_gnd_id, name)
            for term, relation, term_gnd_id in terms
        ),
    )


KUPPLUNG = _sense(
    "gnd-test-1",
    "Kupplung <Technik>",
    [("Kupplung <Technik>", "synonym", "gnd-test-1")],
)


def test_gnd_sense_gives_register_gnd_id_evidence_and_bonus(conn, service):
    plain = _by_notation(ranking.search(conn, service, "Getriebe", []))["ZL 4300"]
    result = ranking.search_result(conn, service, "Getriebe", [], gnd_senses=[KUPPLUNG])
    hit = _by_notation(result.hits)["ZL 4300"]

    gnd_evidence = [e for e in hit.evidences if e.match_type == "register_gnd_id"]
    assert len(gnd_evidence) == 1
    via = gnd_evidence[0].via_term
    assert (via.source, via.relation, via.gnd_id, via.parent_term) == (
        "gnd",
        "synonym",
        "gnd-test-1",
        "Kupplung <Technik>",
    )
    assert gnd_evidence[0].raw_score == 1.0
    assert hit.score_components["gnd_bonus"] == ranking.GND_SYNONYM_BONUS
    assert hit.score == pytest.approx(plain.score + ranking.GND_SYNONYM_BONUS)
    # Die GND-Begriffe laufen nicht durch die Vektorsuche
    assert service.calls == ["Getriebe", "Getriebe"]


def test_exact_register_term_from_gnd_gives_register_exact(conn, service):
    sense = _sense("gnd-x", "Antrieb", [("Kupplung", "related", None)])
    result = ranking.search_result(conn, service, "Getriebe", [], gnd_senses=[sense])
    hit = _by_notation(result.hits)["ZL 4300"]

    exact = [
        e
        for e in hit.evidences
        if e.match_type == "register_exact" and e.via_term.source == "gnd"
    ]
    assert [(e.via_term.term, e.raw_score) for e in exact] == [("Kupplung", 1.0)]
    assert hit.score_components["gnd_bonus"] == ranking.GND_RELATION_BONUS


def test_highest_gnd_bonus_counts_once(conn, service):
    sense = _sense(
        "gnd-test-1",
        "Kupplung <Technik>",
        [
            ("Kupplung <Technik>", "synonym", "gnd-test-1"),
            ("Kupplung", "broader", None),
        ],
    )
    result = ranking.search_result(conn, service, "Getriebe", [], gnd_senses=[sense])
    hit = _by_notation(result.hits)["ZL 4300"]
    assert hit.score_components["gnd_bonus"] == ranking.GND_SYNONYM_BONUS


def test_gnd_only_hit_enters_candidates(conn, service):
    assert ranking.search(conn, service, "Unbekannt", []) == []
    result = ranking.search_result(
        conn, service, "Unbekannt", [], gnd_senses=[KUPPLUNG]
    )

    assert [h.notation.notation for h in result.hits] == ["ZL 4300"]
    hit = result.hits[0]
    assert {e.via_term.source for e in hit.evidences} == {"gnd"}
    assert hit.score == pytest.approx(ranking.GND_SYNONYM_BONUS)
    assert [s.notation_ids for s in result.senses] == [[5]]


def test_search_result_without_senses_matches_search(conn, service):
    for query, terms in (("Getriebe", []), ("Getriebe", ["Zahnrad", "Kupplung"])):
        old = ranking.search(conn, service, query, terms)
        new = ranking.search_result(conn, service, query, terms)
        assert new.senses == []
        assert [(h.notation.id, h.score, h.evidences) for h in new.hits] == [
            (h.notation.id, h.score, h.evidences) for h in old
        ]
        assert all(h.score_components["gnd_bonus"] == 0.0 for h in new.hits)


def test_senses_are_ranked_and_carry_their_notations(conn, service):
    other = _sense(
        "gnd-y", "Kupplung <Familie>", [("Ehe", "synonym", None)], [("99.9", "Familie")]
    )
    technik = _sense(
        "gnd-test-1",
        "Kupplung <Technik>",
        [("Kupplung <Technik>", "synonym", "gnd-test-1")],
        [("31.6", "Maschinenbau")],
    )
    result = ranking.search_result(
        conn, service, "Getriebe", [], gnd_senses=[other, technik]
    )
    # mehr Treffer -> nach vorn, obwohl lobid sie als zweite geliefert hat
    assert [s.sense.gnd_id for s in result.senses] == ["gnd-test-1", "gnd-y"]
    assert result.senses[0].notation_ids == [5]
    assert result.senses[1].hit_count == 0

    rows = ranking.senses_to_rows(conn, result.senses)
    assert rows[0]["name"] == "Kupplung <Technik>"
    assert rows[0]["subject_categories"] == ["Maschinenbau"]
    assert [n["notation"] for n in rows[0]["notations"]] == ["ZL 4300"]
    assert rows[1]["notations"] == []


def _evidence(nid, sense):
    return Evidence(nid, sense.terms[0], "register_gnd_id", 1.0)


MAPPING = {"31.6": ("ZL",), "10.11*": ("QP",)}


def test_rank_senses_filter_before_hit_count():
    many = _sense("a", "Bank <Möbel>", [("Sitzbank", "synonym", "a")], [("99", "X")])
    few = _sense("b", "Bank", [("Kreditinstitut", "synonym", "b")], [("10.11a", "BWL")])
    evidence = {
        1: [_evidence(1, many)],
        2: [_evidence(2, many)],
        3: [_evidence(3, few)],
    }
    ranked = ranking.rank_senses([many, few], evidence, None, MAPPING)
    assert [r.sense.gnd_id for r in ranked] == ["a", "b"]

    ranked = ranking.rank_senses([many, few], evidence, ["qp"], MAPPING)
    assert [r.sense.gnd_id for r in ranked] == ["b", "a"]
    assert ranked[0].matches_filter is True
    assert ranked[0].rvk_groups == ("QP",)
    assert ranked[1].matches_filter is False


def test_rank_senses_tie_keeps_source_order():
    first = _sense("a", "Feder <Technik>", [("Feder1", "synonym", "a")])
    second = _sense("b", "Feder <Vogel>", [("Feder2", "synonym", "b")])
    evidence = {1: [_evidence(1, first)], 2: [_evidence(2, second)]}
    assert [
        r.sense.gnd_id
        for r in ranking.rank_senses([first, second], evidence, [], MAPPING)
    ] == [
        "a",
        "b",
    ]
    assert [
        r.sense.gnd_id
        for r in ranking.rank_senses([second, first], evidence, [], MAPPING)
    ] == [
        "b",
        "a",
    ]


def test_rvk_groups_for_sense_exact_and_prefix():
    sense = _sense(
        "a", "X", [], [("31.6", "Maschinenbau"), ("10.11b", "BWL"), ("31.60", "?")]
    )
    assert ranking.rvk_groups_for_sense(sense, MAPPING) == ("ZL", "QP")
