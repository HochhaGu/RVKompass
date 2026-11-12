"""Abstimmung der Scoring-Gewichte (P5) – Spielzeug-Merkmale, ohne Netz und Datenbank."""

import pytest

from app.services import scoring
from app.services import scoring_tuning as tuning
from app.services.evaluation import GoldEntry
from app.services.ranking import CandidateFeatures


def _entry(query, typ, expected=("ZL 1",)):
    return GoldEntry(query=query, expected=list(expected), typ=typ)


def test_split_is_deterministic_disjoint_complete_and_stratified():
    entries = [_entry(f"q{i}", "A" if i % 3 else "B") for i in range(11)]
    dev, test = tuning.split_indices(entries)
    assert (dev, test) == tuning.split_indices(entries)
    assert set(dev).isdisjoint(test)
    assert sorted(dev + test) == list(range(11))
    for typ in ("A", "B"):
        n_dev = sum(1 for i in dev if entries[i].typ == typ)
        n_test = sum(1 for i in test if entries[i].typ == typ)
        assert 0 <= n_dev - n_test <= 1
    # erster Eintrag je Typ geht in die Entwicklungshälfte
    assert 0 in dev and 1 in dev
    dev_entries, test_entries = tuning.split_gold(entries)
    assert [e.query for e in dev_entries] == [entries[i].query for i in dev]
    assert [e.query for e in test_entries] == [entries[i].query for i in test]


def test_grid_size_and_values():
    base = 3 * 3 * 3 * 3 * 6 * 3 * 3
    assert len(tuning.weight_grid("sum")) == base
    assert len(tuning.weight_grid("max_plus")) == base * 4
    assert len(tuning.weight_grid("sum_capped")) == base * 3
    combo = tuning.weight_grid("max_plus")[0]
    assert set(combo["weights"]) == set(tuning.WEIGHT_GRID)
    assert combo["path_bonus"] in tuning.PATH_BONUS_GRID
    with pytest.raises(ValueError):
        tuning.weight_grid("legacy")


def _feat(nid, notation, cosine, strengths):
    legacy = scoring.legacy_components(cosine, False, False, None, False)
    return CandidateFeatures(nid, notation, cosine, dict(strengths), legacy)


def _toy_queries():
    # Abfrage 1: das Erwartete hat nur einen Volltext-Pfad, ein Konkurrent hohe Cosine.
    q1 = tuning.QueryFeatures(
        _entry("eins", "A", ["ZL 1"]),
        [
            _feat(1, "ZL 1", 0.30, {"label_fts": 1.0, "vector": 0.3}),
            _feat(2, "ZX 2", 0.50, {"vector": 0.5}),
        ],
    )
    # Abfrage 2: GND-Synonym bringt das Erwartete nach vorn
    q2 = tuning.QueryFeatures(
        _entry("zwei", "A", ["ZL 3"]),
        [
            _feat(3, "ZL 3", 0.10, {"gnd_synonym": 1.0}),
            _feat(4, "ZX 4", 0.60, {"vector": 0.6}),
            _feat(5, "ZX 5", 0.55, {"vector": 0.55}),
        ],
    )
    return [q1, q2]


@pytest.mark.parametrize("formula", scoring.NEW_FORMULAS)
def test_fast_scorer_matches_reference_ranking(formula):
    qfs = _toy_queries()
    scorer = tuning.FastScorer(qfs)
    for combo in tuning.weight_grid(formula)[::97]:
        settings = tuning.settings_for(tuning.START_SETTINGS, formula, combo)
        fast = scorer.metrics(settings)
        ref = tuning.metrics_for(qfs, settings)
        # Referenz rundet den reziproken Rang je Anfrage auf 4 Stellen
        assert fast["mrr"] == pytest.approx(ref["mrr"], abs=1e-4)
        assert fast["hit@10"] == pytest.approx(ref["hit@10"], abs=1e-9)


def test_grid_search_finds_expected_optimum_and_prefers_start_on_ties():
    found = tuning.grid_search(_toy_queries(), "sum", tuning.START_SETTINGS)
    assert found["dev_fast"]["mrr"] == pytest.approx(1.0)
    settings = found["settings"]
    # Volltext muss gegen die Cosine-Lücke 0.2 × Vektorgewicht ankommen
    assert settings.weight("label_fts") > 0.2 * settings.weight("vector")
    # Gewichte, die keinen Einfluss haben, bleiben auf dem Startwert
    assert settings.weight("gnd_broader") == 0.7
    assert settings.weight("gnd_related") == 0.6
    assert settings.weight("register_similar") == 0.4


def _variant(name, dev_mrr, test, full_hit10=0.6):
    return {
        "variant": name,
        "dev": {"mrr": dev_mrr},
        "test": test,
        "full": {"hit@10": full_hit10},
    }


P4 = {"hit@10": 0.55, "mrr": 0.34}
LEGACY_TEST = {"hit@10": 0.5, "mrr": 0.3}


def test_accept_takes_best_variant_that_meets_all_conditions():
    decision = tuning.accept(
        [
            _variant("sum", 0.4, {"hit@10": 0.6, "mrr": 0.35}),
            _variant("max_plus", 0.5, {"hit@10": 0.54, "mrr": 0.4}),
        ],
        LEGACY_TEST,
        P4,
        gate_hit10=0.6,
    )
    assert decision["chosen"] == "sum" and decision["option"] == "c"
    assert [c["variant"] for c in decision["checks"]] == ["max_plus", "sum"]
    assert decision["checks"][0]["conditions"]["test_hit10_ge_p4"] is False


def test_accept_falls_back_to_legacy_and_notes_beating_legacy():
    decision = tuning.accept(
        [_variant("sum", 0.4, {"hit@10": 0.54, "mrr": 0.4})], LEGACY_TEST, P4, None
    )
    assert decision["chosen"] == "legacy" and decision["option"] == "b"
    assert decision["checks"][0]["beats_legacy_on_test_only"] is True


def test_accept_checks_legacy_and_gate():
    worse_than_legacy = tuning.accept(
        [_variant("sum", 0.4, {"hit@10": 0.6, "mrr": 0.35})],
        {"hit@10": 0.7, "mrr": 0.3},
        P4,
        None,
    )
    assert worse_than_legacy["chosen"] == "legacy"
    gate = tuning.accept(
        [_variant("sum", 0.4, {"hit@10": 0.6, "mrr": 0.35}, full_hit10=0.5)],
        LEGACY_TEST,
        P4,
        gate_hit10=0.56,
    )
    assert gate["chosen"] == "legacy"
    assert gate["checks"][0]["conditions"]["full_hit10_gate"] is False


def test_derive_tier_thresholds_by_share():
    rows = [(1.0 - i * 0.01, True, i < 5) for i in range(12)]  # alle passend
    rows += [(0.7 - i * 0.01, i % 2 == 0, False) for i in range(12)]  # halb
    rows += [(0.3 - i * 0.01, False, False) for i in range(20)]  # keiner
    result = tuning.derive_tier_thresholds(rows)
    assert result["method"] == "share"
    assert 0.6 <= result["tier_high"] <= 0.7
    assert result["tier_medium"] < result["tier_high"]
    settings = scoring.ScoringSettings(
        tier_high=result["tier_high"], tier_medium=result["tier_medium"]
    )
    shares = tuning.tier_shares(rows, settings)
    assert shares["hoch"]["share_main_group"] >= 0.8
    # „mittel“ gilt kumuliert: ab der Schwelle passt mindestens die Hälfte aller Treffer
    above = [r for r in rows if r[0] >= result["tier_medium"]]
    assert sum(r[1] for r in above) / len(above) >= 0.5
    assert shares["niedrig"]["share_main_group"] < 0.5
    assert sum(s["n"] for s in shares.values()) == len(rows)


def test_derive_tier_thresholds_quantile_fallback():
    rows = [(i / 30, i % 4 == 0, False) for i in range(30)]  # nie 50 % passend
    result = tuning.derive_tier_thresholds(rows)
    assert result["method"] == "quantile"
    assert result["tier_high"] > result["tier_medium"]
    with pytest.raises(ValueError):
        tuning.derive_tier_thresholds([])
