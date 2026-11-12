"""GND-Download und -Aktualisierung (scripts/fetch_gnd_dump.py, scripts/sync_gnd.py), ohne Netz."""

import gzip
import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import gnd_local

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "gnd_sachbegriff_nachgebaut.jsonld"
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fetch_gnd_dump = _load("fetch_gnd_dump")
sync_gnd = _load("sync_gnd")

INDEX_HTML = """
<a href="authorities-gnd-sachbegriff_lds.jsonld.gz">aktuell</a>
<a href="authorities-gnd-sachbegriff_lds_20260713.jsonld.gz">alt</a>
<a href="authorities-gnd-sachbegriff_lds_20261013.jsonld.gz">neu</a>
<a href="authorities-gnd-person_lds_20261013.jsonld.gz">anderes</a>
"""


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text = text
        self.raw = __import__("io").BytesIO(content)

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _config(tmp_path, **extra):
    values = {
        "GND_DUMP_INDEX_URL": "https://data.dnb.de/opendata/",
        "GND_SC_VOCAB_URL": "https://d-nb.info/standards/vocab/gnd/gnd-sc.nt",
        "GND_DOWNLOAD_TIMEOUT_SECONDS": 5,
        "GND_DATABASE_PATH": str(tmp_path / "data" / "gnd.sqlite3"),
        "SYNC_RAW_DIR": str(tmp_path / "data" / "raw"),
        "SYNC_WORK_DIR": str(tmp_path / "data" / "staging"),
        "SYNC_MAX_COUNT_DEVIATION": 0.05,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_find_latest_dump():
    assert fetch_gnd_dump.find_latest_dump(INDEX_HTML) == (
        "authorities-gnd-sachbegriff_lds_20261013.jsonld.gz",
        "20261013",
    )
    assert fetch_gnd_dump.find_latest_dump("nichts") is None


def _http(calls):
    gz = gzip.compress(FIXTURE.read_bytes())

    def http_get(url, **kwargs):
        calls.append(url)
        assert "timeout" in kwargs
        if url.endswith("/opendata/"):
            return FakeResponse(text=INDEX_HTML)
        if url.endswith(".nt"):
            return FakeResponse(content=b"")
        return FakeResponse(content=gz)

    return http_get


def test_fetch_downloads_and_stops_on_same_version(tmp_path):
    config = _config(tmp_path)
    calls = []
    result = fetch_gnd_dump.fetch(config, http_get=_http(calls))
    assert result["status"] == "downloaded"
    assert result["dump_path"].is_file()
    assert any(c.endswith("_20261013.jsonld.gz") for c in calls)
    # lokale DB mit derselben Version -> keine weiteren Downloads
    Path(config.GND_DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.GND_DATABASE_PATH)
    gnd_local.create_schema(conn)
    gnd_local.write_meta(conn, "20261013", "x", 5)
    conn.close()
    calls.clear()
    assert fetch_gnd_dump.fetch(config, http_get=_http(calls))["status"] == "unchanged"
    assert calls == ["https://data.dnb.de/opendata/"]


def test_run_builds_and_replaces(tmp_path):
    config = _config(tmp_path)
    result = sync_gnd.run(
        config, fetch=lambda c, force=False: fetch_gnd_dump.fetch(c, force, _http([]))
    )
    assert result["status"] == "replaced"
    assert result["record_count"] == 5
    conn = sqlite3.connect(config.GND_DATABASE_PATH)
    assert gnd_local.read_meta(conn)["version"] == "20261013"
    conn.close()
    assert not (tmp_path / "data" / "staging" / "gnd.sqlite3").exists()


def test_run_rejects_large_deviation_and_keeps_old(tmp_path):
    config = _config(tmp_path)
    db = Path(config.GND_DATABASE_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    gnd_local.create_schema(conn)
    gnd_local.write_meta(conn, "20260713", "alt", 1000)
    conn.close()
    result = sync_gnd.run(
        config, fetch=lambda c, force=False: fetch_gnd_dump.fetch(c, force, _http([]))
    )
    assert result["status"] == "rejected"
    conn = sqlite3.connect(db)
    assert gnd_local.read_meta(conn)["version"] == "20260713"
    conn.close()


def test_run_error_keeps_old_file(tmp_path):
    config = _config(tmp_path)
    db = Path(config.GND_DATABASE_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"alter Stand")

    def broken_fetch(c, force=False):
        raise ConnectionError("DNB nicht erreichbar")

    with pytest.raises(ConnectionError):
        sync_gnd.run(config, fetch=broken_fetch)
    assert db.read_bytes() == b"alter Stand"


def test_main_mails_on_error(tmp_path, monkeypatch):
    config = _config(tmp_path, MAIL_FALLBACK_DIR=str(tmp_path / "logs"))
    monkeypatch.setattr(sync_gnd, "get_config", lambda: config)

    def broken(*a, **k):
        raise ConnectionError("DNB nicht erreichbar")

    monkeypatch.setattr(sync_gnd, "run", broken)
    assert sync_gnd.main([]) == 1
    (mail,) = (tmp_path / "logs").glob("sync-mail-*.html")
    assert "DNB nicht erreichbar" in mail.read_text(encoding="utf-8")
