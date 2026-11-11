"""Schreibt die Statusseite (statisches HTML) nach jedem Schritt neu."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .notify import jinja_env
from .outbox import RETRY_S, Outbox

TZ = ZoneInfo("Europe/Berlin")

STEP_LABELS = {
    "branch": "Branch anlegen",
    "plan": "Plan erstellen",
    "review": "Plan prüfen (zweite Instanz)",
    "revise": "Plan überarbeiten",
    "implement": "Umsetzung",
    "gates": "Gates (ruff, pytest, Evaluation)",
    "repair": "Reparaturversuch",
    "report": "Phasenbericht schreiben",
    "ship": "Commit, Push, Pull Request, Merge",
}


def local_time(iso: str | None) -> str:
    if not iso:
        return "–"
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


def _plus(iso: str | None, seconds: int) -> str | None:
    try:
        return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat()
    except (TypeError, ValueError):
        return None


def render_status(
    state: dict, settings: Settings, mail_queue: list[dict] | None = None
) -> str:
    phases = []
    for p in settings.phases:
        info = state["phases"].get(p.id, {})
        phases.append(
            {
                "id": p.id,
                "title": p.title,
                "branch": p.branch,
                "status": info.get("status", "offen"),
                "metrics": info.get("metrics") or {},
                "pr": info.get("pr_url"),
                "finished": local_time(info.get("finished_at")),
                "current": p.id == state.get("phase")
                and state.get("status") != "fertig",
            }
        )
    messages = [
        {"ts": local_time(m["ts"]), "text": m["text"]}
        for m in reversed(state.get("messages", []))
    ][:20]
    pause = state.get("pause")
    return (
        jinja_env()
        .get_template("status.html")
        .render(
            state=state,
            status=state.get("status"),
            phase=state.get("phase"),
            step=STEP_LABELS.get(state.get("step"), state.get("step")),
            phases=phases,
            messages=messages,
            pause=pause,
            pause_since=local_time((pause or {}).get("since")),
            wait_until=local_time(state.get("wait_until")),
            auth_expires=local_time(state.get("auth_expires_at")),
            updated=local_time(state.get("updated_at")),
            mail_queue=[
                {
                    **m,
                    "queued_at": local_time(m.get("queued_at")),
                    "next_try": local_time(_plus(m.get("last_try"), RETRY_S)),
                }
                for m in (mail_queue or [])
            ],
        )
    )


class StatusWriter:
    def __init__(self, settings: Settings, log=print):
        self.settings = settings
        self.log = log
        self.last_path: Path | None = None

    def write(self, state: dict) -> Path:
        queue = Outbox(self.settings.outbox_dir, log=self.log).summary()
        markup = render_status(state, self.settings, queue)
        for target in (self.settings.status_dir, self.settings.fallback_status_dir):
            try:
                target.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(
                    dir=target, prefix=".status-", suffix=".html"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(markup)
                os.chmod(tmp, 0o644)
                path = target / "index.html"
                os.replace(tmp, path)
                if target != self.settings.status_dir and self.last_path != path:
                    self.log(
                        f"Hinweis: Statusordner {self.settings.status_dir} nicht beschreibbar, "
                        f"Seite liegt unter {path}"
                    )
                self.last_path = path
                return path
            except OSError:
                continue
        raise OSError("Statusseite konnte nirgends geschrieben werden")
