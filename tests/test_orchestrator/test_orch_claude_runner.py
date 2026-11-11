"""Tests der Einordnung von claude -p-Ergebnissen und der NEEDS_HUMAN-Erkennung."""

import json

import pytest

from orchestrator import claude_runner as cr


def _out(**kw):
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "fertig",
        "session_id": "s1",
        "api_error_status": None,
    }
    base.update(kw)
    return json.dumps(base)


def test_success_with_structured_output():
    res = cr.classify(
        0,
        _out(
            structured_output={"freigabe": True, "gruende": []},
            credential_expires_at="2026-12-08T01:11:39.947Z",
        ),
        "",
    )
    assert res.kind == cr.OK
    assert res.structured == {"freigabe": True, "gruende": []}
    assert res.auth_expires_at.startswith("2026-12-08")


@pytest.mark.parametrize(
    ("kw", "stderr", "kind"),
    [
        ({"is_error": True, "api_error_status": 429, "result": "x"}, "", cr.LIMIT),
        (
            {"is_error": True, "result": "Claude AI usage limit reached|1762880400"},
            "",
            cr.LIMIT,
        ),
        (
            {"is_error": True, "result": "You've hit your session limit · resets 3pm"},
            "",
            cr.LIMIT,
        ),
        ({"is_error": True, "api_error_status": 401, "result": "x"}, "", cr.AUTH),
        ({"is_error": True, "result": "OAuth token has expired"}, "", cr.AUTH),
        (
            {"is_error": True, "api_error_status": 529, "result": "Overloaded"},
            "",
            cr.TRANSIENT,
        ),
        (
            {"is_error": True, "subtype": "error_max_turns", "result": "abgebrochen"},
            "",
            cr.ERROR,
        ),
    ],
)
def test_error_kinds(kw, stderr, kind):
    assert cr.classify(1, _out(**kw), stderr).kind == kind


def test_unparsable_output_uses_stderr():
    assert cr.classify(1, "", "Error: usage limit reached").kind == cr.LIMIT
    assert cr.classify(1, "kaputt", "irgendwas").kind == cr.ERROR


def test_rate_limit_words_in_successful_result_are_ignored():
    # Ein erfolgreicher Lauf, der zufällig über Rate-Limits von lobid schreibt, ist OK.
    assert (
        cr.classify(0, _out(result="lobid hat ein rate limit, daher Cache"), "").kind
        == cr.OK
    )


@pytest.mark.parametrize(
    ("text", "question"),
    [
        (
            "Fertig.\nNEEDS_HUMAN: Darf ich bge-m3 (2,3 GB) laden?\n",
            "Darf ich bge-m3 (2,3 GB) laden?",
        ),
        (
            "**NEEDS_HUMAN: Bitte Lizenzdatei bereitstellen.**",
            "Bitte Lizenzdatei bereitstellen.",
        ),
        ("- NEEDS_HUMAN: A?\n- NEEDS_HUMAN: B?", "A?\nB?"),
        (
            "Alles erledigt, keine Rückfrage. Das Wort NEEDS_HUMAN steht hier nur im Satz.",
            None,
        ),
    ],
)
def test_needs_human(text, question):
    assert cr.find_needs_human(text) == question


def test_build_args_worker():
    args = cr.build_args(
        mode="dontAsk",
        model="opus",
        allow=["Read", "Bash(pytest *)"],
        deny=["Bash(git push *)"],
        settings_file=None,
    )
    assert args[:2] == ["-p", "--output-format"]
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--setting-sources") + 1] == "project"
    assert "Bash(git push *)" in args[args.index("--disallowedTools") :]


def test_build_args_review_has_schema_and_no_persistence():
    args = cr.build_args(
        mode="dontAsk",
        model="opus",
        allow=["Read"],
        deny=[],
        settings_file=None,
        json_schema={"type": "object"},
        tools=["Read", "Grep"],
        persist=False,
    )
    assert json.loads(args[args.index("--json-schema") + 1]) == {"type": "object"}
    assert args[args.index("--tools") + 1] == "Read,Grep"
    assert "--no-session-persistence" in args


def test_runner_strips_api_keys_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("CLAUDE_API_KEY", "y")
    env = cr.ClaudeRunner("claude", tmp_path / "bin", 10)._env()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_API_KEY" not in env
    assert env["PATH"].startswith(str(tmp_path / "bin"))
