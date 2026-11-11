"""Sammelt Kandidatenzeilen für spätere Erweiterungen des Goldstandards aus K10plus-SRU.

Quelle und Lizenz (geprüft am 11.11.2026):
- K10plus-SRU, Datenbank opac-de-627: https://sru.k10plus.de/opac-de-627
  (Doku: https://wiki.k10plus.de/display/K10PLUS/SRU). Frei abfragbar, kein Konto, kein
  institutioneller Zugang nötig.
- Lizenz: „Die Katalogdaten werden der Öffentlichkeit unter Maßgabe der Creative Commons Lizenz
  CC0 veröffentlicht.“ (https://wiki.k10plus.de/display/K10PLUS/Open+Data). Lokaldaten sind
  ausgenommen; dieses Skript nutzt nur Titeldaten (PPN, Titel, RVK-Notation aus MARC 084).

Wichtig: Das Ergebnis sind nur KANDIDATEN. Eine Zeile wird erst nach manueller Prüfung in
RVK-Online von Hand in eval/goldstandard.csv übernommen. Dieses Skript schreibt nie in
eval/goldstandard.csv und überschreibt keine vorhandene Kandidatendatei.

Aufruf:
    python scripts/build_gold_candidates.py --terms begriffe.txt
    python scripts/build_gold_candidates.py --terms begriffe.txt --out /tmp/kandidaten.csv

begriffe.txt: ein Suchbegriff pro Zeile, Zeilen mit # sind Kommentare.
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services import gold_candidates

DEFAULT_OUT = ROOT / "eval" / "goldstandard_kandidaten.csv"
PROTECTED_GOLD = ROOT / "eval" / "goldstandard.csv"
TIMEOUT = (5, 30)
USER_AGENT = "RVKompass-gold-candidates"


def is_protected(path: Path) -> bool:
    """Der handkuratierte Goldstandard darf nie Ziel dieses Skripts sein."""
    if path.name.lower() == PROTECTED_GOLD.name:
        return True
    try:
        return path.resolve() == PROTECTED_GOLD.resolve()
    except OSError:
        return False


def free_path(path: Path) -> Path:
    """Gibt es die Datei schon, wird eine Datei mit Zeitstempel daneben angelegt."""
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{stamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def read_terms(path: Path) -> list[str]:
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def fetch_records(term: str, max_records: int) -> list[gold_candidates.Record]:
    url = gold_candidates.build_sru_url(term, max_records)
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return gold_candidates.parse_marcxml_records(response.content)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Kandidatenzeilen für den Goldstandard aus K10plus-SRU (CC0) sammeln"
    )
    parser.add_argument(
        "--terms", required=True, help="Datei mit einem Suchbegriff pro Zeile"
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help="Kandidatendatei (CSV, ;)"
    )
    parser.add_argument("--max-records", type=int, default=50, help="Titel je Begriff")
    parser.add_argument(
        "--min-count", type=int, default=2, help="Mindestzahl Titel je Notation"
    )
    parser.add_argument(
        "--top", type=int, default=3, help="höchstens so viele Notationen je Begriff"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Pause zwischen Anfragen (s)"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    if is_protected(out):
        print(
            "Abbruch: Dieses Skript schreibt nie in den Goldstandard (goldstandard.csv). "
            "Bitte eine eigene Kandidatendatei angeben.",
            file=sys.stderr,
        )
        return 2

    terms_path = Path(args.terms)
    if not terms_path.is_file():
        print(f"Fehler: Begriffsdatei {terms_path} nicht gefunden.", file=sys.stderr)
        return 1
    terms = read_terms(terms_path)

    rows: list[str] = []
    failed: list[str] = []
    for index, term in enumerate(terms):
        if index and args.delay > 0:
            time.sleep(args.delay)
        try:
            records = fetch_records(term, args.max_records)
        except (requests.RequestException, ET.ParseError) as exc:
            print(
                f"Warnung: Abfrage für {term!r} fehlgeschlagen: {exc}", file=sys.stderr
            )
            failed.append(term)
            continue
        candidates = gold_candidates.aggregate_candidates(
            records, args.min_count, args.top
        )
        rows += gold_candidates.candidate_rows(term, candidates, len(records))
        print(f"{term}: {len(records)} Titel, {len(candidates)} Kandidat(en)")

    target = free_path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join([gold_candidates.CANDIDATE_HEADER, *rows]) + "\n", encoding="utf-8"
    )
    print(
        f"Kandidatendatei: {target} ({len(rows)} Zeilen) – vor Übernahme in RVK-Online prüfen."
    )
    if failed:
        print(f"Ohne Ergebnis wegen Fehlern: {', '.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
