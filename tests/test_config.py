import importlib

import app.config as config_module


def _reload_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ENABLE_QUERY_EXPANSION", raising=False)
    else:
        monkeypatch.setenv("ENABLE_QUERY_EXPANSION", value)
    return importlib.reload(config_module)


def test_llm_expansion_is_off_by_default(monkeypatch):
    # Entscheidung P4: Echttest ohne Gewinn bei „Fachfremd umschrieben“ -> Standard aus.
    try:
        assert (
            _reload_with_env(monkeypatch, None).Config.ENABLE_QUERY_EXPANSION is False
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_scoring_config_is_valid():
    from app.services import scoring

    config = config_module.Config
    assert config.SCORING_FORMULA in scoring.FORMULAS
    assert set(config.SCORE_WEIGHTS) == set(scoring.PATH_TYPES)
    assert all(0 <= w <= 2 for w in config.SCORE_WEIGHTS.values())
    assert config.SCORE_TIER_HIGH > config.SCORE_TIER_MEDIUM > 0
    assert 0 <= config.SCORE_PATH_BONUS <= config.SCORE_PATH_BONUS_CAP
    # lässt sich ohne Fehler in Einstellungen übernehmen
    scoring.ScoringSettings.from_config(config)


def test_scoring_formula_can_be_switched_via_env(monkeypatch):
    try:
        monkeypatch.setenv("SCORING_FORMULA", "max_plus")
        assert importlib.reload(config_module).Config.SCORING_FORMULA == "max_plus"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_llm_expansion_can_be_switched_on_via_env(monkeypatch):
    try:
        assert (
            _reload_with_env(monkeypatch, "true").Config.ENABLE_QUERY_EXPANSION is True
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)
