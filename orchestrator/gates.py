"""Qualitäts-Gates: ruff, pytest und (ab P1) Evaluation gegen den letzten Bericht."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

METRIC_NAMES = ("hit@1", "hit@5", "hit@10", "mrr")
EPS = 1e-9


@dataclass
class GateResult:
    name: str
    ok: bool
    summary: str
    output: str = ""


@dataclass
class GateReport:
    ok: bool
    results: list[GateResult] = field(default_factory=list)
    metrics: dict[str, float] | None = None
    report_path: str | None = None

    def failure_text(self, limit: int = 6000) -> str:
        parts = [
            f"### {r.name}: {r.summary}\n{r.output[-limit:]}"
            for r in self.results
            if not r.ok
        ]
        return "\n\n".join(parts)

    def summary_lines(self) -> list[str]:
        return [
            f"{'grün' if r.ok else 'ROT'} – {r.name}: {r.summary}" for r in self.results
        ]


# ---------- reine Funktionen ----------


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower()).replace("at", "")


def extract_metric(report: dict, name: str) -> float | None:
    """Sucht eine Kennzahl im Evaluationsbericht.

    Bevorzugt report["metrics"][name] (Vertrag), sonst Breitensuche nach einem Schlüssel,
    der normalisiert gleich ist (hit@10 = hit_at_10 = Hit10)."""
    target = _norm(name)
    metrics = report.get("metrics")
    queue: list[object] = [metrics] if isinstance(metrics, dict) else []
    queue.append(report)
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            for k, v in node.items():
                if (
                    _norm(str(k)) == target
                    and isinstance(v, int | float)
                    and not isinstance(v, bool)
                ):
                    return float(v)
            queue.extend(v for v in node.values() if isinstance(v, dict))
    return None


def extract_metrics(report: dict) -> dict[str, float]:
    found = {n: extract_metric(report, n) for n in METRIC_NAMES}
    return {k: v for k, v in found.items() if v is not None}


def compare_hit10(
    new: float | None, old: float | None, tolerance: float
) -> tuple[bool, str]:
    """Hit@10 darf gegenüber dem alten Bericht höchstens um `tolerance` sinken."""
    if new is None:
        return False, "Hit@10 fehlt im neuen Bericht"
    if old is None:
        return True, f"Hit@10 = {new:.3f} (erster Bericht, kein Vergleich)"
    drop = old - new
    if drop > tolerance + EPS:
        return (
            False,
            f"Hit@10 sinkt von {old:.3f} auf {new:.3f} (−{drop:.3f} > {tolerance:.2f})",
        )
    return True, f"Hit@10 {old:.3f} → {new:.3f} (Änderung {new - old:+.3f})"


def newest_report(paths: list[Path]) -> Path | None:
    """Neuester Bericht nach Dateiname (eval-<datum>-<hash>.json), bei Gleichstand nach Zeitstempel."""
    if not paths:
        return None

    def key(p: Path):
        stamp = ""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k in (
                "timestamp",
                "created_at",
                "erstellt",
                "zeitpunkt",
                "datum",
                "date",
            ):
                if isinstance(data.get(k), str):
                    stamp = data[k]
                    break
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        m = re.search(r"(\d{4}-\d{2}-\d{2}(?:[T_-]?\d{2}-?\d{2}(?:-?\d{2})?)?)", p.name)
        return (m.group(1) if m else "", stamp, p.stat().st_mtime)

    return max(paths, key=key)


def list_reports(reports_dir: Path) -> list[Path]:
    return sorted(reports_dir.glob("eval-*.json")) if reports_dir.is_dir() else []


# ---------- Ausführung ----------

Runner = Callable[..., subprocess.CompletedProcess]


class Gates:
    def __init__(
        self,
        venv_bin: Path,
        eval_command: str,
        tolerance: float,
        app_env: dict[str, str],
        redact=lambda s: s,
        run: Runner = subprocess.run,
    ):
        self.venv_bin = venv_bin
        self.eval_command = eval_command
        self.tolerance = tolerance
        self.app_env = app_env
        self.redact = redact
        self._run = run

    def env(self) -> dict[str, str]:
        import os

        env = {k: v for k, v in os.environ.items()}
        env.update(self.app_env)
        env["PATH"] = f"{self.venv_bin}:{env.get('PATH', '/usr/bin:/bin')}"
        env["VIRTUAL_ENV"] = str(self.venv_bin.parent)
        return env

    def _exec(self, name: str, cmd: list[str], cwd: Path, timeout: int) -> GateResult:
        try:
            proc = self._run(
                cmd,
                cwd=cwd,
                env=self.env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return GateResult(name, False, f"Zeitlimit {timeout} s überschritten")
        out = self.redact(f"{proc.stdout}\n{proc.stderr}".strip())
        last = out.splitlines()[-1] if out else ""
        if proc.returncode == 0:
            return GateResult(name, True, last[:200] or "ok", out)
        return GateResult(
            name, False, f"Exit-Code {proc.returncode}: {last[:200]}", out
        )

    def commands(self, evaluation: bool) -> list[tuple[str, list[str]]]:
        cmds = [
            ("ruff check", [str(self.venv_bin / "ruff"), "check", "."]),
            ("pytest", [str(self.venv_bin / "python"), "-m", "pytest", "-q"]),
        ]
        if evaluation:
            parts = shlex.split(self.eval_command)
            if parts and parts[0] == "python":
                parts[0] = str(self.venv_bin / "python")
            cmds.append(("evaluation", parts))
        return cmds

    def run(
        self, worktree: Path, evaluation: bool, baseline: dict | None
    ) -> GateReport:
        """baseline: {"metrics": {...}, "report": "..."} vom letzten bestandenen Lauf oder None."""
        report = GateReport(ok=True)
        timeouts = {"ruff check": 300, "pytest": 3600, "evaluation": 7200}
        reports_dir = worktree / "reports"
        before = set(list_reports(reports_dir))
        for name, cmd in self.commands(evaluation):
            res = self._exec(name, cmd, worktree, timeouts[name])
            report.results.append(res)
            if not res.ok:
                report.ok = False
                return (
                    report  # spätere Gates nicht laufen lassen, Fehler zuerst beheben
                )
        if not evaluation:
            return report

        new_files = [p for p in list_reports(reports_dir) if p not in before]
        new_path = newest_report(new_files)
        if new_path is None:
            report.results.append(
                GateResult(
                    "Bericht",
                    False,
                    "evaluate.py hat keinen neuen reports/eval-*.json erzeugt",
                )
            )
            report.ok = False
            return report
        try:
            new_data = json.loads(new_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.results.append(
                GateResult("Bericht", False, f"{new_path.name} unlesbar: {exc}")
            )
            report.ok = False
            return report
        report.metrics = extract_metrics(new_data)
        report.report_path = str(new_path.relative_to(worktree))

        if (baseline or {}).get("neue_basis"):
            new_hit = report.metrics.get("hit@10")
            ok = new_hit is not None
            msg = (
                f"Hit@10 = {new_hit:.3f} – neue Basismessung (Goldstandard geändert), "
                "kein Vergleich mit dem alten Stand"
                if ok
                else "Hit@10 fehlt im neuen Bericht"
            )
            report.results.append(GateResult("Hit@10-Vergleich", ok, msg))
            report.ok = ok
            return report

        old_metrics = (baseline or {}).get("metrics")
        if (
            old_metrics is None
        ):  # kein gemerkter Stand: letzten vorhandenen Bericht nehmen
            old_path = newest_report(list(before))
            if old_path is not None:
                try:
                    old_metrics = extract_metrics(
                        json.loads(old_path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError):
                    old_metrics = None
        ok, msg = compare_hit10(
            report.metrics.get("hit@10"),
            (old_metrics or {}).get("hit@10"),
            self.tolerance,
        )
        report.results.append(GateResult("Hit@10-Vergleich", ok, msg))
        report.ok = ok
        return report
