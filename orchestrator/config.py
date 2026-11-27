"""Laden von phases.yaml und Ausschneiden der Phasen-Aufträge aus prompts/PROMPTS.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ORCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent


@dataclass(frozen=True)
class Phase:
    id: str
    keyword: str
    title: str
    evaluation: bool
    halt_message: str | None = None
    web_domains: tuple[str, ...] = ()
    extra_bash: tuple[str, ...] = ()
    # Goldstandard geändert: Evaluation läuft, aber ohne Hit@10-Vergleich mit dem alten Stand;
    # das Ergebnis wird der neue Vergleichsstand für spätere Phasen.
    new_baseline: bool = False

    @property
    def number(self) -> int:
        return int(self.id[1:])

    @property
    def branch(self) -> str:
        return f"phase-{self.number}-{self.keyword}"


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    base_branch: str
    worktree: Path
    status_dir: Path
    status_url: str
    model: str
    review_model: str
    report_model: str
    hit10_tolerance: float
    limit_wait_s: int
    transient_wait_s: int
    transient_max_attempts: int
    idle_poll_s: int
    claude_timeout_s: int
    auth_warn_days: int
    eval_command: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]
    phases: tuple[Phase, ...] = field(default_factory=tuple)

    # Laufzeitpfade (alle unter orchestrator/, nicht versioniert)
    @property
    def orch_dir(self) -> Path:
        return self.repo_root / "orchestrator"

    @property
    def state_path(self) -> Path:
        return self.orch_dir / "state.json"

    @property
    def pause_path(self) -> Path:
        return self.orch_dir / "PAUSE"

    @property
    def answers_dir(self) -> Path:
        return self.orch_dir / "antworten"

    @property
    def runs_dir(self) -> Path:
        return self.orch_dir / "runs"

    @property
    def logs_dir(self) -> Path:
        return self.orch_dir / "logs"

    @property
    def claude_env_path(self) -> Path:
        """Langlebige Claude-Anmeldung (claude setup-token), außerhalb des Repos."""
        return Path.home() / ".config" / "rvkompass" / "claude.env"

    @property
    def outbox_dir(self) -> Path:
        return self.orch_dir / "logs" / "mail_warteschlange"

    @property
    def run_lock_path(self) -> Path:
        return self.orch_dir / "logs" / "run.lock"

    @property
    def fallback_status_dir(self) -> Path:
        return self.orch_dir / "status_out"

    @property
    def venv_bin(self) -> Path:
        return self.repo_root / ".venv" / "bin"

    def phase(self, phase_id: str) -> Phase:
        for p in self.phases:
            if p.id == phase_id:
                return p
        raise KeyError(f"Unbekannte Phase: {phase_id}")

    def next_phase(self, phase_id: str) -> Phase | None:
        ids = [p.id for p in self.phases]
        i = ids.index(phase_id)
        return self.phases[i + 1] if i + 1 < len(ids) else None


def load_settings(path: Path | None = None, repo_root: Path | None = None) -> Settings:
    path = path or ORCH_DIR / "phases.yaml"
    repo_root = repo_root or REPO_ROOT
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    e = data["einstellungen"]
    phases = tuple(
        Phase(
            id=p["id"],
            keyword=p["stichwort"],
            title=p["titel"],
            evaluation=bool(p.get("evaluation", False)),
            halt_message=(p.get("haltepunkt") or None),
            web_domains=tuple(p.get("web", []) or []),
            extra_bash=tuple(p.get("bash", []) or []),
            new_baseline=bool(p.get("neue_basis", False)),
        )
        for p in data["phasen"]
    )
    return Settings(
        repo_root=repo_root,
        base_branch=e["basis_branch"],
        worktree=Path(e["arbeitskopie"]).expanduser(),
        status_dir=Path(e["statusordner"]).expanduser(),
        status_url=e["statusseite_url"],
        model=e["modell"],
        review_model=e["pruef_modell"],
        report_model=e["bericht_modell"],
        hit10_tolerance=float(e["hit10_toleranz"]),
        limit_wait_s=int(e["limit_wartezeit_s"]),
        transient_wait_s=int(e["stoerung_wartezeit_s"]),
        transient_max_attempts=int(e["stoerung_max_versuche"]),
        idle_poll_s=int(e["leerlauf_s"]),
        claude_timeout_s=int(e["zeitlimit_claude_s"]),
        auth_warn_days=int(e["anmeldung_warnung_tage"]),
        eval_command=e["evaluation_befehl"],
        allow=tuple(data["erlaubnis"]["erlaubt"]),
        deny=tuple(data["erlaubnis"]["verboten"]),
        phases=phases,
    )


_HEADING = re.compile(r"^## (P\d+)\b.*$", re.MULTILINE)


def extract_phase_prompt(prompts_md: str, phase_id: str) -> str:
    """Schneidet den Auftrag einer Phase aus PROMPTS.md (Inhalt des Codeblocks unter '## P<N> –')."""
    matches = list(_HEADING.finditer(prompts_md))
    for i, m in enumerate(matches):
        if m.group(1) != phase_id:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prompts_md)
        section = prompts_md[m.end() : end]
        # Abschnitt endet ggf. am nächsten '## '-Abschnitt ohne P-Nummer oder an '---'
        section = re.split(
            r"^(?:## |---\s*$)", section, maxsplit=1, flags=re.MULTILINE
        )[0]
        block = re.search(r"```[a-z]*\n(.*?)```", section, re.DOTALL)
        text = block.group(1) if block else section
        return f"{m.group(0).lstrip('# ').strip()}\n\n{text.strip()}\n"
    raise KeyError(f"Phase {phase_id} nicht in PROMPTS.md gefunden")
