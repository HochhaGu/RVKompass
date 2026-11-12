"""Online-Backup der Datenbanken (scripts/backup_db.py)."""

import importlib.util
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "backup_db", ROOT / "scripts" / "backup_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_db"] = module
    spec.loader.exec_module(module)
    return module


backup_db = _load()


def test_backup_copies_existing_databases_and_prunes(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "rvk.sqlite3")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    config = SimpleNamespace(
        DATABASE_PATH=str(data / "rvk.sqlite3"),
        GND_DATABASE_PATH=str(data / "gnd.sqlite3"),  # fehlt -> übersprungen
        USAGE_DATABASE_PATH=str(data / "usage.sqlite3"),
    )
    target = tmp_path / "backups"
    (target / "fremd").mkdir(parents=True)
    folders = [
        backup_db.backup(config, target, keep=2, now=datetime(2026, 12, d, tzinfo=UTC))
        for d in (1, 2, 3)
    ]
    assert sorted(p.name for p in target.iterdir()) == [
        "20261202T000000Z",
        "20261203T000000Z",
        "fremd",
    ]
    copy = sqlite3.connect(folders[-1] / "rvk.sqlite3")
    assert copy.execute("SELECT x FROM t").fetchone() == (42,)
    copy.close()
    assert not (folders[-1] / "gnd.sqlite3").exists()
