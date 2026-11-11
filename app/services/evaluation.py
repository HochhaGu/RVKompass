"""Reine Funktionen für die Evaluation der Suche gegen den Goldstandard (scripts/evaluate.py).

Keine Datenbank, kein Netz, kein Modell – alles hier ist ohne Daten testbar.

Alle Kennzahlen sind Relevanzmaße (Trefferquoten und mittlerer reziproker Rang) zwischen 0 und 1,
keine Wahrscheinlichkeiten. Sie werden deshalb als Dezimalzahlen und nie in Prozent ausgegeben.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

REQUIRED_COLUMNS = ("query", "erwartete_notationen")
METRIC_KEYS = (
    "hit@1",
    "hit@5",
    "hit@10",
    "mrr",
    "main_group_hit@5",
    "mean_response_ms",
    "zero_result_rate",
    "n",
)
METRIC_LABELS = {
    "hit@1": "Hit@1",
    "hit@5": "Hit@5",
    "hit@10": "Hit@10",
    "mrr": "MRR",
    "main_group_hit@5": "Hauptgruppen-Hit@5",
    "mean_response_ms": "Mittlere Antwortzeit (ms)",
    "zero_result_rate": "Anteil Anfragen ohne Treffer",
    "n": "Anzahl Anfragen",
}

_MAIN_GROUP_RE = re.compile(r"^[A-Z]+")


@dataclass
class GoldEntry:
    query: str
    expected: list[str]
    quelle: str = ""
    fachgebiet: str = ""
    typ: str = ""
    anmerkung: str = ""
    line: int = 0
    extra: dict = field(default_factory=dict)


def normalize_notation(notation: str) -> str:
    """Leerraum zusammenfassen und Großschreibung: "zn  8330 " -> "ZN 8330"."""
    return " ".join(str(notation).split()).upper()


def main_group(notation: str) -> str:
    """Hauptgruppe = führende Buchstaben der Notation: "ZN 8330" -> "ZN"."""
    match = _MAIN_GROUP_RE.match(normalize_notation(notation))
    return match.group(0) if match else ""


def parse_gold(lines: Iterable[str]) -> list[GoldEntry]:
    """Liest einen Goldstandard im CSV-Format mit ';' (mit oder ohne Spalte typ).

    Leere Zeilen und Zeilen, die mit '#' beginnen, werden übersprungen. Mehrere erwartete
    Notationen stehen durch '|' getrennt. Fehler werden mit Zeilennummer gemeldet.
    """
    numbered = [
        (no, line.rstrip("\r\n"))
        for no, line in enumerate(lines, start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not numbered:
        raise ValueError("Goldstandard ist leer")

    header_no, header_line = numbered[0]
    header = [
        h.strip().lstrip("\ufeff")
        for h in next(csv.reader([header_line], delimiter=";"))
    ]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise ValueError(
            f"Zeile {header_no}: Pflichtspalte(n) fehlen: {', '.join(missing)}"
        )

    entries: list[GoldEntry] = []
    for no, line in numbered[1:]:
        values = next(csv.reader([line], delimiter=";"))
        row = {
            col: (values[i].strip() if i < len(values) else "")
            for i, col in enumerate(header)
        }
        query = row.get("query", "")
        if not query:
            raise ValueError(f"Zeile {no}: leere Anfrage")
        expected = [
            normalize_notation(n)
            for n in row.get("erwartete_notationen", "").split("|")
            if n.strip()
        ]
        if not expected:
            raise ValueError(f"Zeile {no}: keine erwartete Notation für {query!r}")
        known = {
            "query",
            "erwartete_notationen",
            "quelle",
            "fachgebiet",
            "typ",
            "anmerkung",
        }
        entries.append(
            GoldEntry(
                query=query,
                expected=expected,
                quelle=row.get("quelle", ""),
                fachgebiet=row.get("fachgebiet", ""),
                typ=row.get("typ", ""),
                anmerkung=row.get("anmerkung", ""),
                line=no,
                extra={k: v for k, v in row.items() if k not in known},
            )
        )
    return entries


def first_hit_rank(results: list[str], expected: list[str]) -> int | None:
    """1-basierter Rang des ersten erwarteten Treffers, sonst None."""
    wanted = {normalize_notation(e) for e in expected}
    for rank, notation in enumerate(results, start=1):
        if normalize_notation(notation) in wanted:
            return rank
    return None


def hit_at_k(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: int | None) -> float:
    return 1.0 / rank if rank else 0.0


def main_group_hit_at_k(results: list[str], expected: list[str], k: int) -> bool:
    """Teiltreffer: ein Treffer unter den ersten k liegt in der Hauptgruppe einer erwarteten Notation."""
    groups = {main_group(e) for e in expected} - {""}
    return any(main_group(r) in groups for r in results[:k])


def evaluate_query(
    entry: GoldEntry, results: list[str], seconds: float, expanded_terms: list[str]
) -> dict:
    rank = first_hit_rank(results, entry.expected)
    return {
        "query": entry.query,
        "expected": entry.expected,
        "typ": entry.typ,
        "fachgebiet": entry.fachgebiet,
        "rank": rank,
        "hit@1": hit_at_k(rank, 1),
        "hit@5": hit_at_k(rank, 5),
        "hit@10": hit_at_k(rank, 10),
        "rr": round(reciprocal_rank(rank), 4),
        "main_group_hit@5": main_group_hit_at_k(results, entry.expected, 5),
        "top3": list(results[:3]),
        "result_count": len(results),
        "response_ms": round(seconds * 1000, 1),
        "expanded_terms": list(expanded_terms),
    }


def aggregate(per_query: list[dict]) -> dict:
    if not per_query:
        raise ValueError("Keine Anfragen ausgewertet")
    n = len(per_query)

    def rate(key: str) -> float:
        return round(sum(1 for q in per_query if q[key]) / n, 4)

    return {
        "hit@1": rate("hit@1"),
        "hit@5": rate("hit@5"),
        "hit@10": rate("hit@10"),
        "mrr": round(sum(q["rr"] for q in per_query) / n, 4),
        "main_group_hit@5": rate("main_group_hit@5"),
        "mean_response_ms": round(sum(q["response_ms"] for q in per_query) / n, 1),
        "zero_result_rate": round(
            sum(1 for q in per_query if q["result_count"] == 0) / n, 4
        ),
        "n": n,
    }


def aggregate_by(per_query: list[dict], field_name: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for q in per_query:
        groups.setdefault(q.get(field_name) or "(ohne Angabe)", []).append(q)
    return {key: aggregate(items) for key, items in sorted(groups.items())}


def worst_queries(per_query: list[dict], n: int = 10) -> list[dict]:
    """Die n schlechtesten Anfragen: kleinster reziproker Rang, dann ohne Hauptgruppen-Treffer."""
    return sorted(
        per_query, key=lambda q: (q["rr"], q["main_group_hit@5"], q["query"])
    )[:n]


def compare_metrics(new: dict, old_report: dict) -> dict:
    """Differenz neu minus alt je Kennzahl; berücksichtigt nur Schlüssel, die beide haben."""
    old = old_report.get("metrics", old_report) if isinstance(old_report, dict) else {}
    diff = {}
    for key, value in new.items():
        old_value = old.get(key)
        if _is_number(value) and _is_number(old_value):
            diff[key] = {
                "old": old_value,
                "new": value,
                "diff": round(value - old_value, 4),
            }
    return diff


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value != 0 else "0"
    return str(value)


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _metrics_table(groups: dict[str, dict], first_col: str) -> list[str]:
    keys = ["n", "hit@1", "hit@5", "hit@10", "mrr", "main_group_hit@5"]
    lines = [
        f"| {first_col} | " + " | ".join(METRIC_LABELS[k] for k in keys) + " |",
        "|---" * (len(keys) + 1) + "|",
    ]
    for name, metrics in groups.items():
        lines.append(
            f"| {_cell(name)} | " + " | ".join(_fmt(metrics[k]) for k in keys) + " |"
        )
    return lines


def render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    no_exp = report.get("metrics_no_expansion")
    expansion = report.get("expansion", {})
    lines = [
        "# RVKompass – Evaluationsbericht",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report.get('timestamp', '–')}",
        f"- Git-Stand: {report.get('git_commit', '–')}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report.get('gold_file', '–')} ({report.get('gold_count', '–')} Einträge)",
        f"- Modus: {report.get('mode', '–')}",
        (
            f"- Begriffserweiterung eingeschaltet: {_fmt(expansion.get('enabled'))}, "
            f"API-Schlüssel vorhanden: {_fmt(expansion.get('api_key_present'))}, "
            f"Anfragen mit Erweiterungsbegriffen: {_fmt(expansion.get('queries_with_terms'))}"
        ),
        f"- RVK-Stand: {report.get('rvk_version') or '–'}",
        f"- Embedding-Modell: {report.get('embedding_model', '–')}",
        "",
        "## Kennzahlen",
        "",
        (
            "> Hinweis: Alle Werte sind Relevanzmaße (Trefferquoten zwischen 0 und 1 bzw. "
            "mittlerer reziproker Rang), keine Wahrscheinlichkeiten. Hauptgruppen-Hit@5 zählt "
            "einen Treffer, wenn unter den ersten fünf Vorschlägen eine Notation aus derselben "
            "Hauptgruppe (z. B. „ZN“) liegt."
        ),
        "",
    ]
    if no_exp is not None and report.get("mode") != "no_expansion":
        lines += ["| Kennzahl | mit Erweiterung | ohne Erweiterung |", "|---|---|---|"]
        for key in METRIC_KEYS:
            lines.append(
                f"| {METRIC_LABELS[key]} | {_fmt(metrics.get(key))} | {_fmt(no_exp.get(key))} |"
            )
    else:
        lines += ["| Kennzahl | Wert |", "|---|---|"]
        for key in METRIC_KEYS:
            lines.append(f"| {METRIC_LABELS[key]} | {_fmt(metrics.get(key))} |")

    if report.get("compare"):
        lines += [
            "",
            "## Vergleich mit früherem Bericht",
            "",
            f"Vergleichsbericht: {report.get('compare_file', '–')}",
            "",
            "| Kennzahl | alt | neu | Differenz |",
            "|---|---|---|---|",
        ]
        for key, row in report["compare"].items():
            label = METRIC_LABELS.get(key, key)
            diff = row["diff"]
            lines.append(
                f"| {label} | {_fmt(row['old'])} | {_fmt(row['new'])} | "
                f"{'+' if diff > 0 else ''}{_fmt(diff)} |"
            )

    if report.get("by_typ"):
        lines += ["", "## Kennzahlen je Anfragetyp", ""]
        lines += _metrics_table(report["by_typ"], "Typ")
    if report.get("by_fachgebiet"):
        lines += ["", "## Kennzahlen je Fachgebiet", ""]
        lines += _metrics_table(report["by_fachgebiet"], "Fachgebiet")

    lines += [
        "",
        "## Die 10 schlechtesten Anfragen",
        "",
        "| Anfrage | erwartet | gelieferte Top-3 | Rang |",
        "|---|---|---|---|",
    ]
    for q in report.get("worst", []):
        lines.append(
            f"| {_cell(q['query'])} | {_cell(', '.join(q['expected']))} | "
            f"{_cell(', '.join(q['top3']) or '(keine Treffer)')} | "
            f"{q['rank'] if q['rank'] else 'nicht unter den Treffern'} |"
        )

    lines += ["", "## Erwartete Notationen, die im RVK-Bestand fehlen", ""]
    missing = report.get("missing_notations")
    if missing is None:
        lines.append("Nicht geprüft.")
    elif missing:
        lines.append(
            "Mögliche Tippfehler im Goldstandard – bitte in RVK-Online prüfen:"
        )
        lines.append("")
        lines += [
            f"- {_cell(m['notation'])} (Anfrage: {_cell(m['query'])})" for m in missing
        ]
    else:
        lines.append(
            "Keine – alle erwarteten Notationen sind im RVK-Bestand vorhanden."
        )
    lines.append("")
    return "\n".join(lines)
