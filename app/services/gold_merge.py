"""Prüfen und Übernehmen einer Goldstandard-Erweiterung (P12): reine Funktionen.

Ablauf (scripts/merge_gold_extension.py):
1. Erweiterung mit `evaluation.parse_gold` einlesen, doppelte Anfragen suchen (`find_duplicates`).
2. Jede Notation formal prüfen (`evaluation.NOTATION_FORMAT_RE`) und – im Skript, nur lesend – in
   der Datenbank nachschlagen. Für fehlende Notationen eine Vermutung nur, wenn sie eindeutig ist
   (`notation_candidates`, `unique_guess`). Die Vermutung ist ein Hinweis und wird nie übernommen.
3. Hauptgruppen-Ausreißer markieren (`is_main_group_outlier`) und die Prüfliste schreiben
   (`render_checklist`).
4. Übernahme (`plan_merge`, `append_bytes`): Die alten Zeilen bleiben byte-gleich, neue Zeilen
   kommen in Dateireihenfolge dahinter.

Die fachlichen Angaben (Anfrage, Notationen, Typ, Fachgebiet) stammen von Günther und werden nicht
verändert; entfernt werden nur Notationen, die es im RVK-Stand nicht gibt (mit Vermerk).
"""

from __future__ import annotations

import csv
import io
import re
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.services import evaluation
from app.services.presentation import main_group

# ANNAHME VON CLAUDE (P12), nur für die Prüfliste und die Messung „Bedeutung@1“ in
# scripts/derive_subject_mapping.py – sie ändert nichts am Goldstandard. Fachgebiet (Spalte
# fachgebiet) -> passende RVK-Hauptgruppen, in der Schreibweise von
# Config.MAIN_GROUP_TOP_LEVEL_FALLBACK („SQ - SU“, „LD,LP - LY“). Abgeleitet aus den dortigen
# Titeln; Technikfächer erhalten den ganzen Bereich „ZG - ZS“ (Technik), dazu U (Physik),
# V (Chemie) oder W (Biologie), wo das Fach es nahelegt. AN (Buch- und Bibliothekswesen) und AP
# (Medien) liegen in „A Allgemeines“ und sind zusätzlich angenommen.
FACHGEBIET_MAIN_GROUPS: dict[str, str] = {
    # Technik
    "Maschinenbau": "ZG - ZS",
    "Werkstoffe": "ZG - ZS,U,V",
    "Elektrotechnik": "ZG - ZS,U",
    "Energietechnik": "ZG - ZS,U",
    "Bauwesen": "ZG - ZS",
    "Mess- und Regelungstechnik": "ZG - ZS",
    "Verfahrens- und Umwelttechnik": "ZG - ZS,V",
    "Fahrzeugtechnik": "ZG - ZS",
    "Lebensmitteltechnik": "ZA - ZE,ZG - ZS,V",
    "Informatik": "SQ - SU",
    "Mathematik und Physik": "SA - SP,U",
    # weitere Fachgebiete
    "Wirtschaft und Management": "Q",
    "Recht": "P",
    "Geschichte": "N",
    "Psychologie": "CL - CZ",
    "Musik": "LD,LP - LY",
    "Medizin und Pflege": "X - Y,MT",
    "Theologie und Philosophie": "B,CA - CK",
    "Sprache und Literatur": "E,F,G,H,I,K",
    "Sport": "ZX - ZY",
    "Soziologie": "MN - MS",
    "Politik und Verwaltung": "MA - ML,P",
    "Pädagogik und Bildung": "D",
    "Land- und Forstwirtschaft, Ernährung": "ZA - ZE",
    "Kunst, Medien und Kommunikation": "LD,LH - LO,AP",
    "Geographie und Raumplanung": "R",
    "Biologie und Chemie": "V,W",
    "Bibliotheks- und Informationswesen": "AN",
}

FACHGEBIET_SEPARATOR = " / "


def query_key(query: str) -> str:
    """Schlüssel für doppelte Anfragen: Leerraum zusammengefasst, casefold()."""
    return " ".join(str(query).split()).casefold()


def find_duplicates(
    old_entries: Iterable[evaluation.GoldEntry],
    new_entries: Iterable[evaluation.GoldEntry],
) -> list[dict]:
    """Doppelte Anfragen innerhalb der Erweiterung und gegen den bisherigen Goldstandard.

    Je Fund: Anfrage, Zeilen im alten Goldstandard, Zeilen in der Erweiterung.
    """
    old_lines: dict[str, list[int]] = {}
    for entry in old_entries:
        old_lines.setdefault(query_key(entry.query), []).append(entry.line)
    new_lines: dict[str, list[tuple[int, str]]] = {}
    for entry in new_entries:
        new_lines.setdefault(query_key(entry.query), []).append(
            (entry.line, entry.query)
        )
    duplicates = []
    for key, items in new_lines.items():
        if len(items) > 1 or key in old_lines:
            duplicates.append(
                {
                    "query": items[0][1],
                    "old_lines": old_lines.get(key, []),
                    "new_lines": [line for line, _ in items],
                }
            )
    # Dubletten innerhalb des alten Bestands (dürfen nicht vorkommen, werden mitgemeldet)
    for key, lines in old_lines.items():
        if len(lines) > 1 and key not in new_lines:
            duplicates.append({"query": key, "old_lines": lines, "new_lines": []})
    return duplicates


def format_errors(entries: Iterable[evaluation.GoldEntry]) -> list[dict]:
    """Notationen, die nicht zur RVK-Schreibweise passen („ZL 4100“, „PU 2345.4560“)."""
    return [
        {"query": e.query, "notation": n, "line": e.line}
        for e in entries
        for n in e.expected
        if not evaluation.NOTATION_FORMAT_RE.fullmatch(n)
    ]


# --- Hauptgruppen ------------------------------------------------------------------------------


def main_group_in_range(group: str, spec: str) -> bool:
    """Liegt die Hauptgruppe im Bereich einer Angabe wie „LD,LH - LO“, „X - Y“, „MT“ oder „A“?

    Teile sind durch Komma getrennt. Ein einzelner Code gilt für alle Hauptgruppen, die so
    beginnen („A“ -> AN, AP …; „MT“ -> MT). Ein Bereich „SQ - SU“ vergleicht die ersten Buchstaben
    der Hauptgruppe mit Anfang und Ende („X - Y“ -> XA … YZ).
    """
    group = (group or "").strip().upper()
    if not group:
        return False
    for part in (spec or "").split(","):
        part = part.strip().upper()
        if not part:
            continue
        if "-" in part:
            low, high = (p.strip() for p in part.split("-", 1))
            if low <= group[: len(low)] and group[: len(high)] <= high:
                return True
        elif group.startswith(part):
            return True
    return False


def split_fachgebiete(fachgebiet: str) -> list[str]:
    """„Elektrotechnik / Geographie und Raumplanung“ -> beide Fachgebiete."""
    return [
        p.strip() for p in (fachgebiet or "").split(FACHGEBIET_SEPARATOR) if p.strip()
    ]


def specs_for_fachgebiet(
    fachgebiet: str, table: Mapping[str, str] | None = None
) -> list[str]:
    """Bereichsangaben aller Fachgebiete einer Zeile; unbekannte Fachgebiete fehlen."""
    table = FACHGEBIET_MAIN_GROUPS if table is None else table
    return [table[f] for f in split_fachgebiete(fachgebiet) if f in table]


def matches_fachgebiet(
    notation: str, fachgebiet: str, table: Mapping[str, str] | None = None
) -> bool:
    group = main_group(notation)
    return any(
        main_group_in_range(group, spec)
        for spec in specs_for_fachgebiet(fachgebiet, table)
    )


def is_main_group_outlier(
    entry: evaluation.GoldEntry, table: Mapping[str, str] | None = None
) -> bool | None:
    """Wahr, wenn keine Notation der Zeile in einer zum Fachgebiet passenden Hauptgruppe liegt.

    None, wenn kein Fachgebiet der Zeile in der Tabelle steht (dann nicht prüfbar).
    """
    if not specs_for_fachgebiet(entry.fachgebiet, table):
        return None
    return not any(
        matches_fachgebiet(n, entry.fachgebiet, table) for n in entry.expected
    )


# --- Vermutungen für fehlende Notationen -------------------------------------------------------


def _split_notation(notation: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([A-Z]{1,3}) ([\d.]+)", notation)
    return (match.group(1), match.group(2)) if match else None


def notation_candidates(notation: str) -> set[str]:
    """Naheliegende Tippfehler-Varianten einer Notation (ohne die Notation selbst).

    Gleiche Hauptgruppe und ein Ziffernunterschied (zwei benachbarte Ziffern vertauscht, eine
    Ziffer mehr oder weniger) oder gleiche Zahl und eine Hauptgruppe, die sich in genau einem
    Buchstaben unterscheidet.
    """
    parts = _split_notation(evaluation.normalize_notation(notation))
    if parts is None:
        return set()
    group, number = parts
    numbers: set[str] = set()
    for i in range(len(number) - 1):
        a, b = number[i], number[i + 1]
        if a.isdigit() and b.isdigit() and a != b:
            numbers.add(number[:i] + b + a + number[i + 2 :])
    for i, ch in enumerate(number):
        if ch.isdigit():
            numbers.add(number[:i] + number[i + 1 :])
    for i in range(len(number) + 1):
        for digit in string.digits:
            numbers.add(number[:i] + digit + number[i:])
    result = {
        f"{group} {n}"
        for n in numbers
        if n and n[0] != "." and n[-1] != "." and ".." not in n
    }
    for i in range(len(group)):
        for letter in string.ascii_uppercase:
            if letter != group[i]:
                result.add(f"{group[:i]}{letter}{group[i + 1 :]} {number}")
    result.discard(f"{group} {number}")
    return result


def unique_guess(notation: str, present: Iterable[str]) -> str | None:
    """Die einzige vorhandene Variante aus `notation_candidates`, sonst None."""
    found = notation_candidates(notation) & set(present)
    return next(iter(found)) if len(found) == 1 else None


# --- Datenbank (nur lesend, Verbindung kommt vom Aufrufer) -------------------------------------


def lookup_notations(conn, notations: Iterable[str]) -> dict[str, dict]:
    """Benennung und Hierarchiepfad der vorhandenen Notationen (fehlende fehlen im Ergebnis)."""
    wanted = sorted(set(notations))
    found: dict[str, dict] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start : start + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT notation, label, hierarchy_path FROM notation "
            f"WHERE notation IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            found[row[0]] = {"label": row[1] or "", "hierarchy_path": row[2] or ""}
    return found


# --- Prüfergebnis ------------------------------------------------------------------------------


@dataclass
class RowCheck:
    entry: evaluation.GoldEntry
    missing: list[str]
    invalid: list[str]
    outlier: bool | None

    @property
    def removed(self) -> list[str]:
        """Notationen, die aus der Zeile genommen werden (ungültig oder nicht im RVK-Stand)."""
        return [
            n for n in self.entry.expected if n in self.invalid or n in self.missing
        ]

    @property
    def kept(self) -> list[str]:
        return [n for n in self.entry.expected if n not in self.removed]

    @property
    def status(self) -> str:
        if not self.removed:
            return "unchanged"
        return "trimmed" if self.kept else "open"


def check_rows(
    entries: Iterable[evaluation.GoldEntry], present: Mapping[str, dict]
) -> list[RowCheck]:
    checks = []
    for entry in entries:
        invalid = [
            n for n in entry.expected if not evaluation.NOTATION_FORMAT_RE.fullmatch(n)
        ]
        missing = [n for n in entry.expected if n not in present and n not in invalid]
        checks.append(RowCheck(entry, missing, invalid, is_main_group_outlier(entry)))
    return checks


@dataclass
class ChecklistData:
    checks: list[RowCheck]
    present: Mapping[str, dict]
    guesses: Mapping[str, str | None]
    rvk_version: str
    source_file: str
    duplicates: list[dict] = field(default_factory=list)


def vermerk(removed: Iterable[str], rvk_version: str) -> str:
    return f"nicht im RVK-Stand {rvk_version}: {', '.join(removed)}, siehe Prüfliste"


def _cell(text) -> str:
    return evaluation._cell(text)


def _notation_text(notation: str, present: Mapping[str, dict]) -> str:
    info = present.get(notation)
    if info is None:
        return f"{notation} (nicht im RVK-Stand)"
    return f"{notation} ({info['label'] or 'ohne Benennung'})"


def checklist_summary(data: ChecklistData) -> dict:
    checks = data.checks
    notations = [n for c in checks for n in c.entry.expected]
    return {
        "rows": len(checks),
        "notations": len(notations),
        "found": sum(1 for n in notations if n in data.present),
        "missing": sum(len(c.missing) + len(c.invalid) for c in checks),
        "rows_with_missing": sum(1 for c in checks if c.removed),
        "outliers": sum(1 for c in checks if c.outlier),
        "not_checkable": sum(1 for c in checks if c.outlier is None),
        "unchanged": sum(1 for c in checks if c.status == "unchanged"),
        "trimmed": sum(1 for c in checks if c.status == "trimmed"),
        "open": sum(1 for c in checks if c.status == "open"),
        "duplicates": len(data.duplicates),
    }


def render_checklist(data: ChecklistData) -> str:
    """Prüfliste für Günther: Übersicht mit Zahlen, dann Abschnitte a), b), c)."""
    s = checklist_summary(data)
    lines = [
        "# Prüfliste Goldstandard-Erweiterung 2026-11",
        "",
        (
            f"Eingang: `{data.source_file}`. Geprüft gegen den RVK-Stand **{data.rvk_version}** "
            "(Datenbank nur lesend geöffnet). Erstellt mit `scripts/merge_gold_extension.py`."
        ),
        "",
        "## Übersicht",
        "",
        "| Angabe | Zahl |",
        "|---|---|",
        f"| Zeilen gesamt | {s['rows']} |",
        f"| Notationen gesamt | {s['notations']} |",
        f"| davon im RVK-Stand gefunden | {s['found']} |",
        f"| fehlende oder formal ungültige Notationen | {s['missing']} |",
        f"| betroffene Zeilen | {s['rows_with_missing']} |",
        f"| Zeilen mit Hauptgruppen-Ausreißer | {s['outliers']} |",
        f"| Zeilen ohne prüfbares Fachgebiet | {s['not_checkable']} |",
        f"| doppelte Anfragen | {s['duplicates']} |",
        f"| unverändert übernommen | {s['unchanged']} |",
        f"| übernommen mit gekürzten Notationen | {s['trimmed']} |",
        f"| nach `eval/goldstandard_offen.csv` (keine gültige Notation übrig) | {s['open']} |",
        "",
        "## a) Notationen, die es im RVK-Stand nicht gibt",
        "",
        (
            "Diese Notationen wurden aus der Zeile genommen und in der Spalte anmerkung vermerkt. "
            "Die Vermutung ist nur ein Hinweis (genau eine vorhandene Notation mit einem "
            "Ziffern- oder Buchstabenunterschied) und wurde **nicht** übernommen."
        ),
        "",
    ]
    removed_rows = [c for c in data.checks if c.removed]
    if removed_rows:
        lines += ["| Zeile | Anfrage | Notation | Vermutung |", "|---|---|---|---|"]
        for c in removed_rows:
            for n in c.removed:
                if n in c.invalid:
                    guess = "formal ungültig – keine Vermutung"
                else:
                    g = data.guesses.get(n)
                    guess = (
                        _notation_text(g, data.present)
                        if g
                        else "keine eindeutige Vermutung"
                    )
                lines.append(
                    f"| {c.entry.line} | {_cell(c.entry.query)} | {_cell(n)} | {_cell(guess)} |"
                )
    else:
        lines.append("Keine – alle Notationen sind im RVK-Stand vorhanden.")
    lines += [
        "",
        "## b) Hauptgruppen-Ausreißer",
        "",
        (
            "Zeilen, bei denen keine Notation in einer Hauptgruppe liegt, die zum Fachgebiet "
            "passt. **Die Zuordnung Fachgebiet → Hauptgruppen ist eine Annahme von Claude** "
            "(Tabelle `FACHGEBIET_MAIN_GROUPS` in `app/services/gold_merge.py`), nur für diese "
            "Liste; sie ändert nichts am Goldstandard. Bitte nur gegenlesen."
        ),
        "",
    ]
    outliers = [c for c in data.checks if c.outlier]
    if outliers:
        lines += [
            "| Zeile | Anfrage | Fachgebiet (angenommene Hauptgruppen) | Notation | RVK-Benennung | Hierarchiepfad |",
            "|---|---|---|---|---|---|",
        ]
        for c in outliers:
            groups = " / ".join(specs_for_fachgebiet(c.entry.fachgebiet))
            for n in c.entry.expected:
                info = data.present.get(n) or {}
                lines.append(
                    f"| {c.entry.line} | {_cell(c.entry.query)} | "
                    f"{_cell(c.entry.fachgebiet)} ({_cell(groups)}) | {_cell(n)} | "
                    f"{_cell(info.get('label', 'nicht im RVK-Stand'))} | "
                    f"{_cell(info.get('hierarchy_path', '–'))} |"
                )
    else:
        lines.append("Keine.")
    unknown = [c for c in data.checks if c.outlier is None]
    if unknown:
        lines += [
            "",
            "Fachgebiet nicht in der Tabelle (nicht prüfbar): "
            + ", ".join(f"{c.entry.query} ({c.entry.fachgebiet})" for c in unknown),
        ]
    lines += [
        "",
        "## c) Alle übrigen Zeilen zum Gegenlesen",
        "",
        "Anfrage → Notation (RVK-Benennung) | …",
        "",
    ]
    listed = {id(c) for c in removed_rows} | {id(c) for c in outliers}
    for c in data.checks:
        if id(c) in listed:
            continue
        lines.append(
            f"- {c.entry.query} → "
            + " | ".join(_notation_text(n, data.present) for n in c.entry.expected)
        )
    lines.append("")
    return "\n".join(lines)


# --- Übernahme ---------------------------------------------------------------------------------


@dataclass
class MergePlan:
    append_lines: list[str]  # ohne Zeilenumbruch, Reihenfolge wie in der Erweiterung
    open_lines: list[str]
    header: str
    statuses: list[str]


def _rewrite_line(raw: str, header: list[str], notations: list[str], note: str) -> str:
    values = next(csv.reader([raw], delimiter=";"))
    values += [""] * (len(header) - len(values))
    n_idx = header.index("erwartete_notationen")
    values[n_idx] = "|".join(notations)
    if "anmerkung" in header:
        a_idx = header.index("anmerkung")
        old = values[a_idx].strip()
        values[a_idx] = f"{old} – {note}" if old else note
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\n").writerow(values)
    return buffer.getvalue().rstrip("\n")


def plan_merge(
    raw_lines: list[str], checks: list[RowCheck], rvk_version: str
) -> MergePlan:
    """Zeilen für goldstandard.csv und goldstandard_offen.csv.

    Unveränderte Zeilen bleiben wörtlich (Rohzeile). Gekürzte Zeilen werden mit den übrigen
    Notationen und Vermerk in anmerkung neu geschrieben. Zeilen ohne gültige Notation gehen mit
    allen Original-Notationen und Vermerk nach „offen“ (neu geschrieben, nicht wörtlich).
    """
    header_line = next(
        line for line in raw_lines if line.strip() and not line.lstrip().startswith("#")
    )
    header = [
        h.strip().lstrip("﻿") for h in next(csv.reader([header_line], delimiter=";"))
    ]
    append, open_rows, statuses = [], [], []
    for c in checks:
        raw = raw_lines[c.entry.line - 1].rstrip("\r\n")
        status = c.status
        statuses.append(status)
        if status == "unchanged":
            append.append(raw)
        elif status == "trimmed":
            original = [
                n.strip()
                for n in next(csv.reader([raw], delimiter=";"))[
                    header.index("erwartete_notationen")
                ].split("|")
                if n.strip()
            ]
            kept = [
                n
                for n in original
                if evaluation.normalize_notation(n) not in set(c.removed)
            ]
            append.append(
                _rewrite_line(raw, header, kept, vermerk(c.removed, rvk_version))
            )
        else:
            original = [
                n.strip()
                for n in next(csv.reader([raw], delimiter=";"))[
                    header.index("erwartete_notationen")
                ].split("|")
                if n.strip()
            ]
            open_rows.append(
                _rewrite_line(raw, header, original, vermerk(c.removed, rvk_version))
            )
    return MergePlan(append, open_rows, header_line.rstrip("\r\n"), statuses)


def append_bytes(old: bytes, lines: Iterable[str]) -> bytes:
    """Hängt Zeilen an; der alte Inhalt bleibt byte-gleich (fehlt der letzte Umbruch: nur \\n)."""
    lines = list(lines)
    if not lines:
        return old
    prefix = old if not old or old.endswith(b"\n") else old + b"\n"
    return prefix + "".join(f"{line}\n" for line in lines).encode("utf-8")


def open_file_bytes(header: str, lines: Iterable[str]) -> bytes:
    return "".join(f"{line}\n" for line in [header, *lines]).encode("utf-8")
