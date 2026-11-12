"""Gesamtablauf des monatlichen Abgleichs (app/services/rvk_sync/pipeline.py), ohne Netz/Modell."""

import importlib.util
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.rvk_sync import pipeline, swap
from tests.rvk_sync_helpers import (
    NEW_ROWS,
    NEW_TABLES_SQL,
    OLD_ROWS,
    insert_notations,
    make_db,
)

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_pipeline_test", ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_rvk = _load("sync_rvk")
fetch = _load("fetch_marcxml_dump")

GOLD = "query;erwartete_notationen\nMutter;ZL 4620\nVeraltet;ZL 9999\nFehlt;XX 1\n"


class Recorder:
    def __init__(
        self, tmp_path, new_rows=NEW_ROWS, after_hit10=0.55, url=None, fail=None
    ):
        self.tmp_path = tmp_path
        self.new_rows = new_rows
        self.after_hit10 = after_hit10
        self.url = url or "https://rvk.example/downloads/rvko_marcxml_2026_3.xml.gz"
        self.fail = fail
        self.mails = []
        self.signals = 0
        self.evaluated = []
        self.downloads = 0

    def deps(self):
        def find():
            if self.fail == "page":
                raise ConnectionError("Download-Seite nicht erreichbar")
            return self.url

        def download(url, raw_dir):
            self.downloads += 1
            raw_dir.mkdir(parents=True, exist_ok=True)
            path = raw_dir / Path(url).name.removesuffix(".gz")
            path.write_text("<xml/>")
            return path

        def import_dump(db_path, dump_path):
            conn = sqlite3.connect(db_path)
            conn.executescript(NEW_TABLES_SQL)
            insert_notations(conn, "notation_new", "register_entry_new", self.new_rows)
            conn.close()
            return len(self.new_rows), 0

        def build_embeddings(db_path, paths):
            for key, path in paths.items():
                if key != "DATABASE_PATH":
                    path.write_text(f"neu:{path.name}")

        def evaluate(paths, entries):
            self.evaluated.append((paths["DATABASE_PATH"], [e.query for e in entries]))
            staging = "staging" in str(paths["DATABASE_PATH"])
            return {
                "hit@10": self.after_hit10 if staging else 0.55,
                "mrr": 0.3,
                "n": len(entries),
            }

        return pipeline.SyncDeps(
            find_dump_url=find,
            version_from_url=fetch.version_from_url,
            download=download,
            import_dump=import_dump,
            swap_tables=sync_rvk.swap_in_new_tables,
            build_embeddings=build_embeddings,
            evaluate=evaluate,
            send_mail=lambda s, h, t: self.mails.append((s, h, t)) or "sent",
            signal_workers=self._signal,
            now=lambda: datetime(2026, 12, 2, 3, 0, tzinfo=UTC),
        )

    def _signal(self):
        self.signals += 1
        return True


@pytest.fixture
def config(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = make_db(
        data / "rvk.sqlite3", new_rows=None, sync_file="rvko_marcxml_2026_2.xml"
    )
    conn.execute("INSERT INTO gnd_cache VALUES ('k', '{}', '2026-12-01T00:00:00')")
    conn.commit()
    conn.close()
    for name in (
        "embeddings.npy",
        "embeddings_meta.json",
        "register_term_embeddings.npy",
        "register_term_meta.json",
    ):
        (data / name).write_text(f"alt:{name}")
    gold = tmp_path / "gold.csv"
    gold.write_text(GOLD, encoding="utf-8")
    return {
        "DATABASE_PATH": str(data / "rvk.sqlite3"),
        "EMBEDDINGS_PATH": str(data / "embeddings.npy"),
        "EMBEDDINGS_META_PATH": str(data / "embeddings_meta.json"),
        "REGISTER_TERM_EMBEDDINGS_PATH": str(data / "register_term_embeddings.npy"),
        "REGISTER_TERM_META_PATH": str(data / "register_term_meta.json"),
        "SYNC_WORK_DIR": str(data / "staging"),
        "SYNC_BACKUP_DIR": str(data / "backups"),
        "SYNC_LOCK_PATH": str(data / "sync.lock"),
        "SYNC_RAW_DIR": str(data / "raw"),
        "SYNC_GOLD_PATH": str(gold),
        "SYNC_MAX_COUNT_DEVIATION": 0.5,
        "SYNC_MAX_HIT10_DROP": 0.05,
        "SYNC_BACKUP_KEEP": 2,
        "SYNC_SAMPLE_NOTATIONS": ("ZL 4630",),
    }


def _notations(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT notation FROM notation")}
    finally:
        conn.close()


def test_no_new_version_ends_without_mail(config, tmp_path):
    rec = Recorder(
        tmp_path, url="https://rvk.example/downloads/rvko_marcxml_2026_2.xml.gz"
    )
    result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "no_new_version"
    assert rec.mails == [] and rec.downloads == 0


def test_passed_swaps_and_mails(config, tmp_path):
    rec = Recorder(tmp_path)
    result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "swapped", result
    assert result["old_version"] == "2026/2" and result["new_version"] == "2026/3"
    assert result["diff"] == {"new": 1, "changed": 2, "deleted": 1}
    assert result["notation_change_rows"] == 4
    assert _notations(config["DATABASE_PATH"]) == {r[0] for r in NEW_ROWS}
    assert Path(config["EMBEDDINGS_PATH"]).read_text() == "neu:embeddings.npy"
    backup = Path(result["backup_dir"])
    assert backup.name == "rvk-2026_2-20261202T030000Z"
    assert (backup / "embeddings.npy").read_text() == "alt:embeddings.npy"
    assert _notations(backup / "rvk.sqlite3") == {r[0] for r in OLD_ROWS}
    # Laufzeit-Tabellen übernommen, neuer Stand in sync_log
    conn = sqlite3.connect(config["DATABASE_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM gnd_cache").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT source_file FROM sync_log WHERE status='success' ORDER BY id DESC"
        ).fetchone()[0]
        == "rvko_marcxml_2026_3.xml"
    )
    conn.close()
    assert pipeline.current_version(Path(config["DATABASE_PATH"])) == "2026/3"
    # Gold: „Veraltet“ fällt in beiden Läufen heraus, „Fehlt“ bleibt in der Basis
    assert [q for _, q in rec.evaluated] == [["Mutter", "Fehlt"], ["Mutter", "Fehlt"]]
    statuses = {(m["query"], m["status"]) for m in result["gold_marks"]}
    assert statuses == {("Veraltet", "deleted"), ("Fehlt", "missing_before")}
    assert rec.signals == 1
    ((subject, html, _),) = rec.mails
    assert "2026/3 ist live" in subject
    assert "gelöscht in 2026/3" in html and "fehlte schon vorher" in html
    assert not any(p.exists() for p in swap.staging_paths(config).values())


def test_failed_eval_keeps_old_db(config, tmp_path):
    rec = Recorder(tmp_path, after_hit10=0.40)
    result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "rejected"
    assert _notations(config["DATABASE_PATH"]) == {r[0] for r in OLD_ROWS}
    assert Path(config["EMBEDDINGS_PATH"]).read_text() == "alt:embeddings.npy"
    assert rec.signals == 0
    ((subject, html, _),) = rec.mails
    assert "nicht übernommen" in subject and "NICHT bestanden" in html
    assert not any(p.exists() for p in swap.staging_paths(config).values())
    assert pipeline.current_version(Path(config["DATABASE_PATH"])) == "2026/2"


def test_failed_sample_skips_embeddings(config, tmp_path):
    rows = [r for r in NEW_ROWS if r[0] != "ZM"]  # Hauptgruppe fehlt
    rec = Recorder(tmp_path, new_rows=rows)
    result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "rejected"
    assert rec.evaluated == []
    failed = [c["name"] for c in result["checks"] if not c["passed"]]
    assert "Stichprobe bekannter Notationen" in failed


def test_download_error_mails_cause(config, tmp_path):
    rec = Recorder(tmp_path, fail="page")
    result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "error"
    ((subject, html, _),) = rec.mails
    assert "Fehler" in subject
    assert "Download-Seite nicht erreichbar" in html


def test_dry_run_changes_nothing_and_sends_no_mail(config, tmp_path):
    rec = Recorder(tmp_path)
    result = pipeline.run_monthly_sync(config, rec.deps(), dry_run=True)
    assert result["status"] == "dry_run"
    assert rec.mails == [] and rec.signals == 0
    assert _notations(config["DATABASE_PATH"]) == {r[0] for r in OLD_ROWS}
    assert not Path(config["SYNC_BACKUP_DIR"]).exists()


def test_force_runs_without_new_version(config, tmp_path):
    rec = Recorder(
        tmp_path, url="https://rvk.example/downloads/rvko_marcxml_2026_2.xml.gz"
    )
    result = pipeline.run_monthly_sync(config, rec.deps(), force=True, dry_run=True)
    assert result["status"] == "dry_run"
    assert rec.downloads == 1


def test_second_run_while_locked(config, tmp_path):
    rec = Recorder(tmp_path)
    with swap.SyncLock(config["SYNC_LOCK_PATH"]):
        result = pipeline.run_monthly_sync(config, rec.deps())
    assert result["status"] == "already_running"
    assert rec.mails == [] and rec.downloads == 0
