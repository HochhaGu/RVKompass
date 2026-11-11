import pytest

from app.models import (
    EXPANSION_RELATIONS,
    EXPANSION_SOURCES,
    Evidence,
    ExpansionTerm,
    Hit,
    Notation,
)


def _notation(nid=1):
    return Notation(nid, "ZL 4100", "Getriebe", None, None, None)


def _evidence(nid=1):
    return Evidence(nid, ExpansionTerm("Getriebe", "input", "direct"), "vector", 0.8)


def test_hit_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="ohne Evidence"):
        Hit(_notation(), [], 0.5, {"cosine": 0.5})


def test_hit_with_evidence_of_other_notation_is_rejected():
    with pytest.raises(ValueError):
        Hit(_notation(1), [_evidence(2)], 0.5, {"cosine": 0.5})


def test_hit_with_matching_evidence_is_accepted():
    hit = Hit(_notation(1), [_evidence(1)], 0.5, {"cosine": 0.5})
    assert hit.evidences[0].via_term.term == "Getriebe"


@pytest.mark.parametrize("source", ["input", "gnd", "llm", "vector"])
def test_expansion_term_accepts_sources_from_task(source):
    assert ExpansionTerm("x", source, "direct").source == source


@pytest.mark.parametrize(
    "relation",
    [
        "direct",
        "synonym",
        "broader",
        "narrower",
        "related",
        "llm_verified",
        "embedding",
    ],
)
def test_expansion_term_accepts_relations_from_task(relation):
    assert ExpansionTerm("x", "gnd", relation).relation == relation


def test_provisional_llm_unverified_relation_is_allowed():
    # Vorläufiger Wert bis P4 (siehe Kommentar in app/models.py)
    assert "llm_unverified" in EXPANSION_RELATIONS


def test_expansion_term_rejects_unknown_source():
    assert "wikipedia" not in EXPANSION_SOURCES
    with pytest.raises(ValueError):
        ExpansionTerm("x", "wikipedia", "direct")


def test_expansion_term_rejects_unknown_relation():
    with pytest.raises(ValueError):
        ExpansionTerm("x", "gnd", "irgendwas")


def test_expansion_term_is_hashable_and_keeps_chain():
    term = ExpansionTerm(
        "Zahnrad", "vector", "embedding", gnd_id="4067467-5", parent_term="Getriebe"
    )
    assert {term: 1}[term] == 1
    assert term.parent_term == "Getriebe"


@pytest.mark.parametrize(
    "match_type", ["register_exact", "register_gnd_id", "label_fts", "vector"]
)
def test_evidence_accepts_known_match_types(match_type):
    term = ExpansionTerm("x", "input", "direct")
    assert Evidence(1, term, match_type, 0.1).match_type == match_type


def test_evidence_rejects_unknown_match_type():
    with pytest.raises(ValueError):
        Evidence(1, ExpansionTerm("x", "input", "direct"), "zufall", 0.1)
