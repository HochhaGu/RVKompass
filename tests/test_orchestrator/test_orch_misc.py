"""Tests für Konfiguration, Git-Schutz, Mails, Statusseite und Zustandsdatei."""

import subprocess
from typing import ClassVar

import pytest

from orchestrator.config import REPO_ROOT, extract_phase_prompt, load_settings
from orchestrator.envfile import Secrets
from orchestrator.git_ops import (
    Git,
    SafetyError,
    assert_safe_target,
    forbidden_paths,
)
from orchestrator.notify import Mail, Mailer, html_to_text, render_mail, security_mode
from orchestrator.state import StateStore
from orchestrator.status_page import render_status

# ---------- Konfiguration ----------


def test_phases_yaml_matches_plan():
    s = load_settings()
    assert [p.id for p in s.phases] == [f"P{i}" for i in range(9)]
    assert [p.id for p in s.phases if p.halt_message] == ["P0", "P8"]
    assert s.phase("P0").branch == "phase-0-audit" and not s.phase("P0").evaluation
    assert all(p.evaluation for p in s.phases[1:])
    assert s.hit10_tolerance == 0.02 and s.base_branch == "develop"


def test_every_phase_prompt_is_extracted():
    md = (REPO_ROOT / "prompts" / "PROMPTS.md").read_text(encoding="utf-8")
    p0 = extract_phase_prompt(md, "P0")
    assert p0.startswith("P0 – Onboarding und Audit") and "audit-2026-11.md" in p0
    assert "P1 –" not in p0
    for i in range(9):
        assert len(extract_phase_prompt(md, f"P{i}")) > 200
    assert "Container" in extract_phase_prompt(md, "P8")
    with pytest.raises(KeyError):
        extract_phase_prompt(md, "P42")


# ---------- Git-Schutz ----------


def test_main_is_never_a_target():
    for branch, base in [
        ("main", "develop"),
        ("phase-0-audit", "main"),
        ("master", "develop"),
    ]:
        with pytest.raises(SafetyError):
            assert_safe_target(branch, base)
    assert_safe_target("phase-0-audit", "develop")


def test_forbidden_paths():
    paths = [
        "app/x.py",
        "data/rvk.sqlite3",
        "data",
        ".env",
        "app/.env.local",
        ".env.example",
        "orchestrator/state.json",
        "orchestrator/antworten/P0.md",
        "orchestrator/engine.py",
    ]
    assert forbidden_paths(paths) == [
        "data/rvk.sqlite3",
        "data",
        ".env",
        "app/.env.local",
        "orchestrator/state.json",
        "orchestrator/antworten/P0.md",
    ]


def test_git_refuses_pr_with_wrong_base(tmp_path):
    def run(cmd, cwd, **kw):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, '{"url":"u","state":"OPEN","baseRefName":"main","number":1}', ""
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    git = Git(tmp_path, tmp_path, "develop", run=run)
    with pytest.raises(SafetyError):
        git.merge_pr("phase-0-audit")
    with pytest.raises(SafetyError):
        git.push("main")


def test_git_commit_blocks_data_files(tmp_path):
    calls = []

    def run(cmd, cwd, **kw):
        calls.append(cmd)
        out = "data/rvk.sqlite3\napp/x.py\n" if "--name-only" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    with pytest.raises(SafetyError):
        Git(tmp_path, tmp_path, "develop", run=run).commit_all("msg", [])
    assert ["git", "reset", "-q"] in calls
    assert not any(c[:2] == ["git", "commit"] for c in calls)


# ---------- Mails ----------


def test_render_mail_escapes_and_has_text_alternative():
    m = render_mail(
        "Titel",
        "Hallo <b>",
        [{"heading": "H", "rows": [("Phase", "P0")], "pre": "NEEDS <x>"}],
        "https://s/",
    )
    assert m.subject == "[RVKompass] Titel"
    assert "Hallo &lt;b&gt;" in m.html and "<table" in m.html
    assert "Phase" in m.text and "<" not in html_to_text("<p>a</p>")


@pytest.mark.parametrize(
    ("sec", "port", "mode"),
    [
        ("ssl", 465, "ssl"),
        ("starttls", 587, "starttls"),
        ("tls", 465, "ssl"),
        ("tls", 587, "starttls"),
        ("", 587, "starttls"),
        ("none", 25, "none"),
    ],
)
def test_security_mode(sec, port, mode):
    assert security_mode(sec, port) == mode


class FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port, timeout=30, context=None):
        self.host, self.port, self.context = host, port, context
        self.actions = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.actions.append("starttls")

    def login(self, user, pw):
        self.actions.append("login")

    def send_message(self, msg):
        self.actions.append("send")
        self.msg = msg


MAIL_ENV = {
    "SMTP_HOST": "smtp.example.invalid",
    "SMTP_PORT": "587",
    "SMTP_SECURITY": "starttls",
    "SMTP_USER": "u",
    "SMTP_PASSWORD": "geheim-passwort",
    "MAIL_FROM": "a@example.invalid",
    "MAIL_TO": "b@example.invalid",
}


def test_mailer_sends_html_in_body_with_starttls():
    FakeSMTP.instances.clear()
    Mailer(Secrets(MAIL_ENV), smtp_factory=FakeSMTP).send(
        Mail("S", "<p>Hallo</p>", "Hallo")
    )
    smtp = FakeSMTP.instances[0]
    assert smtp.actions == ["starttls", "login", "send"]
    html_part = smtp.msg.get_body(preferencelist=("html",))
    assert "<p>Hallo</p>" in html_part.get_content()
    assert not any(p.get_filename() for p in smtp.msg.iter_attachments())  # kein Anhang


def test_mailer_reports_missing_keys():
    with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
        Mailer(
            Secrets({k: v for k, v in MAIL_ENV.items() if k != "SMTP_PASSWORD"})
        ).send(Mail("S", "<p/>", ""))


def test_secrets_redact_and_app_env():
    s = Secrets(
        {
            **MAIL_ENV,
            "CLAUDE_API_KEY": "sk-ant-123456789",
            "STATUS_PASSWORD": "pw123456",
        }
    )
    assert "geheim-passwort" not in s.redact("login mit geheim-passwort fehlgeschlagen")
    assert set(s.app_env()) == {"CLAUDE_API_KEY"}
    assert "sk-ant" not in repr(s)


# ---------- Statusseite und Zustand ----------


def test_status_page_renders_phases_without_percent(tmp_path):
    s = load_settings()
    store = StateStore(tmp_path / "state.json", "P0", [p.id for p in s.phases])
    with store.update() as st:
        st["phases"]["P1"].update(
            status="fertig", metrics={"hit@10": 0.612, "mrr": 0.4}
        )
        st["pause"] = {
            "reason": "Rückfrage <x>",
            "question": "Darf ich?",
            "since": None,
        }
    page = render_status(store.load(), s)
    assert "P8" in page and "0.612" in page and "Rückfrage &lt;x&gt;" in page
    assert "%" not in page.split("<body>")[1]
    assert "Konfidenz" not in page


def test_state_store_roundtrip_and_new_phases(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path, "P0", ["P0", "P1"]).save(
        StateStore(path, "P0", ["P0", "P1"]).load() | {"step": "gates"}
    )
    st = StateStore(path, "P0", ["P0", "P1", "P2"]).load()
    assert st["step"] == "gates" and st["phases"]["P2"] == {"status": "offen"}
    assert not list(tmp_path.glob(".state-*"))  # keine Reste der atomaren Schreibweise
