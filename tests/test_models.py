import pytest

from app.models import (
    EXPANSION_RELATIONS,
    EXPANSION_SOURCES,
    LLM_RELATIONS,
    Evidence,
    ExpansionTerm,
    Hit,
    LlmSuggestion,
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


def test_llm_unverified_relation_is_removed():
    # P4: Nicht geprüfte LLM-Begriffe werden nie gesucht, also nie ein ExpansionTerm
    assert "llm_unverified" not in EXPANSION_RELATIONS
    with pytest.raises(ValueError):
        ExpansionTerm("x", "llm", "llm_unverified")


def test_llm_relations_match_task():
    assert LLM_RELATIONS == {
        "synonym",
        "oberbegriff",
        "unterbegriff",
        "verwandt",
        "fachkontext",
    }


def test_llm_suggestion_verified_needs_via_and_matched_term():
    ok = LlmSuggestion(
        "Zahnrad",
        "verwandt",
        "Bauteil im Getriebe",
        True,
        "gnd",
        "Zahnrad",
        "4067467-5",
    )
    assert ok.verified and ok.verified_via == "gnd"
    with pytest.raises(ValueError):
        LlmSuggestion("Zahnrad", "verwandt", "", True, None, "Zahnrad")
    with pytest.raises(ValueError):
        LlmSuggestion("Zahnrad", "verwandt", "", True, "gnd", None)


def test_llm_suggestion_unverified_has_no_via():
    plain = LlmSuggestion("Zahnradkiste", "verwandt")
    assert plain.verified is False and plain.verified_via is None
    with pytest.raises(ValueError):
        LlmSuggestion("Zahnrad", "verwandt", "", False, "gnd", "Zahnrad")


def test_llm_suggestion_rejects_unknown_relation_and_via():
    with pytest.raises(ValueError):
        LlmSuggestion("Zahnrad", "irgendwas")
    with pytest.raises(ValueError):
        LlmSuggestion("Zahnrad", "synonym", "", True, "wikipedia", "Zahnrad")


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
