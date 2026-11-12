"""Monatliche Aktualisierung des lokalen GND-Abzugs (P8).

Ablauf:
1. scripts/fetch_gnd_dump.py: neueste Version auf https://data.dnb.de/opendata/ ermitteln.
   Gleiche Version wie in gnd_meta -> Ende ohne Änderung.
2. Aufbau in <SYNC_WORK_DIR>/gnd.sqlite3 (Staging), die Live-Datei bleibt unberührt.
3. Plausibilität: Datensätze > 0 und Abweichung zum bisherigen Stand < SYNC_MAX_COUNT_DEVIATION.
4. Tausch per os.replace (atomar, gleiches Dateisystem). Bei Fehler bleibt die alte Datei.

Bei Fehlern gibt es eine Info-Mail (nur dann; ein normaler GND-Wechsel steht nur im Log).

Aufruf:
    python scripts/sync_gnd.py                  # Download (falls neu), Aufbau, Tausch
    python scripts/sync_gnd.py --force          # auch bei gleicher Version neu aufbauen
    python scripts/sync_gnd.py --skip-download  # vorhandene Dateien in data/raw/gnd/ nutzen
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_gnd_dump

from app.config import get_config
from app.services import gnd_local
from app.services.rvk_sync.checks import check_count

logger = logging.getLogger("sync_gnd")


def previous_count(db_path: str) -> int | None:
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            meta = gnd_local.read_meta(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return meta["record_count"] if meta else None


def build_database(
    target: Path, dump_path: Path, vocab_path: Path | None, version: str
) -> gnd_local.BuildStats:
    """Baut gnd.sqlite3 neu an `target` (vorhandene Datei dort wird ersetzt)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        gnd_local.create_schema(conn)
        with gzip.open(dump_path, "rt", encoding="utf-8") as fh:
            stats = gnd_local.insert_records(conn, gnd_local.iter_records(fh))
        if vocab_path is not None and vocab_path.is_file():
            labels = gnd_local.parse_gnd_sc_ntriples(
                vocab_path.read_text(encoding="utf-8").splitlines()
            )
            gnd_local.insert_category_labels(conn, labels)
            stats.extra["category_labels"] = len(labels)
        gnd_local.write_meta(conn, version, dump_path.name, stats.record_count)
    finally:
        conn.close()
    return stats


def run(
    config,
    force: bool = False,
    skip_download: bool = False,
    fetch: Callable = fetch_gnd_dump.fetch,
    build: Callable = build_database,
) -> dict:
    """Gesamtablauf; Rückgabe {"status": "unchanged"|"replaced"|"rejected", …}."""
    if skip_download:
        raw = Path(config.SYNC_RAW_DIR) / "gnd"
        dumps = sorted(raw.glob("authorities-gnd-sachbegriff_lds_*.jsonld.gz"))
        if not dumps:
            raise FileNotFoundError(f"Kein GND-Abzug in {raw}")
        found = fetch_gnd_dump.find_latest_dump(" ".join(p.name for p in dumps))
        info = {
            "status": "downloaded",
            "version": found[1],
            "dump_path": raw / found[0],
            "vocab_path": raw / "gnd-sc.nt",
        }
    else:
        info = fetch(config, force=force)
    if info["status"] == "unchanged" and not force:
        return {"status": "unchanged", "version": info["version"]}

    staging = Path(config.SYNC_WORK_DIR) / "gnd.sqlite3"
    stats = build(staging, info["dump_path"], info.get("vocab_path"), info["version"])
    old = previous_count(config.GND_DATABASE_PATH)
    ok, message = check_count(old, stats.record_count, config.SYNC_MAX_COUNT_DEVIATION)
    result = {
        "version": info["version"],
        "record_count": stats.record_count,
        "previous_count": old,
        "check": message,
    }
    if not ok:
        staging.unlink(missing_ok=True)
        result["status"] = "rejected"
        return result
    Path(config.GND_DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, config.GND_DATABASE_PATH)
    result["status"] = "replaced"
    return result


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Lokalen GND-Abzug aktualisieren")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args(argv)
    config = get_config()
    try:
        result = run(config, force=args.force, skip_download=args.skip_download)
    except Exception as exc:  # noqa: BLE001 – jeder Fehler: alte Datei bleibt, Mail
        logger.error("GND-Aktualisierung fehlgeschlagen: %s", exc)
        from app.services import mailer

        mailer.notify_gnd_error(config, str(exc))
        return 1
    logger.info("GND-Aktualisierung: %s", result)
    if result["status"] == "rejected":
        from app.services import mailer

        mailer.notify_gnd_error(config, f"Prüfung nicht bestanden: {result['check']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
