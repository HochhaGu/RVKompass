"""Reine Funktionen für die Evaluation der Suche gegen den Goldstandard (scripts/evaluate.py).

Keine Datenbank, kein Netz, kein Modell – alles hier ist ohne Daten testbar.

Alle Kennzahlen sind Relevanzmaße (Trefferquoten und mittlerer reziproker Rang) zwischen 0 und 1,
keine Wahrscheinlichkeiten. Sie werden deshalb als Dezimalzahlen und nie in Prozent ausgegeben.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field

# Hauptgruppe einer Notation: seit P6 in app/services/presentation.py (auch für die Oberfläche);
# hier weiter verfügbar für bestehende Aufrufer.
from app.services.presentation import main_group

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


def exclude_notations(
    entries: Iterable[GoldEntry], notations: Iterable[str]
) -> list[GoldEntry]:
    """Gold-Liste ohne die angegebenen Notationen (P8, z. B. „gelöscht in 2026/3“).

    Anfragen, denen danach keine erwartete Notation bleibt, fallen ganz heraus. Die Einträge
    werden kopiert, die übergebene Liste bleibt unverändert.
    """
    drop = {normalize_notation(n) for n in notations}
    result = []
    for entry in entries:
        kept = [n for n in entry.expected if normalize_notation(n) not in drop]
        if not kept:
            continue
        if len(kept) == len(entry.expected):
            result.append(entry)
            continue
        result.append(
            GoldEntry(
                query=entry.query,
                expected=kept,
                quelle=entry.quelle,
                fachgebiet=entry.fachgebiet,
                typ=entry.typ,
                anmerkung=entry.anmerkung,
                line=entry.line,
                extra=dict(entry.extra),
            )
        )
    return result


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
    entry: GoldEntry,
    results: list[str],
    seconds: float,
    expanded_terms: list[str],
    gnd_terms: list[str] | None = None,
    gnd_senses: list[str] | None = None,
    llm_suggestions: list[dict] | None = None,
) -> dict:
    """Kennzahlen einer Anfrage. `gnd_terms` als „Beziehung: Begriff“, `gnd_senses` als Namen,
    `llm_suggestions` als Vorschläge des Sprachmodells samt Prüfergebnis (P4)."""
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
        "gnd_senses": list(gnd_senses or []),
        "gnd_terms": list(gnd_terms or []),
        "llm_suggestions": list(llm_suggestions or []),
    }


def rank_changes(before: list[dict], after: list[dict]) -> list[dict]:
    """Anfragen, deren Rang sich zwischen zwei Läufen geändert hat, mit Richtung.

    „better“ heißt: erwartete Notation weiter vorn oder neu in der Trefferliste.
    """
    before_by_query = {q["query"]: q for q in before}
    changes = []
    for new in after:
        old = before_by_query.get(new["query"])
        if old is None or old.get("rank") == new.get("rank"):
            continue
        old_rank, new_rank = old.get("rank"), new.get("rank")
        better = new_rank is not None and (old_rank is None or new_rank < old_rank)
        changes.append(
            {
                "query": new["query"],
                "old_rank": old_rank,
                "new_rank": new_rank,
                "direction": "better" if better else "worse",
                "expanded_terms": list(new.get("expanded_terms", [])),
            }
        )
    return changes


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


def aggregate_variants_by(
    runs: dict[str, list[dict]], field_name: str
) -> dict[str, dict[str, dict]]:
    """Kennzahlen je Gruppe (z. B. Anfragetyp) für jede Variante: {variante: {gruppe: kennzahlen}}."""
    return {key: aggregate_by(run, field_name) for key, run in runs.items()}


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


def compare_per_query(new_per_query: list[dict], old_report: dict) -> list[dict]:
    """Anfragen, deren Rang oder Top-3 sich gegenüber dem alten Bericht geändert hat.

    Verglichen werden nur Anfragen, die in beiden Läufen vorkommen. Alte Berichte ohne
    `per_query` liefern eine leere Liste.
    """
    old_list = old_report.get("per_query") if isinstance(old_report, dict) else None
    if not old_list:
        return []
    old_by_query = {q["query"]: q for q in old_list if "query" in q}
    changed = []
    for new in new_per_query:
        old = old_by_query.get(new["query"])
        if old is None:
            continue
        if old.get("rank") != new.get("rank") or list(old.get("top3", [])) != list(
            new.get("top3", [])
        ):
            changed.append(
                {
                    "query": new["query"],
                    "old_rank": old.get("rank"),
                    "new_rank": new.get("rank"),
                    "old_top3": list(old.get("top3", [])),
                    "new_top3": list(new.get("top3", [])),
                }
            )
    return changed


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


VARIANT_LABELS = {
    "none": "ohne Erweiterung",
    "gnd": "nur GND",
    "llm": "nur LLM",
    "gnd_llm": "GND+LLM",
}


def _variants_section(report: dict) -> list[str]:
    variants = report.get("metrics_variants")
    if not variants:
        return []
    keys = [k for k in VARIANT_LABELS if k in variants]
    lines = [
        "",
        "## Varianten",
        "",
        (
            "„nur LLM“ und „GND+LLM“ nutzen nur Vorschläge des Sprachmodells, die im "
            "RVK-Register oder in der GND gefunden wurden. „Kennzahlen“ oben zeigt den "
            "Lauf GND+LLM."
        ),
        "",
        "| Kennzahl | " + " | ".join(VARIANT_LABELS[k] for k in keys) + " |",
        "|---" * (len(keys) + 1) + "|",
    ]
    for metric in METRIC_KEYS:
        lines.append(
            f"| {METRIC_LABELS[metric]} | "
            + " | ".join(_fmt(variants[k].get(metric)) for k in keys)
            + " |"
        )
    llm_info = report.get("llm") or {}
    if llm_info and not llm_info.get("enabled"):
        lines += [
            "",
            (
                "> Sprachmodell ausgeschaltet (ENABLE_QUERY_EXPANSION=false): „nur LLM“ "
                "entspricht „ohne Erweiterung“, „GND+LLM“ entspricht „nur GND“."
            ),
        ]
    by_typ = report.get("by_typ_variants")
    if by_typ:
        typ_keys = [k for k in keys if k in by_typ]
        types = sorted({t for k in typ_keys for t in by_typ[k]})
        lines += [
            "",
            "### Hit@10 je Anfragetyp und Variante",
            "",
            "| Typ | " + " | ".join(VARIANT_LABELS[k] for k in typ_keys) + " |",
            "|---" * (len(typ_keys) + 1) + "|",
        ]
        for typ in types:
            lines.append(
                f"| {_cell(typ)} | "
                + " | ".join(
                    _fmt((by_typ[k].get(typ) or {}).get("hit@10")) for k in typ_keys
                )
                + " |"
            )
    return lines


SPLIT_LABELS = {"dev": "Entwicklungshälfte", "test": "Testhälfte"}


def _scoring_section(report: dict) -> list[str]:
    """Abschnitt „Scoring“ (P5): Formel, Gewichte, Stufenschwellen, Kennzahlen je Hälfte."""
    settings = report.get("scoring")
    if not settings:
        return []
    lines = [
        "",
        "## Scoring",
        "",
        f"- Formel: {settings.get('formula', '–')}",
        (
            f"- Stufenschwellen: hoch ab {_fmt(settings.get('tier_high'))}, "
            f"mittel ab {_fmt(settings.get('tier_medium'))} (Relevanzwerte, keine "
            "Wahrscheinlichkeiten)"
        ),
    ]
    if settings.get("formula") != "legacy":
        weights = settings.get("weights") or {}
        lines.append(
            "- Pfadgewichte: " + ", ".join(f"{k} {_fmt(v)}" for k, v in weights.items())
        )
        if settings.get("formula") == "max_plus":
            lines.append(
                f"- Mehrfach-Fund: {_fmt(settings.get('path_bonus'))} je weiterem Pfad, "
                f"Deckel {_fmt(settings.get('path_bonus_cap'))}"
            )
        if settings.get("formula") == "sum_capped":
            lines.append(f"- Deckel der Pfadsumme: {_fmt(settings.get('sum_cap'))}")
    by_split = report.get("metrics_by_split") or {}
    if by_split:
        keys = ["n", "hit@1", "hit@5", "hit@10", "mrr", "main_group_hit@5"]
        lines += [
            "",
            "### Kennzahlen je Hälfte des Goldstandards",
            "",
            "| Hälfte | " + " | ".join(METRIC_LABELS[k] for k in keys) + " |",
            "|---" * (len(keys) + 1) + "|",
        ]
        for split, metrics in by_split.items():
            lines.append(
                f"| {SPLIT_LABELS.get(split, split)} | "
                + " | ".join(_fmt(metrics.get(k)) for k in keys)
                + " |"
            )
    return lines


def _rank_text(rank) -> str:
    return str(rank) if rank else "–"


def _llm_section(report: dict) -> list[str]:
    info = report.get("llm")
    if not info:
        return []
    total = info.get("suggestions_total") or 0
    verified = info.get("suggestions_verified") or 0
    lines = [
        "",
        "## Sprachmodell-Echttest",
        "",
        (
            f"- Modell: {info.get('model') or '–'}, "
            f"Bibliothek anthropic {info.get('sdk_version') or '–'}, "
            f"strukturierte Ausgabe: {_fmt(info.get('structured_output'))}"
        ),
        f"- API-Schlüssel vorhanden: {_fmt(info.get('api_key_present'))}",
        (
            f"- Aufrufe: {_fmt(info.get('api_calls'))}, "
            f"Fehler: {_fmt(info.get('api_errors'))}, "
            f"aus dem Zwischenspeicher: {_fmt(info.get('cache_hits'))}, "
            f"Tageslimit erreicht: {_fmt(info.get('limit_reached'))}"
        ),
        f"- Anfragen mit Vorschlägen: {_fmt(info.get('queries_with_suggestions'))}",
        (
            f"- Vorschläge: {total}, davon im Normvokabular gefunden: {verified} "
            f"(RVK-Register: {_fmt(info.get('verified_via_register'))}, "
            f"GND: {_fmt(info.get('verified_via_gnd'))})"
        ),
    ]
    effect = report.get("llm_effect") or {}
    for key, title in (
        ("llm_vs_none", "Anfragen, die mit „nur LLM“ gegenüber „ohne“ anders stehen"),
        (
            "gnd_llm_vs_gnd",
            "Anfragen, die mit „GND+LLM“ gegenüber „nur GND“ anders stehen",
        ),
    ):
        changes = effect.get(key)
        if changes is None:
            continue
        better = sum(1 for c in changes if c["direction"] == "better")
        lines += [
            "",
            f"### {title}",
            "",
            f"Besser: {better}, schlechter: {len(changes) - better}",
        ]
        if changes:
            lines += [
                "",
                "| Anfrage | Rang vorher | Rang nachher | Richtung | geprüfte LLM-Begriffe |",
                "|---|---|---|---|---|",
            ]
            for c in changes:
                lines.append(
                    f"| {_cell(c['query'])} | {_rank_text(c['old_rank'])} | "
                    f"{_rank_text(c['new_rank'])} | "
                    f"{'besser' if c['direction'] == 'better' else 'schlechter'} | "
                    f"{_cell(', '.join(c.get('expanded_terms') or []) or '–')} |"
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
        (
            f"- GND-Erweiterung eingeschaltet: {_fmt(expansion.get('gnd_enabled'))}, "
            f"Anfragen mit GND-Bedeutungen: {_fmt(expansion.get('queries_with_gnd_senses'))}"
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

    lines += _scoring_section(report)
    lines += _variants_section(report)
    lines += _llm_section(report)

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
        if "compare_per_query" in report:
            lines += [
                "",
                f"Anfragen mit geänderter Rangfolge: {len(report['compare_per_query'])}",
            ]

    if report.get("by_typ"):
        lines += ["", "## Kennzahlen je Anfragetyp", ""]
        lines += _metrics_table(report["by_typ"], "Typ")
    if report.get("by_fachgebiet"):
        lines += ["", "## Kennzahlen je Fachgebiet", ""]
        lines += _metrics_table(report["by_fachgebiet"], "Fachgebiet")

    ambiguous = [q for q in report.get("per_query", []) if q.get("typ") == "Mehrdeutig"]
    if ambiguous:
        lines += [
            "",
            "## Mehrdeutige Anfragen und GND-Bedeutungen",
            "",
            "| Anfrage | erwartet | Rang | GND-Bedeutungen |",
            "|---|---|---|---|",
        ]
        for q in ambiguous:
            senses = ", ".join(q.get("gnd_senses") or []) or "(keine)"
            lines.append(
                f"| {_cell(q['query'])} | {_cell(', '.join(q['expected']))} | "
                f"{q['rank'] if q['rank'] else '–'} | {_cell(senses)} |"
            )

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
