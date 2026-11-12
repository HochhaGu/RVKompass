"""Gemeinsame Test-Stellvertreter für das Steuerprogramm (kein Netz, kein echtes claude)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.claude_runner import OK, ClaudeResult
from orchestrator.config import load_settings
from orchestrator.engine import Engine
from orchestrator.envfile import Secrets
from orchestrator.gates import GateReport, GateResult
from orchestrator.state import MemoryStore

PLAN_TEXT = "# Plan\n\n## Übersicht\n" + ("Schritt mit ausreichend Inhalt. " * 20)


class FakeRunner:
    """Antworten je Schritt (Präfix des Log-Namens: plan, review, implement, …)."""

    def __init__(self, script: dict[str, list[ClaudeResult]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, object]] = []

    def run(self, inv) -> ClaudeResult:
        step = inv.log_stem.name.rsplit("-", 2)[0]
        self.calls.append((step, inv))
        queue = self.script.get(step)
        if queue:
            return queue.pop(0)
        if step == "review":
            return ClaudeResult(
                kind=OK, text="{}", structured={"freigabe": True, "gruende": []}
            )
        if step in ("plan", "revise"):
            return ClaudeResult(kind=OK, text=PLAN_TEXT)
        return ClaudeResult(kind=OK, text=f"{step} erledigt")

    def steps(self) -> list[str]:
        return [s for s, _ in self.calls]


def ok_report(hit10: float = 0.5) -> GateReport:
    return GateReport(
        ok=True,
        results=[GateResult("pytest", True, "15 passed")],
        metrics={"hit@1": 0.2, "hit@5": 0.4, "hit@10": hit10, "mrr": 0.3},
        report_path="reports/eval-x.json",
    )


def red_report() -> GateReport:
    return GateReport(
        ok=False, results=[GateResult("pytest", False, "1 failed", "AssertionError")]
    )


class FakeGates:
    def __init__(self, reports: list[GateReport] | None = None):
        self.reports = list(reports or [])
        self.calls: list[dict | None] = []

    def run(self, worktree, evaluation, baseline):
        self.calls.append(baseline)
        return self.reports.pop(0) if self.reports else ok_report()


class FakeGit:
    def __init__(self, raise_on: dict[str, Exception] | None = None):
        self.calls: list[tuple] = []
        self.raise_on = raise_on or {}

    def _rec(self, name, *args):
        self.calls.append((name, *args))
        if name in self.raise_on:
            raise self.raise_on.pop(name)

    def prepare_worktree(self):
        self._rec("prepare_worktree")

    def start_branch(self, branch):
        self._rec("start_branch", branch)

    def diffstat(self):
        return " app/x.py | 2 +-"

    def commit_all(self, message, secret_items):
        self.secret_items = secret_items
        self._rec("commit_all", message)
        return True

    def commits_ahead(self):
        return 1

    def push(self, branch):
        self._rec("push", branch)

    def open_or_get_pr(self, branch, title, body):
        self._rec("open_pr", branch)
        return {"url": f"https://example.invalid/{branch}", "state": "OPEN"}

    def merge_pr(self, branch):
        self._rec("merge_pr", branch)
        return {"url": f"https://example.invalid/{branch}", "state": "MERGED"}

    def names(self):
        return [c[0] for c in self.calls]


class FakeMailer:
    def __init__(self, fail: bool = False, exc: Exception | None = None):
        self.sent = []
        self.fail = fail
        self.exc = exc or RuntimeError("SMTP-Fehler: timed out")
        self.attempts = 0

    def send(self, mail):
        self.attempts += 1
        if self.fail:
            raise self.exc
        self.sent.append(mail)

    def subjects(self):
        return [m.subject for m in self.sent]


class FakeStatus:
    def __init__(self):
        self.writes = 0

    def write(self, state):
        self.writes += 1


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += seconds
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def settings(tmp_path):
    s = load_settings()
    repo = tmp_path / "repo"
    (repo / "orchestrator").mkdir(parents=True)
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / "CLAUDE.md").write_text("# CLAUDE.md (Test)\n", encoding="utf-8")
    return dataclasses.replace(
        s, repo_root=repo, worktree=wt, status_dir=tmp_path / "status", idle_poll_s=60
    )


@pytest.fixture
def prompts_md():
    from orchestrator.config import REPO_ROOT

    return (REPO_ROOT / "prompts" / "PROMPTS.md").read_text(encoding="utf-8")


@pytest.fixture
def make_engine(settings, prompts_md):
    def factory(runner=None, gates=None, git=None, mailer=None, store=None, clock=None):
        ids = [p.id for p in settings.phases]
        clock = clock or Clock()
        logged: list[str] = []
        eng = Engine(
            settings,
            store or MemoryStore(ids[0], ids),
            runner or FakeRunner(),
            gates or FakeGates(),
            git or FakeGit(),
            mailer or FakeMailer(),
            FakeStatus(),
            Secrets({"CLAUDE_API_KEY": "sk-ant-geheim-123456"}),
            prompts_md=prompts_md,
            sleep=clock.sleep,
            now=clock.now,
            log=logged.append,
        )
        eng.logged = logged
        eng.clock = clock
        return eng

    return factory
