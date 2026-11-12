"""Schutzprüfung im Commit: nur echte Geheimnisse, Meldung mit Name und Datei, nie mit Wert."""

import subprocess

import pytest

from orchestrator.envfile import Secrets, is_secret_name
from orchestrator.git_ops import Git, SafetyError, scan_for_secrets

# Die Werte, die in P0 Fehlalarm ausgelöst haben – alle harmlos
HARMLESS = {
    "FLASK_ENV": "development",
    "ENABLE_QUERY_EXPANSION": "true",
    "DATABASE_PATH": "/home/guenther/dev/rvkompass/data/rvk.sqlite3",
    "EMBEDDINGS_PATH": "/home/guenther/dev/rvkompass/data/embeddings.npy",
    "EMBEDDING_MODEL_NAME": "paraphrase-multilingual-MiniLM-L12-v2",
    "SMTP_SECURITY": "starttls",
}
SECRET = {
    "CLAUDE_API_KEY": "sk-ant-api03-ABCDEFGHIJKLMNOP",
    "SYNC_TOKEN": "sync-token-0123456789",
    "SECRET_KEY": "flask-secret-0123456789",
    "SMTP_PASSWORD": "Mail-Passwort-2026!",
    "STATUS_PASSWORD": "Status-Passwort-2026",
    "SMTP_HOST": "smtp.mailbox.example.invalid",
    "MAIL_TO": "empfaenger@example.invalid",
}
OTHER = {
    "SMTP_USER": "benutzer@example.invalid",
    "SMTP_PORT": "587",
    "MAIL_FROM": "a@b.invalid",
}
TOKEN_FILE = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-ZYXWVUTSRQPONMLK"}


def _secrets():
    return Secrets({**HARMLESS, **SECRET, **OTHER}, extra=TOKEN_FILE)


def _diff(path: str, *added: str, removed: str = "") -> str:
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    if removed:
        lines.append(f"-{removed}")
    lines += [f"+{a}" for a in added]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("CLAUDE_API_KEY", True),
        ("SYNC_TOKEN", True),
        ("SECRET_KEY", True),
        ("SMTP_PASSWORD", True),
        ("STATUS_PASSWORD", True),
        ("SMTP_HOST", True),
        ("MAIL_TO", True),
        ("api_key", True),
        ("FLASK_ENV", False),
        ("DATABASE_PATH", False),
        ("SMTP_SECURITY", False),
        ("SMTP_USER", False),
        ("MAIL_FROM", False),
        ("EMBEDDING_MODEL_NAME", False),
    ],
)
def test_secret_names(name, secret):
    assert is_secret_name(name) is secret


def test_secret_items_are_exactly_the_secret_keys_and_claude_env():
    names = {n for n, _ in _secrets().secret_items()}
    assert names == set(SECRET) | {"CLAUDE_CODE_OAUTH_TOKEN (claude.env)"}


def test_values_shorter_than_12_are_ignored():
    s = Secrets({"SMTP_PASSWORD": "elf-Zeichen", "SYNC_TOKEN": "zwoelf-Zeich"})
    assert len("elf-Zeichen") == 11 and len("zwoelf-Zeich") == 12
    assert [n for n, _ in s.secret_items()] == ["SYNC_TOKEN"]


def test_p0_false_alarm_is_gone():
    diff = _diff(
        "app/config.py",
        'FLASK_ENV = "development"',
        'ENABLE_QUERY_EXPANSION = "true"',
        'DATABASE_PATH = "/home/guenther/dev/rvkompass/data/rvk.sqlite3"',
        'EMBEDDINGS_PATH = "/home/guenther/dev/rvkompass/data/embeddings.npy"',
        'EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"',
        'SMTP_SECURITY = "starttls"',
    )
    assert scan_for_secrets(diff, _secrets().secret_items()) == []


@pytest.mark.parametrize("name", sorted(SECRET))
def test_each_secret_is_found_with_name_and_file_but_never_the_value(name):
    value = SECRET[name]
    diff = _diff("docs/bericht.md", f"Gefunden: {value}")
    hits = scan_for_secrets(diff, _secrets().secret_items())
    assert f"{name} in docs/bericht.md" in hits
    joined = " ".join(hits)
    assert value not in joined
    assert not any(
        value[i : i + 6] in joined for i in range(len(value) - 5)
    )  # auch kein Teil


def test_claude_env_token_is_found():
    diff = _diff("README.md", "token: sk-ant-oat01-ZYXWVUTSRQPONMLK")
    hits = scan_for_secrets(diff, _secrets().secret_items())
    assert "CLAUDE_CODE_OAUTH_TOKEN (claude.env) in README.md" in hits
    assert not any("ZYXW" in h or "oat01" in h for h in hits)


def test_pattern_hit_names_kind_and_file_without_value():
    diff = _diff("app/x.py", 'KEY = "sk-ant-api03-ganzandererwert99"')
    hits = scan_for_secrets(diff, [])
    assert hits == ["Anthropic-Schlüssel (Muster) in app/x.py"]


def test_removed_lines_and_multiple_files():
    value = SECRET["SMTP_PASSWORD"]
    diff = _diff("alt.py", "harmlos", removed=f"pw = '{value}'") + _diff(
        "neu/conf.py", f"pw = '{value}'"
    )
    assert scan_for_secrets(diff, _secrets().secret_items()) == [
        "SMTP_PASSWORD in neu/conf.py"
    ]


def test_commit_is_blocked_with_name_and_file_only(tmp_path):
    value = SECRET["SYNC_TOKEN"]
    calls = []

    def run(cmd, cwd, **kw):
        calls.append(cmd)
        if "--name-only" in cmd:
            out = "app/routes/internal.py\n"
        elif cmd[:3] == ["git", "diff", "--cached"]:
            out = _diff("app/routes/internal.py", f'TOKEN = "{value}"')
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    with pytest.raises(SafetyError) as err:
        Git(tmp_path, tmp_path, "develop", run=run).commit_all(
            "msg", _secrets().secret_items()
        )
    assert (
        str(err.value)
        == "Möglicher Schlüssel im Commit: SYNC_TOKEN in app/routes/internal.py"
    )
    assert value not in str(err.value)
    assert ["git", "reset", "-q"] in calls
    assert not any(c[:2] == ["git", "commit"] for c in calls)


def test_commit_with_harmless_values_goes_through(tmp_path):
    calls = []

    def run(cmd, cwd, **kw):
        calls.append(cmd)
        if "--name-only" in cmd:
            out = "app/config.py\n"
        elif cmd[:3] == ["git", "diff", "--cached"]:
            out = _diff(
                "app/config.py",
                f'FLASK_ENV = "{HARMLESS["FLASK_ENV"]}"',
                f'DATABASE_PATH = "{HARMLESS["DATABASE_PATH"]}"',
            )
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert Git(tmp_path, tmp_path, "develop", run=run).commit_all(
        "msg", _secrets().secret_items()
    )
    assert any(c[:2] == ["git", "commit"] for c in calls)


def test_from_file_reads_claude_env_but_never_passes_it_to_apps(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SYNC_TOKEN=sync-token-0123456789\nFLASK_ENV=development\n")
    tok = tmp_path / "claude.env"
    tok.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-ZYXWVUTSRQPONMLK\n")
    s = Secrets.from_file(env, tok)
    assert {n for n, _ in s.secret_items()} == {
        "SYNC_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN (claude.env)",
    }
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in s.app_env()
    assert "ZYXW" not in repr(s)
    missing = Secrets.from_file(
        env, tmp_path / "gibt-es-nicht.env"
    )  # fehlende Datei: kein Fehler
    assert [n for n, _ in missing.secret_items()] == ["SYNC_TOKEN"]


def test_redaction_hides_secrets_but_keeps_harmless_values():
    s = _secrets()
    text = (
        f"Pfad {HARMLESS['DATABASE_PATH']} Modus development, "
        f"Token {TOKEN_FILE['CLAUDE_CODE_OAUTH_TOKEN']} PW {SECRET['SMTP_PASSWORD']}"
    )
    out = s.redact(text)
    assert HARMLESS["DATABASE_PATH"] in out and "development" in out
    assert "ZYXW" not in out and SECRET["SMTP_PASSWORD"] not in out
    assert "***SMTP_PASSWORD***" in out


def test_engine_passes_only_secret_items_to_commit(make_engine):
    eng = make_engine()
    eng.secrets = _secrets()
    eng.run(forever=False)
    names = {n for n, _ in eng.git.secret_items}
    assert names == set(SECRET) | {"CLAUDE_CODE_OAUTH_TOKEN (claude.env)"}
