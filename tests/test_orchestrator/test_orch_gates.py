"""Tests der Gate-Logik: Hit@10-Schwelle, Berichtssuche, Reihenfolge der Gates."""

import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.gates import (
    Gates,
    compare_hit10,
    extract_metric,
    extract_metrics,
    newest_report,
)


@pytest.mark.parametrize(
    ("new", "old", "ok"),
    [
        (0.50, 0.50, True),
        (0.48, 0.50, True),  # genau 0,02 weniger: erlaubt
        (0.4799, 0.50, False),  # mehr als 0,02 weniger: rot
        (0.60, 0.50, True),
        (0.30, None, True),  # erster Bericht (P1): kein Vergleich
        (None, 0.50, False),  # Kennzahl fehlt: rot
    ],
)
def test_compare_hit10_threshold(new, old, ok):
    assert compare_hit10(new, old, 0.02)[0] is ok


def test_extract_metric_prefers_contract_block():
    report = {"hit@10": 0.1, "metrics": {"hit@10": 0.7, "mrr": 0.4}}
    assert extract_metric(report, "hit@10") == 0.7


@pytest.mark.parametrize("key", ["hit@10", "hit_at_10", "Hit10", "HIT@10"])
def test_extract_metric_tolerates_spelling(key):
    assert (
        extract_metric({"ergebnis": {"mit_erweiterung": {key: 0.66}}}, "hit@10") == 0.66
    )


def test_extract_metric_ignores_hauptgruppen_and_bools():
    report = {"hauptgruppen_hit@5": 0.9, "hit@5": True}
    assert extract_metric(report, "hit@5") is None
    assert extract_metrics({"metrics": {"hit@1": 0.1}}) == {"hit@1": 0.1}


def test_newest_report_by_name(tmp_path):
    for name in [
        "eval-2026-11-12-aaaa.json",
        "eval-2026-11-20-bbbb.json",
        "eval-2026-11-15-cccc.json",
    ]:
        (tmp_path / name).write_text("{}")
    assert (
        newest_report(list(tmp_path.glob("*.json"))).name == "eval-2026-11-20-bbbb.json"
    )


class ScriptedRun:
    """Ersetzt subprocess.run: Exit-Code je Gate, Evaluation schreibt einen Bericht."""

    def __init__(self, codes=None, write_report=None):
        self.codes = codes or {}
        self.write_report = write_report
        self.calls = []

    def __call__(self, cmd, cwd, env, **kw):
        name = "ruff" if "ruff" in cmd[0] else ("pytest" if "pytest" in cmd else "eval")
        self.calls.append(name)
        if name == "eval" and self.write_report:
            self.write_report(Path(cwd))
        return subprocess.CompletedProcess(cmd, self.codes.get(name, 0), "ausgabe", "")


def _gates(run):
    return Gates(
        Path("/venv/bin"),
        "python scripts/evaluate.py --out reports/",
        0.02,
        {},
        run=run,
    )


def _writer(hit10):
    def write(cwd):
        (cwd / "reports").mkdir(exist_ok=True)
        (cwd / "reports" / "eval-2026-11-30-neu.json").write_text(
            json.dumps(
                {
                    "metrics": {"hit@10": hit10, "mrr": 0.3},
                    "timestamp": "2026-11-30T10:00",
                }
            )
        )

    return write


def test_red_ruff_stops_before_pytest(tmp_path):
    run = ScriptedRun(codes={"ruff": 1})
    rep = _gates(run).run(tmp_path, evaluation=True, baseline=None)
    assert not rep.ok and run.calls == ["ruff"]
    assert "ruff" in rep.failure_text()


def test_without_evaluation_only_ruff_and_pytest(tmp_path):
    run = ScriptedRun()
    rep = _gates(run).run(tmp_path, evaluation=False, baseline=None)
    assert rep.ok and run.calls == ["ruff", "pytest"]


def test_evaluation_without_new_report_is_red(tmp_path):
    rep = _gates(ScriptedRun()).run(tmp_path, evaluation=True, baseline=None)
    assert not rep.ok
    assert "keinen neuen" in rep.results[-1].summary


def test_evaluation_compares_with_baseline(tmp_path):
    ok = _gates(ScriptedRun(write_report=_writer(0.48))).run(
        tmp_path, True, {"metrics": {"hit@10": 0.50}}
    )
    assert ok.ok and ok.metrics["hit@10"] == 0.48
    (tmp_path / "reports" / "eval-2026-11-30-neu.json").unlink()
    bad = _gates(ScriptedRun(write_report=_writer(0.47))).run(
        tmp_path, True, {"metrics": {"hit@10": 0.50}}
    )
    assert not bad.ok and "sinkt" in bad.results[-1].summary


def test_evaluation_falls_back_to_last_committed_report(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "eval-2026-11-01-alt.json").write_text(
        json.dumps({"metrics": {"hit@10": 0.80}})
    )
    rep = _gates(ScriptedRun(write_report=_writer(0.70))).run(
        tmp_path, True, baseline=None
    )
    assert not rep.ok  # 0,80 → 0,70 ist mehr als 0,02 Verlust


def test_secrets_are_redacted_from_gate_output(tmp_path):
    def run(cmd, cwd, env, **kw):
        return subprocess.CompletedProcess(cmd, 1, "key=sk-ant-geheim-123456", "")

    g = Gates(
        Path("/v/bin"),
        "python x.py",
        0.02,
        {},
        redact=lambda s: s.replace("sk-ant-geheim-123456", "***"),
        run=run,
    )
    rep = g.run(tmp_path, evaluation=False, baseline=None)
    assert "sk-ant" not in rep.failure_text()
