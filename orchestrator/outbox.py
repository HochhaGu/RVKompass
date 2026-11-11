"""Mail-Warteschlange: eine Datei je nicht zugestellter Mail.

Liegt bewusst NICHT in state.json: So können auch Wochenbrief und andere Befehle Mails ablegen,
ohne mit dem laufenden Dienst um state.json zu konkurrieren. Fehler hier dürfen den Ablauf nie
anhalten – jede Methode fängt Dateifehler selbst ab.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .notify import Mail

RETRY_S = 600  # frühestens alle 10 Minuten erneut versuchen


def _now() -> datetime:
    return datetime.now(UTC)


class Outbox:
    def __init__(self, directory: Path, log=print):
        self.dir = directory
        self.log = log

    def _write(self, path: Path, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".neu-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)

    def add(self, mail: Mail, error: str, now: datetime | None = None) -> None:
        now = now or _now()
        data = {
            **mail.as_dict(),
            "queued_at": now.isoformat(timespec="seconds"),
            "last_try": now.isoformat(timespec="seconds"),
            "attempts": 1,
            "last_error": error[:500],
        }
        path = self.dir / f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.json"
        try:
            self._write(path, data)
        except OSError as exc:  # selbst das darf den Ablauf nicht anhalten
            self.log(
                f"Mail-Warteschlange nicht beschreibbar ({exc}) – Mail verworfen: {mail.subject}"
            )

    def items(self) -> list[tuple[Path, dict]]:
        result = []
        try:
            paths = sorted(self.dir.glob("*.json"))
        except OSError:
            return []
        for path in paths:
            try:
                result.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError) as exc:
                self.log(
                    f"Unlesbarer Eintrag in der Mail-Warteschlange {path.name}: {exc}"
                )
        return result

    def due(self, now: datetime | None = None) -> list[tuple[Path, dict]]:
        now = now or _now()
        out = []
        for path, data in self.items():
            try:
                last = datetime.fromisoformat(data.get("last_try", ""))
            except ValueError:
                last = None
            if last is None or (now - last).total_seconds() >= RETRY_S:
                out.append((path, data))
        return out

    def mark_failed(
        self, path: Path, data: dict, error: str, now: datetime | None = None
    ) -> None:
        now = now or _now()
        data = {
            **data,
            "attempts": int(data.get("attempts", 0)) + 1,
            "last_try": now.isoformat(timespec="seconds"),
            "last_error": error[:500],
        }
        try:
            self._write(path, data)
        except OSError as exc:
            self.log(f"Mail-Warteschlange nicht beschreibbar: {exc}")

    def remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.log(f"Eintrag {path.name} nicht entfernbar: {exc}")

    def summary(self) -> list[dict]:
        """Für Statusseite und --status: ohne Mailinhalt."""
        return [
            {
                "subject": d.get("subject", "?"),
                "queued_at": d.get("queued_at"),
                "last_try": d.get("last_try"),
                "attempts": d.get("attempts", 0),
                "last_error": d.get("last_error", ""),
            }
            for _, d in self.items()
        ]


def mail_from(data: dict) -> Mail:
    return Mail(subject=data["subject"], html=data["html"], text=data["text"])
