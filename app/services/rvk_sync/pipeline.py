"""Ablauf des monatlichen RVK-Abgleichs (P8).

a) Download-Seite prüfen: neuer Versionsname? Sonst Ende (nur Log, keine Mail).
b) Live-DB per Backup-API nach SYNC_WORK_DIR kopieren, neuen Abzug dort in *_new importieren.
c) Vergleich alt/neu, Prüfungen Anzahl und Stichprobe. Erst wenn beide bestanden sind: Tabellen
   in der Staging-DB tauschen, `notation_change` befüllen, Embeddings neu berechnen,
   Goldstandard markieren, Evaluation vorher (live) und nachher (Staging).
d) Bestanden -> Laufzeit-Tabellen übernehmen, Dateien atomar tauschen, alte Dateien als Backup
   (die letzten SYNC_BACKUP_KEEP bleiben), gunicorn-Worker neu starten.
   Nicht bestanden -> Live-Dateien unberührt, Staging-Dateien weg.
e) Info-Mail in jedem Fall außer „kein neuer Stand“ und Probelauf.

Alle Netz- und Rechenschritte kommen als Abhängigkeiten (`SyncDeps`) herein, damit der Ablauf ohne
Netz und ohne Modell testbar ist. Die echte Verdrahtung steht in scripts/monthly_sync.py.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.models import SCHEMA_SQL
from app.services import evaluation, rvk_repository
from app.services.rvk_sync import checks, diff, swap

logger = logging.getLogger(__name__)


@dataclass
class SyncDeps:
    # () -> URL des Gesamtabzugs auf der Download-Seite
    find_dump_url: Callable[[], str]
    # URL -> Versionsname, z. B. "2026/3"
    version_from_url: Callable[[str], str]
    # (URL, Rohdaten-Ordner) -> Pfad der entpackten XML-Datei
    download: Callable[[str, Path], Path]
    # (DB-Pfad, XML-Pfad) -> (Notationen, Registereinträge); befüllt die *_new-Tabellen
    import_dump: Callable[[Path, Path], tuple[int, int]]
    # (Tabellen in der Verbindung tauschen)
    swap_tables: Callable[[sqlite3.Connection], None]
    # (DB-Pfad, Pfade der Embedding-Dateien) -> None
    build_embeddings: Callable[[Path, dict[str, Path]], None]
    # (Pfade DB+Embeddings, Gold-Einträge) -> Kennzahlen {"hit@10", "mrr", "n", …}
    evaluate: Callable[[dict[str, Path], list], dict]
    # (Betreff, HTML, Text) -> "sent" | "file:<pfad>"
    send_mail: Callable[[str, str, str], str]
    # () -> True, wenn die Worker ein Signal bekommen haben
    signal_workers: Callable[[], bool]
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def _setting(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def current_version(db_path: Path) -> str | None:
    conn = rvk_repository.get_readonly_connection(str(db_path))
    if conn is None:
        return None
    try:
        return rvk_repository.latest_rvk_version(conn)
    finally:
        conn.close()


def load_gold(path: str | Path | None) -> list[evaluation.GoldEntry]:
    if not path or not Path(path).is_file():
        return []
    return evaluation.parse_gold(Path(path).read_text(encoding="utf-8").splitlines())


def _log_live_status(db_path: Path, source_file: str, status: str) -> None:
    """Eintrag in sync_log der Live-DB (Fehler, nicht bestanden). Nie ausschlaggebend."""
    if not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute(
                "INSERT INTO sync_log (run_at, source_file, notation_count, register_count, "
                "status) VALUES (?, ?, 0, 0, ?)",
                (datetime.now(UTC).isoformat(), source_file, status[:500]),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("sync_log der Live-DB nicht beschreibbar: %s", exc)


def run_monthly_sync(
    config, deps: SyncDeps, force: bool = False, dry_run: bool = False
) -> dict:
    """Gesamtablauf mit Sperre. Liefert ein Ergebnis-Dict (auch Grundlage der Mail).

    status: "already_running" | "no_new_version" | "swapped" | "rejected" | "error" | "dry_run"
    """
    try:
        with swap.SyncLock(_setting(config, "SYNC_LOCK_PATH")):
            return _run(config, deps, force=force, dry_run=dry_run)
    except swap.AlreadyRunning as exc:
        logger.warning("%s", exc)
        return {"status": "already_running", "kind": "rvk"}


def _run(config, deps: SyncDeps, force: bool, dry_run: bool) -> dict:
    live = swap.live_paths(config)
    staging = swap.staging_paths(config)
    result: dict = {
        "kind": "rvk",
        "status": "error",
        "started_at": deps.now().isoformat(timespec="seconds"),
        "old_version": current_version(live["DATABASE_PATH"]),
        "dry_run": dry_run,
        "checks": [],
        "gold_marks": [],
    }
    source_file = ""
    try:
        url = deps.find_dump_url()
        new_version = deps.version_from_url(url)
        result["new_version"] = new_version
        source_file = Path(url).name
        if new_version == result["old_version"] and not force:
            logger.info("Kein neuer RVK-Stand (%s) – Ende.", new_version)
            result["status"] = "no_new_version"
            return result

        swap.clear_staging(staging.values())
        staging_db = staging["DATABASE_PATH"]
        if live["DATABASE_PATH"].is_file():
            swap.copy_database(live["DATABASE_PATH"], staging_db)
        staging_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(staging_db)
        try:
            diff.migrate_notation_change(conn)
            conn.executescript(SCHEMA_SQL)
        finally:
            conn.close()

        dump_path = deps.download(url, Path(_setting(config, "SYNC_RAW_DIR")))
        notation_count, register_count = deps.import_dump(staging_db, dump_path)
        _decide(
            config, deps, result, staging, dump_path, notation_count, register_count
        )
    except Exception as exc:
        logger.exception("RVK-Abgleich fehlgeschlagen")
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log_live_status(live["DATABASE_PATH"], source_file, f"error: {exc}")
        swap.clear_staging(staging.values())

    result["finished_at"] = deps.now().isoformat(timespec="seconds")
    if dry_run:
        result["mail"] = "keine (Probelauf)"
        return result
    try:
        from app.services import mailer

        subject, html, text = mailer.render_sync_report(result)
        result["mail"] = deps.send_mail(subject, html, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Info-Mail konnte nicht erzeugt werden: %s", exc)
        result["mail"] = f"Fehler: {exc}"
    return result


def _decide(
    config,
    deps: SyncDeps,
    result: dict,
    staging: dict[str, Path],
    dump_path: Path,
    notation_count: int,
    register_count: int,
) -> None:
    live = swap.live_paths(config)
    staging_db = staging["DATABASE_PATH"]
    report = checks.CheckReport()
    gold = load_gold(_setting(config, "SYNC_GOLD_PATH"))
    new_version = result["new_version"]

    conn = sqlite3.connect(staging_db)
    try:
        old_count = diff.count_rows(conn, "notation")
        result["old_count"] = old_count
        result["new_count"] = notation_count
        delta = diff.compute_diff(conn)
        result["diff"] = delta.counts
        result["diff_baseline"] = delta.baseline

        ok, message = checks.check_count(
            old_count or None,
            notation_count,
            _setting(config, "SYNC_MAX_COUNT_DEVIATION"),
        )
        report.add("Notationsanzahl", ok, message)

        sample = list(checks.main_groups(conn, "notation")) if old_count else []
        sample += list(_setting(config, "SYNC_SAMPLE_NOTATIONS", ()) or ())
        if sample:
            ok, message, _missing = checks.check_sample(conn, sample, "notation_new")
        else:
            ok, message = True, "Kein bisheriger Stand – keine Stichprobe."
        report.add("Stichprobe bekannter Notationen", ok, message)

        gold_notations = [n for e in gold for n in e.expected]
        old_present = (
            checks.existing_notations(conn, gold_notations, "notation")
            if old_count
            else set()
        )
        new_present = checks.existing_notations(conn, gold_notations, "notation_new")
        marks = checks.mark_gold(gold, old_present, new_present, new_version, delta)
        result["gold_marks"] = [
            {
                "query": m.query,
                "notation": m.notation,
                "status": m.status,
                "label": m.label,
            }
            for m in marks
        ]

        early_ok = report.passed
        if early_ok:
            conn.execute("BEGIN")
            deps.swap_tables(conn)
            result["notation_change_rows"] = (
                0 if delta.baseline else diff.apply_diff(conn, delta, new_version)
            )
            conn.execute(
                "INSERT INTO sync_log (run_at, source_file, notation_count, register_count, "
                "status) VALUES (?, ?, ?, ?, 'success')",
                (
                    datetime.now(UTC).isoformat(),
                    dump_path.name,
                    notation_count,
                    register_count,
                ),
            )
            conn.commit()
    finally:
        conn.close()

    if not early_ok:
        report.add(
            "Evaluation Hit@10",
            False,
            "Nicht ausgeführt, weil eine Prüfung oben scheiterte.",
        )
        _reject(result, report, live, staging)
        return

    deps.build_embeddings(staging_db, staging)
    filtered = evaluation.exclude_notations(gold, checks.excluded_by_marks(marks))
    result["gold_count"] = len(gold)
    result["gold_count_scored"] = len(filtered)
    if filtered:
        before = (
            deps.evaluate(live, filtered) if live["DATABASE_PATH"].is_file() else None
        )
        after = deps.evaluate(staging, filtered)
        result["eval_before"] = before
        result["eval_after"] = after
        if before is None:
            report.add(
                "Evaluation Hit@10", True, "Kein bisheriger Stand zum Vergleich."
            )
        else:
            ok, message = checks.check_eval(
                before, after, _setting(config, "SYNC_MAX_HIT10_DROP")
            )
            report.add("Evaluation Hit@10", ok, message)
    else:
        report.add("Evaluation Hit@10", False, "Keine Goldstandard-Anfragen vorhanden.")

    if not report.passed:
        _reject(result, report, live, staging)
        return
    result["checks"] = _checks_dicts(report)
    if result.get("dry_run"):
        result["status"] = "dry_run"
        swap.clear_staging(staging.values())
        return

    result["carried_over"] = {}
    if live["DATABASE_PATH"].is_file():
        result["carried_over"] = swap.carry_over_runtime_tables(
            live["DATABASE_PATH"], staging_db
        )
        swapped = swap.swap_files(
            live,
            staging,
            Path(_setting(config, "SYNC_BACKUP_DIR")),
            result.get("old_version"),
            now=deps.now(),
        )
        result["backup_dir"] = str(swapped.backup_dir)
        result["backups_removed"] = swap.prune_backups(
            Path(_setting(config, "SYNC_BACKUP_DIR")),
            _setting(config, "SYNC_BACKUP_KEEP", 2),
        )
    else:
        # Erstinstallation: nichts zu sichern
        for key, path in live.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging[key], path)
    result["workers_signalled"] = deps.signal_workers()
    result["status"] = "swapped"
    try:
        result["raw_removed"] = swap.prune_raw_dumps(
            Path(_setting(config, "SYNC_RAW_DIR")), dump_path.name
        )
    except OSError as exc:
        logger.warning("Alte Rohdateien nicht entfernt: %s", exc)


def _checks_dicts(report: checks.CheckReport) -> list[dict]:
    return [
        {"name": r.name, "passed": r.passed, "message": r.message}
        for r in report.results
    ]


def _reject(result: dict, report: checks.CheckReport, live, staging) -> None:
    result["checks"] = _checks_dicts(report)
    result["status"] = "rejected"
    failed = "; ".join(f"{r.name}: {r.message}" for r in report.failed())
    if not result.get("dry_run"):
        _log_live_status(
            live["DATABASE_PATH"],
            f"{result.get('new_version')}",
            f"rejected: {failed}",
        )
    swap.clear_staging(staging.values())
