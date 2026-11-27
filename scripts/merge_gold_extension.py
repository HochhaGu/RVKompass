"""Prüft eine Goldstandard-Erweiterung gegen den RVK-Stand und hängt sie an (P12).

Schritt 1 (ohne --write): Prüfliste docs/goldstandard/pruefliste-2026-11.md schreiben –
Übersicht mit Zahlen, a) fehlende Notationen mit Vermutung, b) Hauptgruppen-Ausreißer,
c) alle übrigen Zeilen. Die Datenbank wird nur lesend geöffnet (file:…?mode=ro).

Schritt 2 (--write): Die Zeilen der Erweiterung in Dateireihenfolge an eval/goldstandard.csv
anhängen. Die bisherigen Zeilen bleiben byte-gleich. Fehlende Notationen werden aus der Zeile
genommen und in anmerkung vermerkt; bleibt keine übrig, geht die Zeile (neu geschrieben, mit
allen Original-Notationen und Vermerk) nach eval/goldstandard_offen.csv. Danach wird das
Ergebnis erneut geprüft und bestätigt, dass jede Anfrage der Erweiterung in einer der beiden
Dateien steht. Die Erweiterungsdatei löscht das Skript nicht.

Doppelte Anfragen (in der Erweiterung oder gegen den Goldstandard) brechen ab: Welche Zeile
gilt, entscheidet Günther.

Aufruf:
    python scripts/merge_gold_extension.py
    python scripts/merge_gold_extension.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services import evaluation, gold_merge, rvk_repository

DEFAULT_EXTENSION = "eval/goldstandard_erweiterung_2026-11.csv"
DEFAULT_GOLD = "eval/goldstandard.csv"
DEFAULT_OPEN = "eval/goldstandard_offen.csv"
DEFAULT_CHECKLIST = "docs/goldstandard/pruefliste-2026-11.md"
# Letzte Anfrage der Technik-Teilliste in der Erweiterung (danach: andere Fachgebiete)
DEFAULT_TECHNIK_LAST = "Firmware"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Goldstandard-Erweiterung prüfen und anhängen"
    )
    parser.add_argument("--extension", default=DEFAULT_EXTENSION)
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--open-file", default=DEFAULT_OPEN)
    parser.add_argument("--checklist", default=DEFAULT_CHECKLIST)
    parser.add_argument("--db", help="RVK-Datenbank (Standard: DATABASE_PATH)")
    parser.add_argument("--technik-last", default=DEFAULT_TECHNIK_LAST)
    parser.add_argument(
        "--write", action="store_true", help="Zeilen anhängen (sonst nur Prüfliste)"
    )
    return parser.parse_args(argv)


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def check_extension(conn, old_entries, ext_entries) -> dict:
    """Prüfergebnis: Zeilenprüfungen, vorhandene Notationen, Vermutungen, RVK-Stand."""
    notations = {n for e in ext_entries for n in e.expected}
    present = gold_merge.lookup_notations(conn, notations)
    checks = gold_merge.check_rows(ext_entries, present)
    missing = sorted({n for c in checks for n in c.missing})
    candidates = {n: gold_merge.notation_candidates(n) for n in missing}
    found = gold_merge.lookup_notations(
        conn, {c for cands in candidates.values() for c in cands}
    )
    guesses = {n: gold_merge.unique_guess(n, found) for n in missing}
    return {
        "present": {**present, **found},
        "checks": checks,
        "guesses": guesses,
        "rvk_version": rvk_repository.latest_rvk_version(conn) or "unbekannt",
        "duplicates": gold_merge.find_duplicates(old_entries, ext_entries),
    }


def subset_sizes(n_old: int, ext_entries, statuses, technik_last: str) -> dict:
    """GOLD_SUBSET_SIZES nach der Übernahme (Zeilen nach „offen“ zählen nicht)."""
    last_line = next((e.line for e in ext_entries if e.query == technik_last), None)
    if last_line is None:
        raise ValueError(f"Letzte Technik-Anfrage {technik_last!r} nicht gefunden")
    technik = other = 0
    for entry, status in zip(ext_entries, statuses, strict=True):
        if status == "open":
            continue
        if entry.line <= last_line:
            technik += 1
        else:
            other += 1
    return {"alt": n_old, "neu_technik": technik, "neu_andere": other}


def main(argv=None) -> int:
    args = parse_args(argv)
    config = get_config()
    ext_path = _resolve(args.extension)
    gold_path = _resolve(args.gold)
    open_path = _resolve(args.open_file)
    checklist_path = _resolve(args.checklist)
    db_path = args.db or config.DATABASE_PATH

    if not ext_path.is_file():
        print(f"Fehler: Erweiterung {ext_path} nicht gefunden.", file=sys.stderr)
        return 1
    ext_lines = ext_path.read_text(encoding="utf-8").splitlines()
    old_bytes = gold_path.read_bytes()
    try:
        ext_entries = evaluation.parse_gold(ext_lines)
        old_entries = evaluation.parse_gold(old_bytes.decode("utf-8").splitlines())
    except ValueError as exc:
        print(f"Fehler beim Einlesen: {exc}", file=sys.stderr)
        return 1
    ext_header = next(line for line in ext_lines if line.strip())
    old_header = old_bytes.decode("utf-8").splitlines()[0]
    if ext_header.lstrip("﻿") != old_header.lstrip("﻿"):
        print(
            "Fehler: Kopfzeilen der beiden Dateien unterscheiden sich.", file=sys.stderr
        )
        return 1

    conn = rvk_repository.get_readonly_connection(db_path)
    if conn is None:
        print(f"Fehler: Datenbank {db_path} nicht lesbar.", file=sys.stderr)
        return 1
    try:
        result = check_extension(conn, old_entries, ext_entries)
    finally:
        conn.close()

    data = gold_merge.ChecklistData(
        checks=result["checks"],
        present=result["present"],
        guesses=result["guesses"],
        rvk_version=result["rvk_version"],
        source_file=args.extension,
        duplicates=result["duplicates"],
    )
    summary = gold_merge.checklist_summary(data)
    if result["duplicates"]:
        print("Doppelte Anfragen – nichts übernommen:", file=sys.stderr)
        for d in result["duplicates"]:
            print(f"  {json.dumps(d, ensure_ascii=False)}", file=sys.stderr)
        return 2

    checklist_path.parent.mkdir(parents=True, exist_ok=True)
    checklist_path.write_text(gold_merge.render_checklist(data), encoding="utf-8")
    print(f"Prüfliste: {checklist_path}")
    print(json.dumps(summary, ensure_ascii=False))
    old_errors = gold_merge.format_errors(old_entries)
    if old_errors:
        print(f"Warnung: formal ungültige Notationen im alten Teil: {old_errors}")

    if not args.write:
        return 0

    plan = gold_merge.plan_merge(ext_lines, result["checks"], result["rvk_version"])
    new_bytes = gold_merge.append_bytes(old_bytes, plan.append_lines)
    # Prüfung vor dem Schreiben: Ergebnis lesbar, keine fehlenden Notationen im neuen Teil
    merged = evaluation.parse_gold(new_bytes.decode("utf-8").splitlines())
    new_part = merged[len(old_entries) :]
    conn = rvk_repository.get_readonly_connection(db_path)
    try:
        present = gold_merge.lookup_notations(
            conn, {n for e in new_part for n in e.expected}
        )
    finally:
        conn.close()
    still_missing = [n for e in new_part for n in e.expected if n not in present]
    if still_missing or gold_merge.format_errors(new_part):
        print(f"Fehler: nach dem Kürzen fehlen noch {still_missing}", file=sys.stderr)
        return 1
    merged_keys = {gold_merge.query_key(e.query) for e in merged}
    open_entries = (
        evaluation.parse_gold([plan.header, *plan.open_lines])
        if plan.open_lines
        else []
    )
    open_keys = {gold_merge.query_key(e.query) for e in open_entries}
    uncovered = [
        e.query
        for e in ext_entries
        if gold_merge.query_key(e.query) not in merged_keys | open_keys
    ]
    if uncovered or merged_keys & open_keys:
        print(
            f"Fehler: Anfragen nicht eindeutig verteilt: {uncovered}", file=sys.stderr
        )
        return 1

    gold_path.write_bytes(new_bytes)
    if plan.open_lines:
        open_path.write_bytes(gold_merge.open_file_bytes(plan.header, plan.open_lines))
        print(f"Offen: {open_path} ({len(plan.open_lines)} Zeilen)")
    sizes = subset_sizes(
        len(old_entries), ext_entries, plan.statuses, args.technik_last
    )
    print(f"Goldstandard: {gold_path} ({len(merged)} Einträge)")
    print("Jede Anfrage der Erweiterung steht im Goldstandard oder in „offen“: ja")
    print(f"GOLD_SUBSET_SIZES = {json.dumps(sizes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
