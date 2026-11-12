"""Erklärbarer Score (P5) – reine Funktionen, ohne Netz und Datenbank."""

import pytest

from app.config import Config
from app.models import Evidence, ExpansionTerm
from app.services import scoring
from app.services.scoring_tuning import START_SETTINGS

INPUT = "getriebe"


def _ev(term, source, relation, match_type, raw=1.0, parent=None):
    return Evidence(
        1, ExpansionTerm(term, source, relation, None, parent), match_type, raw
    )


@pytest.mark.parametrize(
    "evidence,expected",
    [
        (_ev("Getriebe", "input", "direct", "label_fts", 7.3), ("label_fts", 1.0)),
        (_ev("Getriebe", "input", "direct", "vector", 0.62), ("vector", 0.62)),
        (
            _ev("Getriebe", "vector", "embedding", "register_exact", 0.97, "Getriebe"),
            ("register_exact", 1.0),
        ),
        (
            _ev("Zahnrad", "vector", "embedding", "register_exact", 0.55, "Getriebe"),
            ("register_similar", 0.55),
        ),
        (
            _ev("Zahnrad", "vector", "embedding", "register_exact", 0.7, "Zahnrad"),
            ("llm_verified", 0.7),
        ),
        (_ev("X", "gnd", "synonym", "register_gnd_id"), ("gnd_synonym", 1.0)),
        (_ev("X", "gnd", "broader", "register_exact"), ("gnd_broader", 1.0)),
        (_ev("X", "gnd", "narrower", "register_exact"), ("gnd_narrower", 1.0)),
        (_ev("X", "gnd", "related", "register_gnd_id"), ("gnd_related", 1.0)),
        (_ev("X", "llm", "llm_verified", "register_exact"), ("llm_verified", 1.0)),
        (_ev("X", "llm", "llm_verified", "vector", 0.4), ("llm_verified", 0.4)),
    ],
)
def test_classify_evidence(evidence, expected):
    path, strength = scoring.classify_evidence(evidence, INPUT)
    assert path == expected[0]
    assert strength == pytest.approx(expected[1])


def test_direct_register_match_is_case_and_space_insensitive():
    evidence = _ev(
        " GETRIEBE ", "vector", "embedding", "register_exact", 0.9, "Getriebe"
    )
    assert scoring.classify_evidence(evidence, INPUT)[0] == "register_exact"


def test_path_strengths_keeps_strongest_per_path():
    strengths = scoring.path_strengths(
        [
            _ev("A", "vector", "embedding", "register_exact", 0.4, "Getriebe"),
            _ev("B", "vector", "embedding", "register_exact", 0.6, "Getriebe"),
            _ev("X", "gnd", "related", "register_exact"),
        ],
        INPUT,
    )
    assert strengths == {"register_similar": 0.6, "gnd_related": 1.0}


SETTINGS = scoring.ScoringSettings(
    formula="sum",
    weights=dict(START_SETTINGS.weights),
    path_bonus=0.05,
    path_bonus_cap=0.15,
    sum_cap=1.2,
)
STRENGTHS = {"gnd_synonym": 1.0, "label_fts": 1.0, "vector": 0.5}


def test_sum_formula_example():
    score, parts = scoring.combine("sum", STRENGTHS, 0.5, SETTINGS)
    # 0.9 + 0.2 + 0.4 × 0.5
    assert score == pytest.approx(1.3)
    assert parts == pytest.approx({"gnd_synonym": 0.9, "label_fts": 0.2, "vector": 0.2})


def test_max_plus_formula_example():
    score, parts = scoring.combine("max_plus", STRENGTHS, 0.5, SETTINGS)
    # max(0.9, 0.2) + 0.4 × 0.5 + 0.05 × (3 Pfadtypen − 1)
    assert score == pytest.approx(0.9 + 0.2 + 0.1)
    assert parts == pytest.approx(
        {"gnd_synonym": 0.9, "vector": 0.2, "path_bonus": 0.1}
    )


def test_sum_capped_scales_proportionally():
    settings = SETTINGS.replace(sum_cap=0.55)
    score, parts = scoring.combine("sum_capped", STRENGTHS, 0.5, settings)
    assert parts["gnd_synonym"] + parts["label_fts"] == pytest.approx(0.55)
    assert parts["gnd_synonym"] / parts["label_fts"] == pytest.approx(0.9 / 0.2)
    assert score == pytest.approx(0.55 + 0.2)


@pytest.mark.parametrize("formula", scoring.NEW_FORMULAS)
@pytest.mark.parametrize(
    "strengths,cosine",
    [
        (STRENGTHS, 0.5),
        ({"register_exact": 1.0, "gnd_broader": 1.0, "register_similar": 0.3}, 0.1),
        ({}, 0.7),
        ({"vector": 0.3}, -0.2),
    ],
)
def test_components_sum_to_score(formula, strengths, cosine):
    score, parts = scoring.combine(formula, strengths, cosine, SETTINGS)
    assert sum(parts.values()) == pytest.approx(score)
    assert all(value >= 0 for value in parts.values())


def test_path_bonus_is_capped():
    many = {p: 1.0 for p in scoring.PATH_TYPES}
    _, parts = scoring.combine("max_plus", many, 1.0, SETTINGS)
    assert parts["path_bonus"] == pytest.approx(0.15)


def test_vector_only_hit_has_only_vector_component():
    for formula in scoring.NEW_FORMULAS:
        score, parts = scoring.combine(formula, {"vector": 0.6}, 0.6, SETTINGS)
        assert parts == pytest.approx({"vector": 0.24})
        assert score == pytest.approx(0.24)


@pytest.mark.parametrize(
    "score,tier",
    [
        (0.8, "hoch"),
        (0.75, "hoch"),
        (0.7499, "mittel"),
        (0.55, "mittel"),
        (0.1, "niedrig"),
    ],
)
def test_tier_for_boundaries(score, tier):
    settings = SETTINGS.replace(tier_high=0.75, tier_medium=0.55)
    assert scoring.tier_for(score, settings) == tier


def test_unknown_formula_or_weight_raises():
    with pytest.raises(ValueError):
        scoring.ScoringSettings(formula="magie")
    with pytest.raises(ValueError):
        scoring.ScoringSettings(formula="legacy", weights={"zauber": 1.0})
    with pytest.raises(ValueError):
        scoring.ScoringSettings(formula="sum", weights={"vector": 1.0})
    with pytest.raises(ValueError):
        scoring.ScoringSettings(formula="legacy", tier_high=0.2, tier_medium=0.5)
    with pytest.raises(ValueError):
        scoring.combine("legacy", {}, 0.5, SETTINGS)


def test_legacy_components_match_previous_constants():
    parts = scoring.legacy_components(0.7, True, True, {"broader", "synonym"}, True)
    assert parts == {
        "cosine": 0.7,
        "keyword_bonus": 0.03,
        "register_bonus": 0.015,
        "gnd_bonus": 0.08,
        "llm_bonus": 0.0,
    }
    assert (
        scoring.legacy_components(0.1, False, False, {"related"}, False)["gnd_bonus"]
        == 0.02
    )
    assert scoring.legacy_score(parts) == pytest.approx(0.825)
    assert scoring.legacy_score({**parts, "cosine": 0.99}) == 1.0


def test_from_config_reads_class_and_mapping():
    from_class = scoring.ScoringSettings.from_config(Config)
    assert from_class.formula == Config.SCORING_FORMULA
    assert from_class.tier_high == Config.SCORE_TIER_HIGH
    from_mapping = scoring.ScoringSettings.from_config({"SCORE_TIER_MEDIUM": 0.1})
    assert from_mapping.tier_medium == 0.1
    assert from_mapping.weights == Config.SCORE_WEIGHTS


def test_every_component_key_has_a_german_label():
    keys = set(scoring.PATH_TYPES) | {"path_bonus"}
    keys |= set(scoring.legacy_components(0.5, True, True, {"synonym"}, True))
    assert keys <= set(scoring.PATH_LABELS)
    assert not any("%" in label for label in scoring.PATH_LABELS.values())
