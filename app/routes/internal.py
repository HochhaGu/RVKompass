"""Manueller Anstoß des monatlichen RVK-Abgleichs (P8).

POST /internal/sync mit Token im Header `X-Sync-Token` (oder, wie bisher, `?token=`). Der
Abgleich läuft losgelöst als eigener Prozess (scripts/monthly_sync.py), die Antwort kommt sofort:
- 202 {"status": "started"}
- 409 {"status": "running"}, wenn ein Abgleich die Sperre hält
- 403 ohne oder mit falschem Token, oder wenn kein SYNC_TOKEN gesetzt ist

Das Token steht nie in den Argumenten des Prozesses und nie im Log. Von außen ist /internal/*
über Caddy gesperrt (deploy/caddy/rvkompass.caddy.live); Aufruf nur auf dem Server selbst.
"""

from __future__ import annotations

import hmac
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, request

from app.config import BASE_DIR
from app.services.rvk_sync import swap

bp = Blueprint("internal", __name__)

SYNC_SCRIPT = BASE_DIR / "scripts" / "monthly_sync.py"


def _popen():
    """Für Tests austauschbar über app.extensions["sync_popen"]."""
    return current_app.extensions.get("sync_popen", subprocess.Popen)


def _token_ok() -> bool:
    expected = current_app.config.get("SYNC_TOKEN") or ""
    given = request.headers.get("X-Sync-Token") or request.args.get("token") or ""
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def start_sync_process(log_path: str | Path):
    """Startet scripts/monthly_sync.py losgelöst; Ausgabe hängt an das Sync-Log an."""
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as log:
        return _popen()(
            [sys.executable, str(SYNC_SCRIPT)],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


@bp.post("/internal/sync")
def sync():
    if not _token_ok():
        abort(403)
    if swap.is_locked(current_app.config["SYNC_LOCK_PATH"]):
        return {"status": "running"}, 409
    start_sync_process(current_app.config["SYNC_LOG_PATH"])
    current_app.logger.info("RVK-Abgleich manuell angestoßen.")
    return {"status": "started"}, 202
