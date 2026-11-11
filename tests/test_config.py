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


def test_llm_expansion_can_be_switched_on_via_env(monkeypatch):
    try:
        assert (
            _reload_with_env(monkeypatch, "true").Config.ENABLE_QUERY_EXPANSION is True
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)
