"""Lädt den GND-Abzug „Sachbegriffe“ der DNB (CC0) und das Sachgruppen-Vokabular (P8).

Quelle (geprüft am 12.11.2026): https://data.dnb.de/opendata/ verlinkt
„authorities-gnd-sachbegriff_lds_<JJJJMMTT>.jsonld.gz“ (JSON-LD, „UTF-8 decomposed“). Das Datum
im Dateinamen ist zugleich die Version. Die Sachgruppen-Bezeichnungen stehen in
https://d-nb.info/standards/vocab/gnd/gnd-sc.nt (N-Triples, CC0).

Aufruf:
    python scripts/fetch_gnd_dump.py           # lädt nur, wenn es eine neuere Version gibt
    python scripts/fetch_gnd_dump.py --force   # lädt in jedem Fall
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config

DUMP_RE = re.compile(r"authorities-gnd-sachbegriff_lds_(\d{8})\.jsonld\.gz")
USER_AGENT = "RVKompass"


def find_latest_dump(index_html: str) -> tuple[str, str] | None:
    """(Dateiname, Version) des neuesten datierten Abzugs auf der Verzeichnisseite."""
    found = {m.group(1): m.group(0) for m in DUMP_RE.finditer(index_html or "")}
    if not found:
        return None
    version = max(found)
    return found[version], version


def current_gnd_version(db_path: str) -> str | None:
    """Version des lokal geladenen Abzugs (Tabelle gnd_meta) oder None."""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT version FROM gnd_meta ORDER BY loaded_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def download(url: str, target: Path, timeout: float, http_get: Callable) -> Path:
    """Lädt in eine .part-Datei und benennt erst nach vollständigem Download um."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with http_get(
        url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as resp:
        resp.raise_for_status()
        with open(part, "wb") as fh:
            shutil.copyfileobj(resp.raw, fh)
    part.replace(target)
    return target


def fetch(
    config,
    force: bool = False,
    http_get: Callable = requests.get,
) -> dict:
    """Ermittelt die neueste Version und lädt Abzug und Vokabular nach data/raw/gnd/.

    Rückgabe: {"status": "unchanged"|"downloaded", "version", "dump_path", "vocab_path"}.
    Netz- und HTTP-Fehler werden als Ausnahme weitergegeben (der Aufrufer meldet sie).
    """
    timeout = config.GND_DOWNLOAD_TIMEOUT_SECONDS
    resp = http_get(
        config.GND_DUMP_INDEX_URL, timeout=30, headers={"User-Agent": USER_AGENT}
    )
    resp.raise_for_status()
    latest = find_latest_dump(resp.text)
    if latest is None:
        raise RuntimeError(
            "Kein Abzug „authorities-gnd-sachbegriff_lds_<Datum>.jsonld.gz“ auf "
            f"{config.GND_DUMP_INDEX_URL} gefunden."
        )
    filename, version = latest
    raw_dir = Path(config.SYNC_RAW_DIR) / "gnd"
    dump_path = raw_dir / filename
    vocab_path = raw_dir / "gnd-sc.nt"
    if not force and current_gnd_version(config.GND_DATABASE_PATH) == version:
        return {
            "status": "unchanged",
            "version": version,
            "dump_path": dump_path,
            "vocab_path": vocab_path,
        }
    base = config.GND_DUMP_INDEX_URL.rstrip("/") + "/"
    if force or not dump_path.is_file():
        download(base + filename, dump_path, timeout, http_get)
    download(config.GND_SC_VOCAB_URL, vocab_path, timeout, http_get)
    return {
        "status": "downloaded",
        "version": version,
        "dump_path": dump_path,
        "vocab_path": vocab_path,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GND-Sachbegriffe laden")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--peek",
        type=int,
        metavar="ZEICHEN",
        help="nur die ersten ZEICHEN Zeichen des vorhandenen Abzugs zeigen (Formatprüfung)",
    )
    parser.add_argument(
        "--peek-vocab",
        metavar="CODE",
        help="Zeilen des Sachgruppen-Vokabulars zu einem Code zeigen (Formatprüfung)",
    )
    args = parser.parse_args(argv)
    if args.peek_vocab:
        from app.services.gnd_local import parse_gnd_sc_ntriples

        vocab = Path(get_config().SYNC_RAW_DIR) / "gnd" / "gnd-sc.nt"
        lines = vocab.read_text(encoding="utf-8").splitlines()
        needle = f"#{args.peek_vocab}>"
        for line in lines:
            if needle in line.split(" ", 1)[0]:
                print(line)
        labels = parse_gnd_sc_ntriples(lines)
        print(f"{len(lines)} Zeilen, {len(labels)} Codes mit deutscher Bezeichnung")
        print(f"{args.peek_vocab}: {labels.get(args.peek_vocab)!r}")
        return 0
    if args.peek:
        import gzip

        dumps = sorted((Path(get_config().SYNC_RAW_DIR) / "gnd").glob("*.jsonld.gz"))
        if not dumps:
            print("Kein Abzug vorhanden.", file=sys.stderr)
            return 1
        from collections import Counter

        from app.services.gnd_local import iter_json_objects

        key_counts: Counter = Counter()
        shown = 0
        with gzip.open(dumps[-1], "rt", encoding="utf-8") as fh:
            for obj in iter_json_objects(fh):
                if not str(obj.get("@id", "")).startswith("https://d-nb.info/gnd/"):
                    continue
                key_counts.update(obj.keys())
                shown += 1
                if shown <= 2:
                    print(json.dumps(obj, ensure_ascii=False, indent=1))
                if shown >= args.peek:
                    break
        print(f"Schlüssel in den ersten {shown} Hauptknoten:")
        for key, count in key_counts.most_common():
            print(f"  {count:6d}  {key}")
        return 0
    result = fetch(get_config(), force=args.force)
    print(f"GND-Abzug {result['version']}: {result['status']} ({result['dump_path']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
