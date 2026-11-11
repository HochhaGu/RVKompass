"""Evidence-Datenmodell und Multi-Query in ranking.search – ohne Netz, ohne Modell, ohne echte Daten."""

import numpy as np
import pytest

from app.models import SCHEMA_SQL, Hit
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
