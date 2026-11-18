"""Sprachmodell-Baustein (P4) – ohne Netz: Der Anthropic-Client ist eine Attrappe."""

import inspect
import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.models import LLM_RELATIONS, SCHEMA_SQL
from app.services import query_expansion as qe
from app.services import rvk_repository

ANSWER = {
    "begriffe": [
        {
            "begriff": "Zahnrad",
            "beziehung": "unterbegriff",
            "begruendung": "Bauteil eines Getriebes",
        },
        {
            "begriff": "Antriebstechnik",
            "beziehung": "oberbegriff",
            "begruendung": "Fachgebiet",
        },
    ]
}


@pytest.fixture
def conn():
    connection = rvk_repository.get_connection(":memory:")
    connection.executescript(SCHEMA_SQL)
    yield connection
    connection.close()


class FakeClient:
    def __init__(self, text=None, exc=None, blocks=None):
        self.calls = []
        self.text = json.dumps(ANSWER) if text is None else text
        self.exc = exc
        self.blocks = blocks
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        blocks = self.blocks or [SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=blocks)


def _factory(client, seen=None):
    def factory(api_key, timeout, max_retries):
        if seen is not None:
            seen.update(api_key=api_key, timeout=timeout, max_retries=max_retries)
        return client

    return factory


def _suggest(conn, client, **kwargs):
    kwargs.setdefault("use_output_config", True)
    return qe.suggest_terms(
        conn, "sk-test", "Getriebe", client_factory=_factory(client), **kwargs
    )


# --- parse_suggestions ------------------------------------------------------------------------


def test_parse_object_and_list_form():
    as_object = qe.parse_suggestions(json.dumps(ANSWER), "Getriebe")
    as_list = qe.parse_suggestions(json.dumps(ANSWER["begriffe"]), "Getriebe")
    assert as_object == as_list
    assert [s["begriff"] for s in as_object] == ["Zahnrad", "Antriebstechnik"]
    assert as_object[0] == {
        "begriff": "Zahnrad",
        "beziehung": "unterbegriff",
        "begruendung": "Bauteil eines Getriebes",
    }


def test_parse_drops_unknown_relation_missing_term_duplicates_and_query():
    text = json.dumps(
        [
            {"begriff": "Zahnrad", "beziehung": "erfunden", "begruendung": "x"},
            {"begriff": "", "beziehung": "synonym", "begruendung": "x"},
            {"beziehung": "synonym"},
            "kein Objekt",
            {"begriff": "getriebe", "beziehung": "synonym", "begruendung": "x"},
            {"begriff": "Welle", "beziehung": "Verwandt", "begruendung": "x"},
            {"begriff": " welle ", "beziehung": "synonym", "begruendung": "x"},
        ]
    )
    result = qe.parse_suggestions(text, "Getriebe")
    assert [(s["begriff"], s["beziehung"]) for s in result] == [("Welle", "verwandt")]


def test_parse_shortens_rationale_to_12_words():
    long = " ".join(f"w{i}" for i in range(20))
    (item,) = qe.parse_suggestions(
        json.dumps([{"begriff": "A", "beziehung": "synonym", "begruendung": long}])
    )
    assert len(item["begruendung"].split()) == 12


def test_parse_limits_number_of_items():
    items = [
        {"begriff": f"B{i}", "beziehung": "verwandt", "begruendung": ""}
        for i in range(12)
    ]
    assert len(qe.parse_suggestions(json.dumps(items), max_items=8)) == 8


def test_parse_accepts_code_fence():
    text = "```json\n" + json.dumps(ANSWER) + "\n```"
    assert len(qe.parse_suggestions(text, "Getriebe")) == 2


@pytest.mark.parametrize("text", ["kein json", "", '{"andere": 1}', "42"])
def test_parse_rejects_broken_json(text):
    with pytest.raises((ValueError, TypeError)):
        qe.parse_suggestions(text)


# --- Prompt und Schema ------------------------------------------------------------------------


def test_prompt_contains_context_and_relations():
    prompt = qe.build_prompt("Getriebe")
    assert "Bibliothek mit technischem Schwerpunkt, Nutzer ist fachfremd." in prompt
    assert '"Getriebe"' in prompt
    for relation in qe.RELATION_VALUES:
        assert relation in prompt
    assert "12 Wörter" in prompt


def test_relation_values_match_model():
    assert set(qe.RELATION_VALUES) == LLM_RELATIONS


def test_schema_has_no_unsupported_constraints():
    text = json.dumps(qe.OUTPUT_SCHEMA)
    assert "maxItems" not in text and "maxLength" not in text
    item = qe.OUTPUT_SCHEMA["properties"]["begriffe"]["items"]
    assert qe.OUTPUT_SCHEMA["additionalProperties"] is False
    assert item["additionalProperties"] is False
    assert set(item["properties"]["beziehung"]["enum"]) == LLM_RELATIONS


# --- suggest_terms ----------------------------------------------------------------------------


def test_call_uses_model_schema_effort_and_no_temperature(conn):
    client = FakeClient()
    seen = {}
    result = qe.suggest_terms(
        conn,
        "sk-test",
        "Getriebe",
        model="claude-haiku-5-5",
        timeout=7,
        max_retries=1,
        client_factory=_factory(client, seen),
        use_output_config=True,
    )
    assert [s["begriff"] for s in result] == ["Zahnrad", "Antriebstechnik"]
    assert seen == {"api_key": "sk-test", "timeout": 7, "max_retries": 1}
    (call,) = client.calls
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"] == {
        "type": "json_schema",
        "schema": qe.OUTPUT_SCHEMA,
    }
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in call
    assert "fachfremd" in call["messages"][0]["content"]


def test_without_output_config_support_call_omits_it(conn):
    client = FakeClient()
    _suggest(conn, client, use_output_config=False)
    assert "output_config" not in client.calls[0]


def test_text_is_read_from_text_block_after_thinking(conn):
    blocks = [
        SimpleNamespace(type="thinking", thinking="…"),
        SimpleNamespace(type="text", text=json.dumps(ANSWER)),
    ]
    assert len(_suggest(conn, FakeClient(blocks=blocks))) == 2


def test_result_is_cached_with_rationale_and_p4_prefix(conn):
    first = FakeClient()
    result = _suggest(conn, first)
    cached = rvk_repository.get_expansion_cache(conn, "p4|getriebe")
    assert json.loads(cached)["begriffe"] == result
    assert rvk_repository.get_expansion_cache(conn, "getriebe") is None

    second = FakeClient()
    stats = {}
    assert _suggest(conn, second, stats=stats) == result
    assert second.calls == []
    assert stats == {"cache_hits": 1}


def test_old_cache_entry_without_prefix_is_ignored(conn):
    rvk_repository.set_expansion_cache(conn, "getriebe", json.dumps(["Alt"]))
    client = FakeClient()
    result = _suggest(conn, client)
    assert len(client.calls) == 1
    assert [s["begriff"] for s in result] == ["Zahnrad", "Antriebstechnik"]
    # alter Eintrag bleibt unverändert
    assert json.loads(rvk_repository.get_expansion_cache(conn, "getriebe")) == ["Alt"]


def test_error_gives_empty_list_and_is_not_cached(conn, caplog):
    client = FakeClient(exc=RuntimeError("geheim sk-test"))
    stats = {}
    assert _suggest(conn, client, stats=stats) == []
    assert rvk_repository.get_expansion_cache(conn, "p4|getriebe") is None
    assert stats == {"api_calls": 1, "api_errors": 1}
    assert "sk-test" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_broken_answer_gives_empty_list_and_is_not_cached(conn):
    stats = {}
    assert _suggest(conn, FakeClient(text="Das ist kein JSON"), stats=stats) == []
    assert rvk_repository.get_expansion_cache(conn, "p4|getriebe") is None
    assert stats["api_errors"] == 1


def test_call_origin_selects_counter_column(conn):
    _suggest(conn, FakeClient(), call_origin="eval")
    row = conn.execute(
        "SELECT claude_calls, claude_calls_eval FROM daily_usage"
    ).fetchone()
    assert tuple(row) == (0, 1)


def test_daily_limit_applies_to_search_and_eval_together(conn):
    rvk_repository.get_and_increment_daily_claude_calls(conn, "eval")
    stats = {}
    # eine Evaluation hat das Limit von 1 schon verbraucht: die Suche bekommt keinen Aufruf
    assert _suggest(conn, FakeClient(), daily_limit=1, stats=stats) == []
    assert stats == {"limit_reached": 1}


def test_daily_limit_reached_gives_empty_list(conn):
    client = FakeClient()
    stats = {}
    assert _suggest(conn, client, daily_limit=0, stats=stats) == []
    assert client.calls == []
    assert stats == {"limit_reached": 1}


def test_without_key_no_call(conn):
    client = FakeClient()
    assert qe.suggest_terms(conn, "", "Getriebe", client_factory=_factory(client)) == []
    assert client.calls == []


def test_empty_query_gives_empty_list(conn):
    client = FakeClient()
    assert (
        qe.suggest_terms(conn, "sk-test", "   ", client_factory=_factory(client)) == []
    )
    assert client.calls == []


def test_config_names_current_haiku_model():
    assert Config.CLAUDE_MODEL == "claude-haiku-5-5"
    assert Config.CLAUDE_EFFORT == "low"
    assert Config.CLAUDE_TIMEOUT_SECONDS > 0


def test_installed_sdk_matches_requirements():
    """Die installierte anthropic-Bibliothek muss zu requirements.txt passen und die genutzte
    Schnittstelle bieten (api_key, timeout, max_retries; output_config bei messages.create)."""
    anthropic = pytest.importorskip("anthropic")
    from anthropic.resources.messages import Messages

    from app.config import BASE_DIR

    pinned = next(
        line.split("==", 1)[1].strip()
        for line in (BASE_DIR / "requirements.txt").read_text().splitlines()
        if line.startswith("anthropic==")
    )
    assert anthropic.__version__ == pinned
    init_params = inspect.signature(anthropic.Anthropic.__init__).parameters
    for name in ("api_key", "timeout", "max_retries"):
        assert name in init_params
    assert "output_config" in inspect.signature(Messages.create).parameters
    assert qe.sdk_supports_output_config() is True
