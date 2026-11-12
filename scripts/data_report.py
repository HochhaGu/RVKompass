"""Kennzahlen des Datenbestands (P8), nur lesend: Dateigrößen, Notationen, GND-Datensätze.

Für docs/deployment.md (Platzbedarf, Backups) und den Phasenbericht.

Aufruf:
    python scripts/data_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services import gnd_local, rvk_repository
from app.services.rvk_sync.swap import DATA_FILE_KEYS


def size_mb(path: Path) -> float | None:
    return round(path.stat().st_size / 1_000_000, 1) if path.is_file() else None


def main() -> int:
    config = get_config()
    files = {key: Path(getattr(config, key)) for key in DATA_FILE_KEYS}
    sizes = {key: size_mb(path) for key, path in files.items()}
    swapped_total = round(sum(v for v in sizes.values() if v), 1)
    report = {
        "files_mb": {files[k].name: v for k, v in sizes.items()},
        "swapped_total_mb": swapped_total,
        "backups_mb_for_2": round(2 * swapped_total, 1),
        "gnd_mb": size_mb(Path(config.GND_DATABASE_PATH)),
    }
    conn = rvk_repository.get_readonly_connection(config.DATABASE_PATH)
    if conn is not None:
        try:
            report["notation_count"] = conn.execute(
                "SELECT COUNT(*) FROM notation"
            ).fetchone()[0]
            report["register_count"] = conn.execute(
                "SELECT COUNT(*) FROM register_entry"
            ).fetchone()[0]
            report["rvk_version"] = rvk_repository.latest_rvk_version(conn)
        finally:
            conn.close()
    gconn = rvk_repository.get_readonly_connection(config.GND_DATABASE_PATH)
    if gconn is not None:
        try:
            report["gnd"] = gnd_local.read_meta(gconn)
        finally:
            gconn.close()
    raw = Path(config.SYNC_RAW_DIR)
    if raw.is_dir():
        report["raw_mb"] = {
            p.relative_to(raw).as_posix(): size_mb(p)
            for p in sorted(raw.rglob("*"))
            if p.is_file()
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
