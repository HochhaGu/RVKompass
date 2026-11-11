"""Probelauf sendet nichts; Arbeitsverzeichnis und PATH aller Läufe; Berichtsmodell."""

import shutil
import smtplib
import subprocess
from pathlib import Path

import pytest

from orchestrator import claude_runner as cr
from orchestrator.claude_runner import ClaudeRunner, Invocation
from orchestrator.config import REPO_ROOT, load_settings
from orchestrator.gates import Gates


def test_defaults_point_to_worktree_and_project_venv():
    s = load_settings()
    assert s.worktree == Path.home() / "dev" / "rvkompass-phasen"
    assert s.venv_bin == REPO_ROOT / ".venv" / "bin"
    assert s.report_model == "sonnet" and s.model == "opus" and s.review_model == "opus"


def test_dry_run_never_sends_mail(settings, monkeypatch):
    from orchestrator import cli

    def forbidden(*a, **kw):
        raise AssertionError("Im Probelauf darf keine SMTP-Verbindung entstehen")

    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)
    (settings.repo_root / "prompts").mkdir()
    shutil.copy(REPO_ROOT / "prompts" / "PROMPTS.md", settings.repo_root / "prompts")
    eng = cli.build_engine(settings, dry_run=True)
    sent = []
    eng.mailer.send = lambda mail: sent.append(
        mail
    )  # auch der Probe-Mailer wird nicht gerufen
    out = []
    eng.log = out.append
    eng.run(only="P0", forever=False)
    texts = [m["text"] for m in eng.store.load()["messages"]] + out
    assert sent == []
    assert any(
        t.startswith("würde senden: [RVKompass] Haltepunkt nach P0") for t in texts
    )
    assert not any("Mail gesendet" in t for t in texts)
    assert not settings.state_path.exists()  # nichts gespeichert
    assert not settings.outbox_dir.exists()  # nichts in die Warteschlange


def test_all_claude_runs_use_worktree_and_report_uses_sonnet(make_engine):
    eng = make_engine()
    eng.run(forever=False)
    assert {inv.cwd for _, inv in eng.runner.calls} == {eng.s.worktree}
    models = {
        step: inv.args[inv.args.index("--model") + 1] for step, inv in eng.runner.calls
    }
    assert models == {
        "plan": "opus",
        "review": "opus",
        "implement": "opus",
        "report": "sonnet",
    }


def test_claude_runner_starts_in_worktree_with_venv_first(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(
            cmd, 0, '{"type":"result","result":"ok"}', ""
        )

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    venv = tmp_path / "rvkompass" / ".venv" / "bin"
    wt = tmp_path / "rvkompass-phasen"
    wt.mkdir()
    ClaudeRunner("claude", venv, 10).run(
        Invocation(
            args=["-p"], cwd=wt, prompt="x", log_stem=tmp_path / "runs" / "plan-1"
        )
    )
    assert seen["cwd"] == wt
    assert seen["env"]["PATH"].split(":")[0] == str(venv)


@pytest.mark.parametrize("evaluation", [False, True])
def test_gates_run_in_worktree_with_venv_first(tmp_path, evaluation):
    calls = []

    def fake_run(cmd, cwd, env, **kw):
        calls.append((cmd, cwd, env["PATH"].split(":")[0]))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    venv = tmp_path / "rvkompass" / ".venv" / "bin"
    Gates(venv, "python scripts/evaluate.py", 0.02, {}, run=fake_run).run(
        tmp_path, evaluation, None
    )
    assert len(calls) == (3 if evaluation else 2)
    for cmd, cwd, path0 in calls:
        assert cwd == tmp_path and path0 == str(venv)
        assert cmd[0].startswith(
            str(venv)
        )  # ruff/python direkt aus der Projektumgebung
