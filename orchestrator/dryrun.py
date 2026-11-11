"""Stellvertreter für --dry-run: zeigen, was passieren würde, ohne etwas auszuführen."""

from __future__ import annotations

from pathlib import Path

from .claude_runner import OK, ClaudeResult, Invocation
from .gates import GateReport, GateResult

FAKE_PLAN = (
    "# Plan (Probelauf)\n\n## Übersicht\n1. Schritte\n\nIm Probelauf wird kein echter Plan erstellt. "
    "Hier stünde der vollständige Plan, den Claude im Plan-Modus für die Phase schreibt. "
    "Danach prüft eine zweite Instanz ihn gegen den Phasen-Auftrag und CLAUDE.md.\n"
)


class DryRunner:
    def __init__(self, out=print):
        self.out = out

    def run(self, inv: Invocation) -> ClaudeResult:
        first = inv.prompt.strip().splitlines()[0][:90] if inv.prompt.strip() else ""
        self.out(f"    würde ausführen: {inv.shell_preview()}")
        self.out(f"    Prompt: {len(inv.prompt)} Zeichen, beginnt mit „{first}“")
        return ClaudeResult(
            kind=OK, text=FAKE_PLAN, structured={"freigabe": True, "gruende": []}
        )


class DryGates:
    def __init__(self, real_gates, out=print):
        self.real = real_gates
        self.out = out

    def run(
        self, worktree: Path, evaluation: bool, baseline: dict | None
    ) -> GateReport:
        results = []
        for name, cmd in self.real.commands(evaluation):
            self.out(f"    würde prüfen: {name}: {' '.join(cmd)}")
            results.append(GateResult(name, True, "(Probelauf, nicht ausgeführt)"))
        if evaluation:
            old = (baseline or {}).get("metrics", {}).get("hit@10")
            self.out(
                f"    würde Hit@10 vergleichen mit {old if old is not None else 'letztem Bericht'}"
                f" (Toleranz {self.real.tolerance:.2f})"
            )
        return GateReport(ok=True, results=results, metrics=None)


class DryGit:
    def __init__(self, worktree: Path, base: str, out=print):
        self.worktree = worktree
        self.base = base
        self.out = out

    def prepare_worktree(self) -> None:
        self.out(
            f"    würde Arbeitskopie sicherstellen: {self.worktree} (data/ verlinkt)"
        )

    def start_branch(self, branch: str) -> None:
        self.out(f"    würde Branch {branch} von origin/{self.base} anlegen")

    def diffstat(self) -> str:
        return "(Probelauf)"

    def commit_all(self, message: str, secret_values: list[str]) -> bool:
        self.out(
            f"    würde committen: „{message.splitlines()[0]}“ (nach Schutzprüfungen)"
        )
        return True

    def commits_ahead(self) -> int:
        return 1

    def push(self, branch: str) -> None:
        self.out(f"    würde pushen: {branch} → origin/{branch}")

    def open_or_get_pr(self, branch: str, title: str, body: str) -> dict:
        self.out(f"    würde Pull Request öffnen: {branch} → {self.base}")
        return {"url": "(Probelauf)", "state": "OPEN", "baseRefName": self.base}

    def merge_pr(self, branch: str) -> dict:
        self.out(f"    würde Pull Request mergen (Ziel {self.base}, nie main)")
        return {"url": "(Probelauf)", "state": "MERGED", "baseRefName": self.base}


class DryMailer:
    def __init__(self, out=print):
        self.out = out

    def send(self, mail) -> None:
        self.out(f"    würde Mail senden: {mail.subject}")


class DryStatus:
    def write(self, state: dict) -> None:
        return None
