"""Tausch, Backups, Laufzeit-Tabellen, Sperre, SIGHUP (app/services/rvk_sync/swap.py)."""

import signal
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import SCHEMA_SQL
from app.services.rvk_sync import swap

NOW = datetime(2026, 12, 2, 3, 0, 0, tzinfo=UTC)


def _config(tmp_path: Path) -> dict:
    data = tmp_path / "data"
    return {
        "DATABASE_PATH": str(data / "rvk.sqlite3"),
        "EMBEDDINGS_PATH": str(data / "embeddings.npy"),
        "EMBEDDINGS_META_PATH": str(data / "embeddings_meta.json"),
        "REGISTER_TERM_EMBEDDINGS_PATH": str(data / "register_term_embeddings.npy"),
        "REGISTER_TERM_META_PATH": str(data / "register_term_meta.json"),
        "SYNC_WORK_DIR": str(data / "staging"),
    }


def _write_all(paths: dict[str, Path], content: str) -> None:
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{content}:{path.name}")


def test_paths(tmp_path):
    config = _config(tmp_path)
    live = swap.live_paths(config)
    staging = swap.staging_paths(config)
    assert set(live) == set(swap.DATA_FILE_KEYS)
    assert staging["DATABASE_PATH"] == tmp_path / "data" / "staging" / "rvk.sqlite3"


def test_swap_with_backup(tmp_path):
    config = _config(tmp_path)
    live, staging = swap.live_paths(config), swap.staging_paths(config)
    _write_all(live, "alt")
    _write_all(staging, "neu")
    result = swap.swap_files(live, staging, tmp_path / "backups", "2026/2", now=NOW)
    assert result.backup_dir.name == "rvk-2026_2-20261202T030000Z"
    for path in live.values():
        assert path.read_text() == f"neu:{path.name}"
        assert (result.backup_dir / path.name).read_text() == f"alt:{path.name}"
    assert not any(p.exists() for p in staging.values())


def test_swap_error_restores(tmp_path):
    config = _config(tmp_path)
    live, staging = swap.live_paths(config), swap.staging_paths(config)
    _write_all(live, "alt")
    _write_all(staging, "neu")
    calls = []

    def flaky_replace(src, dst):
        calls.append(dst)
        if len(calls) == 3:
            raise OSError("Platte voll")
        return __import__("os").replace(src, dst)

    with pytest.raises(OSError):
        swap.swap_files(
            live,
            staging,
            tmp_path / "backups",
            "2026/2",
            now=NOW,
            replace=flaky_replace,
        )
    for path in live.values():
        assert path.read_text() == f"alt:{path.name}"


def test_swap_needs_all_staging_files(tmp_path):
    config = _config(tmp_path)
    live, staging = swap.live_paths(config), swap.staging_paths(config)
    _write_all(live, "alt")
    with pytest.raises(FileNotFoundError):
        swap.swap_files(live, staging, tmp_path / "backups", "2026/2", now=NOW)
    assert not (tmp_path / "backups").exists()


def test_backups_keep_last_two(tmp_path):
    names = [
        "rvk-2026_1-20260902T030000Z",
        "rvk-2026_2-20261002T030000Z",
        "rvk-2026_3-20261102T030000Z",
        "anderes",
    ]
    assert swap.backups_to_remove(names, 2) == ["rvk-2026_1-20260902T030000Z"]
    assert swap.backups_to_remove(names, 0) == names[2::-1]
    root = tmp_path / "backups"
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "rvk.sqlite3").write_text("x")
    assert swap.prune_backups(root, 2) == ["rvk-2026_1-20260902T030000Z"]
    assert sorted(p.name for p in root.iterdir()) == sorted(names[1:])


def test_prune_raw_dumps(tmp_path):
    for name in ("rvko_marcxml_2026_2.xml", "rvko_marcxml_2026_3.xml", "anderes.xml"):
        (tmp_path / name).write_text("x")
    assert swap.prune_raw_dumps(tmp_path, "rvko_marcxml_2026_3.xml") == [
        "rvko_marcxml_2026_2.xml"
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "anderes.xml",
        "rvko_marcxml_2026_3.xml",
    ]


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    return conn


def test_copy_database_and_carry_over_runtime_tables(tmp_path):
    live_path, staging_path = tmp_path / "live.sqlite3", tmp_path / "staging.sqlite3"
    live = _db(live_path)
    live.execute("INSERT INTO notation (notation, label) VALUES ('ZL', 'Maschinenbau')")
    live.execute(
        "INSERT INTO gnd_cache VALUES ('k1', '{\"alt\": 1}', '2026-11-01T00:00:00')"
    )
    live.execute("INSERT INTO daily_usage (day, claude_calls) VALUES ('2026-12-01', 3)")
    live.commit()

    swap.copy_database(live_path, staging_path)
    staging = sqlite3.connect(staging_path)
    assert staging.execute("SELECT label FROM notation").fetchone() == ("Maschinenbau",)
    staging.close()

    # Während des Abgleichs schreibt die App weiter in die Live-DB
    live.execute(
        "INSERT OR REPLACE INTO gnd_cache VALUES ('k1', '{\"neu\": 1}', '2026-12-02T00:00:00')"
    )
    live.execute(
        "INSERT INTO gnd_cache VALUES ('k2', '{\"x\": 2}', '2026-12-02T00:00:00')"
    )
    live.execute(
        "INSERT INTO expansion_cache VALUES ('getriebe', '[]', '2026-12-02T00:00:00')"
    )
    live.execute("UPDATE daily_usage SET claude_calls = 9 WHERE day = '2026-12-01'")
    live.execute("INSERT INTO daily_usage (day, claude_calls) VALUES ('2026-12-02', 1)")
    live.commit()
    live.close()

    counts = swap.carry_over_runtime_tables(live_path, staging_path)
    assert counts == {"expansion_cache": 1, "gnd_cache": 2, "daily_usage": 2}
    staging = sqlite3.connect(staging_path)
    assert dict(
        staging.execute("SELECT request_key, response_json FROM gnd_cache")
    ) == {
        "k1": '{"neu": 1}',
        "k2": '{"x": 2}',
    }
    # daily_usage ist der LLM-Tageszähler der RVK-DB – nicht die Nutzungsstatistik
    assert dict(staging.execute("SELECT day, claude_calls FROM daily_usage")) == {
        "2026-12-01": 9,
        "2026-12-02": 1,
    }
    tables = {r[0] for r in staging.execute("SELECT name FROM sqlite_master")}
    assert "usage_daily" not in tables
    assert staging.execute("SELECT COUNT(*) FROM expansion_cache").fetchone()[0] == 1
    staging.close()


def test_carry_over_creates_missing_table(tmp_path):
    live_path, staging_path = tmp_path / "live.sqlite3", tmp_path / "staging.sqlite3"
    live = _db(live_path)
    live.execute("INSERT INTO daily_usage (day, claude_calls) VALUES ('2026-12-01', 4)")
    live.commit()
    live.close()
    sqlite3.connect(staging_path).close()  # leere DB ohne Tabellen
    counts = swap.carry_over_runtime_tables(live_path, staging_path)
    assert counts["daily_usage"] == 1


def test_lock_blocks_second_run(tmp_path):
    lock = tmp_path / "sync.lock"
    assert swap.is_locked(lock) is False
    with swap.SyncLock(lock):
        assert swap.is_locked(lock) is True
        with pytest.raises(swap.AlreadyRunning), swap.SyncLock(lock):
            pass
    assert swap.is_locked(lock) is False


def test_sighup_to_gunicorn(tmp_path):
    pid_file = tmp_path / "gunicorn.pid"
    pid_file.write_text("4242\n")
    sent = []
    assert swap.signal_gunicorn(pid_file, kill=lambda pid, sig: sent.append((pid, sig)))
    assert sent == [(4242, signal.SIGHUP)]


def test_missing_pid_file_is_only_logged(tmp_path):
    sent = []
    assert (
        swap.signal_gunicorn(tmp_path / "fehlt.pid", kill=lambda *a: sent.append(a))
        is False
    )
    assert sent == []


def test_kill_error_is_reported(tmp_path):
    pid_file = tmp_path / "gunicorn.pid"
    pid_file.write_text("4242")

    def denied(pid, sig):
        raise PermissionError("nicht erlaubt")

    assert swap.signal_gunicorn(pid_file, kill=denied) is False


def test_clear_staging_only_known_files(tmp_path):
    keep = tmp_path / "andere.txt"
    keep.write_text("bleibt")
    target = tmp_path / "rvk.sqlite3"
    target.write_text("x")
    swap.clear_staging([target, tmp_path / "fehlt.npy"])
    assert not target.exists()
    assert keep.exists()
