"""Betriebszustand für Docker-Healthcheck und Überwachung (P8): GET /health.

Antwort (JSON):
- status: "ok" oder "degraded" (HTTP 503, wenn die RVK-Datenbank fehlt oder unlesbar ist)
- db_version: ID und Zeitpunkt des letzten erfolgreichen Eintrags in sync_log
- rvk_version: RVK-Stand, z. B. "2026/2"
- notation_count: Anzahl Notationen
- gnd_version: Stand des lokalen GND-Abzugs (gnd_meta) oder null
- cache_hits_today: GND- und Sprachmodell-Antworten aus dem Zwischenspeicher heute (UTC)
- requests_today, zero_hit_rate_today: aus der anonymen Nutzungsstatistik

Alle Datenbanken werden nur lesend geöffnet.
"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app

from app import limiter
from app.services import gnd_local, rvk_repository, usage_stats

bp = Blueprint("health", __name__)


def _gnd_version(path: str | None) -> str | None:
    if not path:
        return None
    conn = rvk_repository.get_readonly_connection(path)
    if conn is None:
        return None
    try:
        meta = gnd_local.read_meta(conn)
    finally:
        conn.close()
    return meta["version"] if meta else None


def health_payload(config) -> tuple[dict, int]:
    usage = usage_stats.stats_for_day(config.get("USAGE_DATABASE_PATH") or "")
    payload = {
        "status": "ok",
        "db_version": None,
        "rvk_version": None,
        "notation_count": None,
        "gnd_version": _gnd_version(config.get("GND_DATABASE_PATH")),
        "cache_hits_today": usage["gnd_cache_hits"] + usage["llm_cache_hits"],
        "requests_today": usage["requests"],
        "zero_hit_rate_today": usage["zero_hit_rate"],
    }
    conn = rvk_repository.get_readonly_connection(config.get("DATABASE_PATH") or "")
    if conn is None:
        payload["status"] = "degraded"
        return payload, 503
    try:
        row = conn.execute(
            "SELECT id, run_at FROM sync_log WHERE status = 'success' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            payload["db_version"] = {"sync_id": row[0], "run_at": row[1]}
        payload["rvk_version"] = rvk_repository.latest_rvk_version(conn)
        payload["notation_count"] = conn.execute(
            "SELECT COUNT(*) FROM notation"
        ).fetchone()[0]
    except sqlite3.Error:
        payload["status"] = "degraded"
        return payload, 503
    finally:
        conn.close()
    return payload, 200


@bp.get("/health")
@limiter.exempt
def health():
    # Ausgenommen vom Rate-Limit: Der Docker-Healthcheck fragt alle 30 Sekunden.
    payload, status = health_payload(current_app.config)
    return payload, status
