"""Arbeitskopie, Tausch, Backups, Sperre und Neustart der Worker beim RVK-Abgleich (P8).

Ablauf beim Tausch:
1. Die Laufzeit-Tabellen der Live-DB (`expansion_cache`, `gnd_cache`, `daily_usage`) werden in
   die Staging-DB übernommen, damit nichts aus der Laufzeit des Abgleichs verloren geht.
   `daily_usage` ist der Tageszähler für Aufrufe des Sprachmodells (Kostenbremse) – nicht zu
   verwechseln mit der Nutzungsstatistik in usage.sqlite3, die gar nicht getauscht wird.
2. Jede Live-Datei wird per Hardlink (sonst Kopie) in den Backup-Ordner
   `rvk-<alteVersion>-<UTC-Zeit>/` gesichert und dann per `os.replace` atomar durch die
   Staging-Datei ersetzt. Die Live-Pfade fehlen dabei zu keinem Zeitpunkt.
3. Tritt dabei ein Fehler auf, werden die bereits getauschten Dateien aus dem Backup
   zurückgeschrieben.
4. Es bleiben die neuesten SYNC_BACKUP_KEEP Backup-Ordner.
5. gunicorn bekommt SIGHUP und startet die Worker sanft neu; jeder Worker lädt dabei
   Datenbank und Embeddings neu.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import signal
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

logger = logging.getLogger(__name__)

# Laufzeit-Tabellen der Live-DB, die beim Tausch erhalten bleiben
RUNTIME_TABLES = ("expansion_cache", "gnd_cache", "daily_usage")

# Config-Schlüssel der getauschten Dateien
DATA_FILE_KEYS = (
    "DATABASE_PATH",
    "EMBEDDINGS_PATH",
    "EMBEDDINGS_META_PATH",
    "REGISTER_TERM_EMBEDDINGS_PATH",
    "REGISTER_TERM_META_PATH",
)

BACKUP_PREFIX = "rvk-"
_BACKUP_TIME_RE = re.compile(r"-(\d{8}T\d{6}Z)$")


class AlreadyRunning(RuntimeError):
    """Ein anderer Abgleich hält die Sperre."""


def _setting(config, key):
    if isinstance(config, dict):
        return config[key]
    return getattr(config, key)


def live_paths(config) -> dict[str, Path]:
    return {key: Path(_setting(config, key)) for key in DATA_FILE_KEYS}


def staging_paths(config, work_dir: Path | None = None) -> dict[str, Path]:
    """Gleiche Dateinamen wie live, aber im Arbeitsordner SYNC_WORK_DIR."""
    base = Path(work_dir or _setting(config, "SYNC_WORK_DIR"))
    return {key: base / path.name for key, path in live_paths(config).items()}


# --- Arbeitskopie ---------------------------------------------------------------------------


def copy_database(source: Path, target: Path) -> None:
    """Konsistente Kopie per SQLite-Backup-API, auch während die App liest."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def clear_staging(paths: Iterable[Path]) -> None:
    """Entfernt nur die bekannten Staging-Dateien (nie ganze Ordner)."""
    for path in paths:
        for candidate in (path, Path(f"{path}-journal")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def carry_over_runtime_tables(
    live_db: Path, staging_db: Path, tables: Iterable[str] = RUNTIME_TABLES
) -> dict[str, int]:
    """Übernimmt Laufzeit-Tabellen aus der Live- in die Staging-DB (INSERT OR REPLACE).

    Liefert je Tabelle die Zahl der übernommenen Zeilen. Fehlt eine Tabelle live, wird sie
    übersprungen.
    """
    counts: dict[str, int] = {}
    conn = sqlite3.connect(staging_db)
    try:
        conn.execute("ATTACH DATABASE ? AS live", (str(live_db),))
        for table in tables:
            exists = conn.execute(
                "SELECT 1 FROM live.sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            create = conn.execute(
                "SELECT sql FROM live.sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            has_main = conn.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not has_main:
                conn.execute(create)
            columns = [r[1] for r in conn.execute(f"PRAGMA live.table_info({table})")]
            cols = ", ".join(columns)
            cur = conn.execute(
                f"INSERT OR REPLACE INTO main.{table} ({cols}) SELECT {cols} FROM live.{table}"
            )
            counts[table] = cur.rowcount
        conn.commit()
        conn.execute("DETACH DATABASE live")
    finally:
        conn.close()
    return counts


# --- Tausch und Backups ---------------------------------------------------------------------


def backup_dir_name(old_version: str | None, now: datetime) -> str:
    version = re.sub(r"[^0-9A-Za-z]+", "_", old_version or "unbekannt").strip("_")
    return f"{BACKUP_PREFIX}{version}-{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


@dataclass
class SwapResult:
    backup_dir: Path
    swapped: list[str]


def swap_files(
    live: dict[str, Path],
    staging: dict[str, Path],
    backup_root: Path,
    old_version: str | None,
    now: datetime | None = None,
    replace: Callable = os.replace,
) -> SwapResult:
    """Sichert die Live-Dateien und ersetzt sie atomar durch die Staging-Dateien.

    Bei einem Fehler werden alle schon getauschten Dateien aus dem Backup zurückgeschrieben und
    der Fehler weitergegeben.
    """
    missing = [str(p) for p in staging.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError("Staging-Dateien fehlen: " + ", ".join(missing))
    backup_dir = Path(backup_root) / backup_dir_name(
        old_version, now or datetime.now(UTC)
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    for key, path in live.items():
        if path.is_file():
            _link_or_copy(path, backup_dir / path.name)
    swapped: list[str] = []
    try:
        for key, path in live.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            replace(staging[key], path)
            swapped.append(key)
    except Exception:
        restore_from_backup(backup_dir, {k: live[k] for k in swapped})
        raise
    return SwapResult(backup_dir=backup_dir, swapped=swapped)


def restore_from_backup(backup_dir: Path, targets: dict[str, Path]) -> list[str]:
    """Schreibt Dateien aus einem Backup-Ordner zurück (atomar je Datei)."""
    restored = []
    for key, path in targets.items():
        source = Path(backup_dir) / path.name
        if not source.is_file():
            continue
        tmp = path.with_name(path.name + ".restore")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(source, tmp)
        os.replace(tmp, path)
        restored.append(key)
    if restored:
        logger.warning("Aus Backup %s zurückgeschrieben: %s", backup_dir, restored)
    return restored


def _backup_time(name: str) -> str:
    match = _BACKUP_TIME_RE.search(name)
    return match.group(1) if match else ""


def backups_to_remove(names: Iterable[str], keep: int) -> list[str]:
    """Welche Backup-Ordner wegfallen: alle außer den `keep` neuesten (nach Zeit im Namen)."""
    backups = sorted(
        (n for n in names if n.startswith(BACKUP_PREFIX) and _backup_time(n)),
        key=_backup_time,
        reverse=True,
    )
    return backups[max(keep, 0) :]


def prune_backups(backup_root: Path, keep: int) -> list[str]:
    root = Path(backup_root)
    if not root.is_dir():
        return []
    names = [p.name for p in root.iterdir() if p.is_dir()]
    removed = backups_to_remove(names, keep)
    for name in removed:
        shutil.rmtree(root / name)
    return removed


def prune_raw_dumps(raw_dir: Path, keep_name: str) -> list[str]:
    """Löscht ältere entpackte RVK-Gesamtabzüge (rvko_marcxml_*.xml) außer `keep_name`."""
    root = Path(raw_dir)
    if not root.is_dir():
        return []
    removed = []
    for path in sorted(root.glob("rvko_marcxml_*.xml")):
        if path.name != keep_name and path.is_file():
            path.unlink()
            removed.append(path.name)
    return removed


# --- Sperre ---------------------------------------------------------------------------------


class SyncLock:
    """Exklusive Sperre per fcntl.flock; ein zweiter Lauf endet sofort mit AlreadyRunning."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AlreadyRunning("Ein RVK-Abgleich läuft bereits.") from None
        self._fh = fh
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def is_locked(path: str | Path) -> bool:
    """Probe ohne Warten: Hält gerade ein anderer Prozess die Sperre?"""
    try:
        with SyncLock(path):
            return False
    except AlreadyRunning:
        return True


# --- Neustart der Worker --------------------------------------------------------------------


def signal_gunicorn(pid_path: str | Path, kill: Callable = os.kill) -> bool:
    """SIGHUP an den gunicorn-Hauptprozess: Worker starten sanft neu und laden neu.

    Fehlt die PID-Datei (Werkstatt ohne gunicorn), wird das nur protokolliert.
    """
    path = Path(pid_path)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        logger.info("Keine gunicorn-PID unter %s – kein Neustart der Worker.", path)
        return False
    try:
        kill(pid, signal.SIGHUP)
    except OSError as exc:
        logger.warning("SIGHUP an gunicorn (PID %s) fehlgeschlagen: %s", pid, exc)
        return False
    logger.info("SIGHUP an gunicorn (PID %s) gesendet.", pid)
    return True
