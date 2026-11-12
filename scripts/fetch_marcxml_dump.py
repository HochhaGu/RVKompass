"""Lädt den aktuellen RVK-MarcXML-Gesamtabzug herunter (CC0, kein API-Key nötig).

Die Download-Seite verlinkt eine versionierte Datei (z.B. rvko_marcxml_2026_1.xml.gz).
Der Link wird bei jedem Lauf neu ermittelt, damit Versionswechsel automatisch erkannt werden.
Der monatliche Abgleich (scripts/monthly_sync.py) nutzt dieselben Funktionen.
"""

import gzip
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import requests

DOWNLOAD_PAGE = "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online/rvk-download"
BASE_URL = "https://rvk.uni-regensburg.de"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

FULL_DUMP_PATTERN = re.compile(r'href="(/downloads/rvko_marcxml_[^"]*?\.xml\.gz)"')
UPDATE_SUFFIX = ".update.xml.gz"
# Gleiche Regel wie app.services.rvk_repository._VERSION_RE
VERSION_PATTERN = re.compile(r"rvko_marcxml_(\d{4})_(\d+)")


def find_full_dump_url(http_get: Callable = requests.get) -> str:
    resp = http_get(DOWNLOAD_PAGE, timeout=30)
    resp.raise_for_status()
    candidates = [
        m for m in FULL_DUMP_PATTERN.findall(resp.text) if not m.endswith(UPDATE_SUFFIX)
    ]
    if not candidates:
        raise RuntimeError(
            "Kein MarcXML-Gesamtabzug-Link auf der Download-Seite gefunden."
        )
    return BASE_URL + candidates[0]


def version_from_url(url: str) -> str:
    """ ".../rvko_marcxml_2026_3.xml.gz" -> "2026/3"."""
    match = VERSION_PATTERN.search(url or "")
    if not match:
        raise ValueError(f"Kein RVK-Versionsname in {url!r}")
    return f"{match.group(1)}/{match.group(2)}"


def download_and_extract(
    url: str, raw_dir: Path = RAW_DIR, http_get: Callable = requests.get
) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    gz_path = raw_dir / Path(url).name
    xml_path = gz_path.with_suffix("")

    print(f"Lade {url} ...")
    resp = http_get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(gz_path, "wb") as f:
        shutil.copyfileobj(resp.raw, f)

    print(f"Entpacke nach {xml_path} ...")
    with gzip.open(gz_path, "rb") as f_in, open(xml_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    gz_path.unlink()
    return xml_path


def main():
    url = find_full_dump_url()
    xml_path = download_and_extract(url)
    print(f"Fertig: {xml_path} ({xml_path.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
