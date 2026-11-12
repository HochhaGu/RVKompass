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


def test_default_main_groups_empty_by_default(monkeypatch):
    try:
        monkeypatch.delenv("DEFAULT_MAIN_GROUPS", raising=False)
        assert importlib.reload(config_module).Config.DEFAULT_MAIN_GROUPS == ()
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_default_main_groups_from_env(monkeypatch):
    try:
        monkeypatch.setenv("DEFAULT_MAIN_GROUPS", "zl, ZM,,zl ")
        assert importlib.reload(config_module).Config.DEFAULT_MAIN_GROUPS == (
            "ZL",
            "ZM",
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_operations_defaults(monkeypatch):
    names = (
        "GND_SOURCE",
        "SYNC_MAX_COUNT_DEVIATION",
        "SYNC_MAX_HIT10_DROP",
        "SYNC_BACKUP_KEEP",
        "SYNC_SAMPLE_NOTATIONS",
        "SMTP_PORT",
        "SMTP_STARTTLS",
        "PROXY_FIX_X_FOR",
        "GUNICORN_PID_PATH",
    )
    try:
        for name in names:
            monkeypatch.delenv(name, raising=False)
        config = importlib.reload(config_module).Config
        assert config.GND_SOURCE == "local"
        assert config.GND_DATABASE_PATH.endswith("data/gnd.sqlite3")
        assert config.USAGE_DATABASE_PATH.endswith("data/usage.sqlite3")
        assert config.SYNC_WORK_DIR.endswith("data/staging")
        assert config.SYNC_BACKUP_DIR.endswith("data/backups")
        assert config.SYNC_MAX_COUNT_DEVIATION == 0.05
        assert config.SYNC_MAX_HIT10_DROP == 0.05
        assert config.SYNC_BACKUP_KEEP == 2
        assert config.SYNC_SAMPLE_NOTATIONS == ()
        assert config.SMTP_PORT == 587 and config.SMTP_STARTTLS is True
        assert config.SMTP_TIMEOUT_SECONDS == 30
        assert config.GUNICORN_PID_PATH == "/tmp/gunicorn.pid"
        assert config.PROXY_FIX_X_FOR == 0
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_operations_from_env(monkeypatch):
    try:
        monkeypatch.setenv("GND_SOURCE", " Lobid ")
        monkeypatch.setenv("SYNC_SAMPLE_NOTATIONS", "ZL 4620, ,ST 230")
        monkeypatch.setenv("SYNC_MAX_HIT10_DROP", "0.02")
        config = importlib.reload(config_module).Config
        assert config.GND_SOURCE == "lobid"
        assert config.SYNC_SAMPLE_NOTATIONS == ("ZL 4620", "ST 230")
        assert config.SYNC_MAX_HIT10_DROP == 0.02
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
