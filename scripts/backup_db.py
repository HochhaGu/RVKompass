"""Online-Backup der SQLite-Datenbanken (P8, Routine „Update und Backup“ in docs/deployment.md).

Sichert rvk.sqlite3, gnd.sqlite3 und usage.sqlite3 per SQLite-Backup-API – konsistent, auch
während die App läuft – nach <Ziel>/<UTC-Zeit>/. Es bleiben die neuesten --keep Ordner.
Embeddings werden nicht gesichert: Sie lassen sich aus rvk.sqlite3 neu berechnen und liegen
außerdem in den Backups des monatlichen Abgleichs (data/backups/).

Aufruf im Container:
    python scripts/backup_db.py --target /app/data/db-backups --keep 4
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services.rvk_sync.swap import copy_database

DB_KEYS = ("DATABASE_PATH", "GND_DATABASE_PATH", "USAGE_DATABASE_PATH")
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def backup(config, target: Path, keep: int, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime(STAMP_FORMAT)
    folder = Path(target) / stamp
    folder.mkdir(parents=True, exist_ok=False)
    for key in DB_KEYS:
        source = Path(getattr(config, key))
        if source.is_file():
            copy_database(source, folder / source.name)
    prune(Path(target), keep)
    return folder


def prune(target: Path, keep: int) -> list[str]:
    """Behält die `keep` neuesten Ordner mit Zeitstempel-Namen."""
    folders = []
    for path in target.iterdir():
        try:
            datetime.strptime(path.name, STAMP_FORMAT)
        except ValueError:
            continue
        if path.is_dir():
            folders.append(path)
    folders.sort(key=lambda p: p.name, reverse=True)
    removed = []
    for path in folders[max(keep, 1) :]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Online-Backup der SQLite-Datenbanken")
    parser.add_argument("--target", required=True)
    parser.add_argument("--keep", type=int, default=4)
    args = parser.parse_args(argv)
    folder = backup(get_config(), Path(args.target), args.keep)
    print(f"Backup: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
