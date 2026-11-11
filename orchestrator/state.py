"""Zustand des Steuerprogramms in state.json – atomar geschrieben, mit Dateisperre."""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# Status-Werte (Deutsch, weil sie auf Statusseite und in Mails erscheinen)
BEREIT = "bereit"
LAEUFT = "läuft"
PAUSIERT = "pausiert"
WARTET_LIMIT = "wartet auf Nutzungslimit"
WARTET_STOERUNG = "wartet nach Störung"
FERTIG = "fertig"

MAX_MESSAGES = 50


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_state(first_phase: str, phase_ids: list[str]) -> dict:
    return {
        "version": 1,
        "status": BEREIT,
        "phase": first_phase,
        "step": "branch",
        "counters": {"review": 0, "repair": 0, "transient": 0},
        "pause": None,  # {"reason", "question", "resume_step", "since"}
        "wait_until": None,
        "phases": {pid: {"status": "offen"} for pid in phase_ids},
        "digest": [],
        "outbox": [],
        "messages": [],
        "auth_expires_at": None,
        "auth_warned_for": None,
        "updated_at": now_iso(),
    }


def add_message(state: dict, text: str) -> None:
    state["messages"].append({"ts": now_iso(), "text": text})
    del state["messages"][:-MAX_MESSAGES]


class StateStore:
    """Liest und schreibt state.json. Jede Änderung läuft unter einer exklusiven Sperre."""

    def __init__(self, path: Path, first_phase: str, phase_ids: list[str]):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.first_phase = first_phase
        self.phase_ids = phase_ids

    @contextlib.contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> dict:
        if not self.path.exists():
            return new_state(self.first_phase, self.phase_ids)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        # Neue Phasen aus phases.yaml ergänzen, ohne Bestehendes anzutasten
        for pid in self.phase_ids:
            state["phases"].setdefault(pid, {"status": "offen"})
        return state

    def _write(self, state: dict) -> None:
        state["updated_at"] = now_iso()
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=".state-", suffix=".json"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def load(self) -> dict:
        with self._locked():
            return self._read()

    def save(self, state: dict) -> None:
        with self._locked():
            self._write(state)

    @contextlib.contextmanager
    def update(self):
        """Lesen-Ändern-Schreiben unter einer Sperre (für CLI-Befehle wie --resume)."""
        with self._locked():
            state = self._read()
            yield state
            self._write(state)


class MemoryStore(StateStore):
    """Zustand nur im Speicher (für --dry-run und Tests)."""

    def __init__(
        self, first_phase: str, phase_ids: list[str], state: dict | None = None
    ):
        self.first_phase = first_phase
        self.phase_ids = phase_ids
        self.state = (
            copy.deepcopy(state) if state else new_state(first_phase, phase_ids)
        )

    def load(self) -> dict:
        return copy.deepcopy(self.state)

    def save(self, state: dict) -> None:
        self.state = copy.deepcopy(state)

    @contextlib.contextmanager
    def update(self):
        state = self.load()
        yield state
        self.save(state)
