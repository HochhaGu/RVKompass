"""Stimmt die Scoring-Gewichte (P5) am Goldstandard ab und schreibt einen Bericht.

Ablauf: Goldstandard nach Anfragetyp geschichtet in Entwicklungs- und Testhälfte teilen, Merkmale
je Anfrage einmal berechnen (gleiche Suchwege wie scripts/evaluate.py, Variante „nur GND“ bzw.
„GND+LLM“ wenn das Sprachmodell eingeschaltet ist), Grid-Search je Formel nur auf der
Entwicklungshälfte (Ziel MRR), Bericht auf Test- und Entwicklungshälfte sowie dem vollen
Bestand, Entscheidung nach Günthers Regel, Stufenschwellen aus der Entwicklungshälfte.

Ergebnis: reports/scoring-<datum>-<git-shorthash>.json und .md (bewusst nicht „eval-“, damit
das Gate des Steuerprogramms weiter den Evaluationsbericht liest).

Aufruf:
    python scripts/tune_scoring.py --gold eval/goldstandard.csv --out reports/

P11, ohne Grid (wiederholbar für P12 mit größerem Goldstandard):
    python scripts/tune_scoring.py --gold eval/goldstandard.csv --diagnose
        Prüfauftrag „Hit@5 = Hit@10“: Ränge, Scores um Platz 5/6/10, Fundwege, Selbstprüfung.
        Bericht reports/scoring-diagnose-<datum>-<hash>.json/.md
    python scripts/tune_scoring.py --gold eval/goldstandard.csv --tiers-only
        Fünf Relevanzstufen für die eingestellte Formel (Profil aus app/config.py) auf der
        Entwicklungshälfte ableiten, auf der Testhälfte prüfen.
        Bericht reports/scoring-stufen-<datum>-<hash>.json/.md
    --formula max_plus|legacy|… wählt die Formel statt SCORING_FORMULA aus der Konfiguration.

P12, Grid nur für die eingestellte Formel:
    python scripts/tune_scoring.py --gold eval/goldstandard.csv --retune
        Grid auf der Entwicklungshälfte (Start und Gleichstand: aktuelle Profilgewichte),
        Vergleich mit den aktuellen Gewichten auf der Testhälfte nach der vorab festgelegten
        Regel scoring_tuning.accept_p12 (Hit@10 und MRR nicht schlechter, eine ≥ +0.01).
        Kennzahlen auch je Teilmenge (GOLD_SUBSET_SIZES). Trägt nichts in app/config.py ein.
        Bericht reports/scoring-retune-<datum>-<hash>.json/.md
    --diagnose enthält in P12 zusätzlich die Werte je Teilmenge („diagnose_by_subset“).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services import evaluation, ranking, rvk_repository, scoring
from app.services import scoring_tuning as tuning
from app.services.expansion import gnd, llm
from scripts.evaluate import (
    build_embedding_service,
    config_with_overrides,
    git_info,
    gnd_enabled,
    llm_enabled,
)

# Bezugswerte P4, Variante „nur GND“ (reports/eval-2026-11-11-c784d47-4.json, voller Bestand)
P4_REFERENCE = {"hit@10": 0.5567, "mrr": 0.3432, "hit@1": 0.2577}
P4_REFERENCE_FILE = "reports/eval-2026-11-11-c784d47-4.json"

TABLE_KEYS = ("hit@1", "hit@5", "hit@10", "mrr", "main_group_hit@5")
VARIANT_TITLES = {
    "legacy": "legacy (Stand P4)",
    "sum": "sum – Summe + Vektor",
    "max_plus": "max_plus – Max + Vektor + Mehrfach-Fund",
    "sum_capped": "sum_capped – gedeckelte Summe + Vektor",
}


# Bezugswerte P5 für max_plus ohne Sprachmodell, voller Bestand
# (reports/scoring-2026-11-11-c877270-2.json, Variante max_plus, „full“)
P5_MAX_PLUS_REFERENCE = {"hit@10": 0.5979, "mrr": 0.4649}
P5_MAX_PLUS_FILE = "reports/scoring-2026-11-11-c877270-2.json"
# Letzter legacy-Bericht vor P11 (P10, reports/eval-2026-11-16-afc2ce4-3.json)
P10_LEGACY_REFERENCE = {"hit@10": 0.5464, "mrr": 0.3431}
P10_LEGACY_FILE = "reports/eval-2026-11-16-afc2ce4-3.json"
SELF_CHECK_TOLERANCE = 0.005


def report_basename(
    out_dir: Path, date: str, commit: str, prefix: str = "scoring"
) -> str:
    """<prefix>-<datum>-<hash>; existiert die Datei schon, wird -2, -3 ... angehängt."""
    base = f"{prefix}-{date}-{commit}"
    name = base
    counter = 2
    while (out_dir / f"{name}.json").exists() or (out_dir / f"{name}.md").exists():
        name = f"{base}-{counter}"
        counter += 1
    return name


def latest_eval_report(out_dir: Path) -> tuple[str | None, float | None]:
    """Neuester eval-*.json (nach Feld timestamp) und dessen metrics.hit@10."""
    best = None
    for path in out_dir.glob("eval-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(data["timestamp"])
            hit10 = float(data["metrics"]["hit@10"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if best is None or stamp > best[0]:
            best = (stamp, str(path), hit10)
    if best is None:
        return None, None
    return best[1], best[2]


def collect_features(conn, service, entries, config, source) -> list:
    """Merkmale je Anfrage, mit denselben Suchwegen wie die Evaluation."""
    result = []
    for entry in entries:
        senses = []
        suggestions = []
        if source is not None:
            senses = gnd.gnd_senses_for_query(conn, config, entry.query, source=source)
        if llm_enabled(config):
            suggestions = llm.llm_suggestions_for_query(
                conn, config, entry.query, source=source
            )
        llm_terms = llm.verified_terms(suggestions, entry.query)
        candidates = ranking.build_candidates(
            conn, service, entry.query, llm_terms, gnd_senses=senses
        )
        features = candidates.features if candidates is not None else []
        result.append(
            {
                "qf": tuning.QueryFeatures(entry, features),
                "has_senses": bool(senses),
                "has_llm_terms": bool(llm_terms),
            }
        )
    return result


def _fmt(value) -> str:
    return evaluation._fmt(value)


def _settings_text(settings: scoring.ScoringSettings) -> str:
    if settings.formula == "legacy":
        return "feste Zuschläge wie in P4"
    weights = ", ".join(f"{p} {_fmt(settings.weight(p))}" for p in scoring.PATH_TYPES)
    extra = ""
    if settings.formula == "max_plus":
        extra = f"; Mehrfach-Fund {_fmt(settings.path_bonus)} je weiterem Pfad, Deckel {_fmt(settings.path_bonus_cap)}"
    elif settings.formula == "sum_capped":
        extra = f"; Deckel {_fmt(settings.sum_cap)}"
    return weights + extra


def render_markdown(report: dict) -> str:
    split = report["split"]
    lines = [
        "# RVKompass – Abstimmung des Scorings (P5)",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Git-Stand: {report['git_commit']}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report['gold_file']} ({report['gold_count']} Anfragen)",
        (
            f"- Aufteilung: Entwicklung {split['dev_count']}, Test {split['test_count']} "
            "(je Anfragetyp abwechselnd in Dateireihenfolge)"
        ),
        (
            f"- Suchwege: GND {_fmt(report['gnd_enabled'])} "
            f"(Anfragen mit GND-Bedeutungen: {report['queries_with_gnd_senses']}), "
            f"Sprachmodell {_fmt(report['llm_enabled'])}"
        ),
        f"- Bewertungen im Grid: {report['grid_size_total']}",
        "",
        (
            "> Alle Werte sind Relevanzmaße (Trefferquoten zwischen 0 und 1 bzw. mittlerer "
            "reziproker Rang), keine Wahrscheinlichkeiten. Gewichte nur an der "
            "Entwicklungshälfte abgestimmt; maßgeblich für die Entscheidung ist die Testhälfte."
        ),
        "",
        "## Selbstprüfung",
        "",
        (
            f"Offline gerechnete Formel „legacy“ auf dem vollen Bestand: Hit@10 "
            f"{_fmt(report['self_check']['legacy_full']['hit@10'])}, MRR "
            f"{_fmt(report['self_check']['legacy_full']['mrr'])} (P4 „nur GND“: Hit@10 "
            f"{_fmt(P4_REFERENCE['hit@10'])}, MRR {_fmt(P4_REFERENCE['mrr'])}; "
            f"Abweichung Hit@10 {_fmt(report['self_check']['diff_hit@10'])}, MRR "
            f"{_fmt(report['self_check']['diff_mrr'])})."
        ),
        (
            "Schnellbewertung im Grid und Referenz-Rangfolge stimmen überein: "
            f"{_fmt(report['self_check']['fast_matches_reference'])}."
        ),
        "",
        "## Ergebnisse aller geprüften Varianten",
        "",
    ]
    for part, title in (
        ("test", "Testhälfte"),
        ("dev", "Entwicklungshälfte"),
        ("full", "Voller Bestand"),
    ):
        lines += [
            f"### {title}",
            "",
            "| Variante | "
            + " | ".join(evaluation.METRIC_LABELS[k] for k in TABLE_KEYS)
            + " |",
            "|---" * (len(TABLE_KEYS) + 1) + "|",
        ]
        for v in report["variants"]:
            lines.append(
                f"| {VARIANT_TITLES[v['variant']]} | "
                + " | ".join(_fmt(v[part][k]) for k in TABLE_KEYS)
                + " |"
            )
        lines.append("")
    lines += ["## Gewählte Gewichte je Variante", ""]
    for v in report["variants"]:
        lines.append(f"- {VARIANT_TITLES[v['variant']]}: {v['settings_text']}")
    decision = report["decision"]
    lines += [
        "",
        "## Entscheidung",
        "",
        (
            f"Regel: Testhälfte Hit@10 ≥ {_fmt(P4_REFERENCE['hit@10'])} und MRR ≥ "
            f"{_fmt(P4_REFERENCE['mrr'])} (P4), mindestens so gut wie „legacy“ auf der "
            f"Testhälfte, voller Bestand Hit@10 ≥ {_fmt(report['gate']['hit@10'])} − 0.02 "
            f"(letzter Bericht {report['gate']['file'] or '–'})."
        ),
        "",
        "| Variante | P4 Hit@10 | P4 MRR | ≥ legacy Hit@10 | ≥ legacy MRR | Gate | übernommen |",
        "|---|---|---|---|---|---|---|",
    ]
    for check in decision["checks"]:
        c = check["conditions"]
        lines.append(
            f"| {check['variant']} | {_fmt(c['test_hit10_ge_p4'])} | "
            f"{_fmt(c['test_mrr_ge_p4'])} | {_fmt(c['test_hit10_ge_legacy'])} | "
            f"{_fmt(c['test_mrr_ge_legacy'])} | {_fmt(c['full_hit10_gate'])} | "
            f"{_fmt(check['accepted'])} |"
        )
    lines += ["", f"**{decision['reason']}**", ""]
    near = [c["variant"] for c in decision["checks"] if c["beats_legacy_on_test_only"]]
    if near:
        lines += [
            (
                "Hinweis: "
                + ", ".join(near)
                + " ist/sind auf der Testhälfte mindestens so "
                "gut wie „legacy“ (Hit@10 und MRR), scheitert/scheitern aber an den "
                "wörtlichen P4-Zahlen."
            ),
            "",
        ]
    tiers = report["tiers"]
    lines += [
        "## Stufen „hoch / mittel / niedrig“",
        "",
        (
            f"Formel: {tiers['formula']}. Schwellen aus den ersten {tuning.TIER_TOP_K} "
            f"Treffern je Anfrage der Entwicklungshälfte (Verfahren: {tiers['method']}): "
            f"hoch ab {_fmt(tiers['tier_high'])}, mittel ab {_fmt(tiers['tier_medium'])}."
        ),
        "",
        (
            "„passend“ = Hauptgruppe einer erwarteten Notation, „exakt“ = erwartete "
            "Notation. Diese Anteile stehen nur im Bericht, nicht in der Oberfläche."
        ),
        "",
        "| Hälfte | Stufe | Treffer | Anteil passend | Anteil exakt |",
        "|---|---|---|---|---|",
    ]
    for part, title in (("dev", "Entwicklung"), ("test", "Test")):
        for tier in scoring.TIERS:
            row = tiers["shares"][part][tier]
            lines.append(
                f"| {title} | {tier} | {row['n']} | {_fmt(row['share_main_group'])} | "
                f"{_fmt(row['share_exact'])} |"
            )
    if report.get("five_tiers"):
        lines += [
            "",
            "## Fünf Relevanzstufen (P11)",
            "",
            *five_tier_lines(report["five_tiers"]),
        ]
    lines += [
        "",
        "## Aufteilung",
        "",
        "Entwicklung: " + ", ".join(split["dev"]),
        "",
        "Test: " + ", ".join(split["test"]),
        "",
    ]
    return "\n".join(lines)


# --- P11: Fünf Stufen und Diagnose (ohne Grid) ------------------------------------------------


def derive_and_check_five_tiers(parts: dict, settings: scoring.ScoringSettings) -> dict:
    """Fünf Stufen auf der Entwicklungshälfte ableiten und auf beiden Hälften auswerten.

    Liefert `{"formula", "tier_*", "methods", "shares": {"dev", "test"}, "settings"}` oder
    `{"formula", "error"}`, wenn sich mit diesen Daten keine fünf Stufen bilden lassen.
    """
    rows_dev = tuning.top_hits(parts["dev"], settings)
    try:
        tiers = tuning.derive_five_tiers(rows_dev)
        tier_settings = settings.replace(
            tier_very_high=tiers["tier_very_high"],
            tier_high=tiers["tier_high"],
            tier_medium=tiers["tier_medium"],
            tier_weak=tiers["tier_weak"],
        )
    except ValueError as exc:
        return {"formula": settings.formula, "error": str(exc)}
    shares = {
        part: tuning.five_tier_shares(
            tuning.top_hits(parts[part], tier_settings), tier_settings
        )
        for part in ("dev", "test")
    }
    return {
        "formula": settings.formula,
        **tiers,
        "top_k": tuning.TIER_TOP_K,
        "shares_by_part": shares,
        "settings": tier_settings.to_dict(),
    }


TIER_NAMES = (
    ("tier_very_high", "sehr hoch"),
    ("tier_high", "hoch"),
    ("tier_medium", "mittel"),
    ("tier_weak", "schwach"),
)


def five_tier_lines(tiers: dict) -> list[str]:
    """Markdown-Abschnitt der fünf Stufen (Regel, Werte, Anteile je Hälfte)."""
    if "error" in tiers:
        return [
            f"Formel {tiers['formula']}: keine fünf Stufen ableitbar ({tiers['error']}).",
            "",
        ]
    shares = dict(tiers["shares"])
    lines = [
        (
            f"Formel {tiers['formula']}. Grundlage: die ersten {tiers['top_k']} Treffer je "
            "Anfrage der Entwicklungshälfte. Regel (gesetzte Annahme, nicht gemessen): "
            "Eine Stufe beginnt beim kleinsten Score, ab dem mindestens "
            + " / ".join(f"{round(shares[k] * 100)} %" for k, _ in TIER_NAMES)
            + " der Treffer passen („sehr hoch / hoch / mittel / schwach“), mit "
            f"mindestens {tiers['min_support']} Treffern je Stufe. Nur wenn alle vier "
            "Grenzen so erreichbar sind, gilt die Anteilsregel; sonst gelten für alle "
            "vier Grenzen die Quintile der Scores (reine Rangordnung, nicht kalibriert)."
        ),
        "",
        f"Verfahren: **{tiers['method']}**",
        "",
        "| Stufe | ab Relevanzwert | Anteilsregel hätte ergeben |",
        "|---|---|---|",
    ]
    for key, word in TIER_NAMES:
        reached = tiers["share_rule"].get(key)
        lines.append(
            f"| {word} | {_fmt(tiers[key])} | "
            f"{_fmt(reached) if reached is not None else 'nicht erreichbar'} |"
        )
    lines += [
        "",
        (
            "„passend“ = Hauptgruppe einer erwarteten Notation, „exakt“ = erwartete "
            "Notation; Intervall = 95-%-Wilson-Intervall des Anteils passend. Nur im "
            "Bericht, nicht in der Oberfläche."
        ),
        "",
    ]
    for part, title in (("test", "Testhälfte"), ("dev", "Entwicklungshälfte")):
        data = tiers["shares_by_part"][part]
        lines += [
            f"### {title}",
            "",
            "| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt | 95-%-Intervall exakt |",
            "|---|---|---|---|---|---|---|---|",
        ]

        def ci_text(ci) -> str:
            return f"{_fmt(ci[0])} – {_fmt(ci[1])}" if ci else "–"

        for word in reversed(tuning.FIVE_TIER_WORDS):
            row = data["levels"][word]
            lines.append(
                f"| {word} | {row['n']} | {row['main_group']} | "
                f"{_fmt(row['share_main_group'])} | {ci_text(row['ci_main_group'])} | "
                f"{row['exact']} | {_fmt(row['share_exact'])} | "
                f"{ci_text(row.get('ci_exact'))} |"
            )
        separated = [k.replace("|", " / ") for k, v in data["separated"].items() if v]
        overlapping = [
            k.replace("|", " / ") for k, v in data["separated"].items() if not v
        ]
        lines += [
            "",
            f"- Anteil passend fällt von Stufe zu Stufe: {_fmt(data['ordered'])}",
            "- Klar getrennt (Intervalle überlappen nicht): "
            + (", ".join(separated) or "keine"),
            "- Überlappende Nachbarstufen: " + (", ".join(overlapping) or "keine"),
            "",
        ]
    return lines


def render_tiers_markdown(report: dict) -> str:
    lines = [
        "# RVKompass – Fünf Relevanzstufen (P11)",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Git-Stand: {report['git_commit']}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report['gold_file']} ({report['gold_count']} Anfragen)",
        (
            f"- Aufteilung: Entwicklung {report['split']['dev_count']}, Test "
            f"{report['split']['test_count']} (je Anfragetyp abwechselnd, wie in P5)"
        ),
        (
            f"- Suchwege: GND {_fmt(report['gnd_enabled'])}, Sprachmodell "
            f"{_fmt(report['llm_enabled'])}"
        ),
        f"- Gewichte: {report['settings_text']}",
        "",
        (
            "> Relevanzwerte sind ein Maß für die Passung, keine Wahrscheinlichkeiten. "
            "Die Stufen sind nur auf der Entwicklungshälfte abgeleitet; maßgeblich für ihre "
            "Belastbarkeit ist die Testhälfte."
        ),
        "",
        "## Stufen",
        "",
        *five_tier_lines(report["tiers"]),
    ]
    return "\n".join(lines)


def _settings_for_formula(config, formula: str | None) -> scoring.ScoringSettings:
    """Einstellungen der Konfiguration, wahlweise für eine andere Formel (samt Profil)."""
    if formula:
        config = config_with_overrides(config, {"SCORING_FORMULA": formula})
    return scoring.ScoringSettings.from_config(config)


def legacy_changes_since(per_query: list[dict], report_file: str) -> list[dict]:
    """Anfragen, deren Rang sich bei legacy gegenüber einem älteren Bericht geändert hat."""
    path = ROOT / report_file
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [
        {k: c[k] for k in ("query", "old_rank", "new_rank")}
        for c in evaluation.compare_per_query(per_query, old)
        if c["old_rank"] != c["new_rank"]
    ]


DIAGNOSE_EXAMPLES = 12


def render_diagnose_markdown(report: dict) -> str:
    sc = report["self_check"]
    full = report["diagnose"]["full"]
    lines = [
        "# RVKompass – Diagnose der Rangfolge (P11, Prüfauftrag „Hit@5 = Hit@10“)",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Git-Stand: {report['git_commit']}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report['gold_file']} ({report['gold_count']} Anfragen)",
        (
            f"- Suchwege: GND {_fmt(report['gnd_enabled'])}, Sprachmodell "
            f"{_fmt(report['llm_enabled'])}"
        ),
        f"- Formel: {report['settings']['formula']} – {report['settings_text']}",
        "",
        "## Hit@5 und Hit@10 auf zwei Wegen",
        "",
        (
            "„Referenz“ = derselbe Weg wie scripts/evaluate.py (score_candidates, danach "
            f"Abschneiden auf {report['max_results']} Vorschläge). „FastScorer“ = "
            "Schnellbewertung des Grids (ungekürzte Rangberechnung)."
        ),
        "",
        "| Hälfte | Hit@5 Referenz | Hit@10 Referenz | Hit@5 FastScorer | Hit@10 FastScorer | Ränge 6–10 | Ränge gleich |",
        "|---|---|---|---|---|---|---|",
    ]
    for part, title in (
        ("full", "Voller Bestand"),
        ("dev", "Entwicklungshälfte"),
        ("test", "Testhälfte"),
    ):
        d = report["diagnose"][part]
        lines.append(
            f"| {title} | {_fmt(d['hit@5'])} | {_fmt(d['hit@10'])} | "
            f"{_fmt(d['hit@5_fast'])} | {_fmt(d['hit@10_fast'])} | {d['rank_6_to_10']} | "
            f"{_fmt(d['fast_matches_reference'])} |"
        )
    for name, d in (report.get("diagnose_by_subset") or {}).items():
        lines.append(
            f"| Teilmenge {evaluation.SUBSET_LABELS.get(name, name)} | {_fmt(d['hit@5'])} | "
            f"{_fmt(d['hit@10'])} | {_fmt(d['hit@5_fast'])} | {_fmt(d['hit@10_fast'])} | "
            f"{d['rank_6_to_10']} | {_fmt(d['fast_matches_reference'])} |"
        )
    subsets = report.get("diagnose_by_subset") or {}
    if subsets:
        lines += [
            "",
            "| Teilmenge | Gleichstand 5/6 | erste erwartete Stelle gefunden | davon nur Vektor | abgeschnitten nach Platz 20 | nicht unter den Kandidaten |",
            "|---|---|---|---|---|---|",
        ]
        for name, d in subsets.items():
            hist = d["histogram"]
            lines.append(
                f"| {evaluation.SUBSET_LABELS.get(name, name)} | {d['ties_at_5_6']} | "
                f"{d['expected_found']} | {d['expected_vector_only']} | "
                f"{hist['nach Platz 20 (abgeschnitten)']} | "
                f"{hist['nicht unter den Kandidaten']} |"
            )
    lines += [
        "",
        "## Rang der ersten erwarteten Stelle (voller Bestand)",
        "",
        "| Rang | Anfragen |",
        "|---|---|",
    ]
    for rank, count in full["histogram"].items():
        if count:
            lines.append(f"| {rank} | {count} |")
    lines += [
        "",
        f"- Gleichstand des Scores an der Grenze Platz 5/6: {full['ties_at_5_6']} Anfragen",
        (
            f"- Erste erwartete Stelle unter den Kandidaten: {full['expected_found']} Anfragen; "
            f"davon über einen Fundweg mit Normbegriff, Register oder Volltext "
            f"{full['expected_with_norm_path']}, nur über Vektor-Ähnlichkeit "
            f"{full['expected_vector_only']}"
        ),
        (
            "- Anfragen mit mindestens 10 Kandidaten mit Fundweg außer Vektor: "
            f"{full['queries_with_10_path_candidates']}"
        ),
        "",
        "## Beispiele",
        "",
        (
            "Anfragen, deren erste erwartete Stelle nicht unter den ersten 5 liegt. "
            "„mit Fundweg“ = Kandidaten mit mindestens einem Fundweg außer Vektor."
        ),
        "",
        "| Anfrage | Rang | ungekürzt | Score erwartet | Fundwege erwartet | Score Platz 5 / 6 / 10 / 11 | Kandidaten (mit Fundweg) |",
        "|---|---|---|---|---|---|---|",
    ]
    examples = [
        q for q in full["per_query"] if q["rank_uncut"] is None or q["rank_uncut"] > 5
    ]
    examples.sort(key=lambda q: (q["rank_uncut"] is None, q["rank_uncut"] or 0))
    for q in examples[:DIAGNOSE_EXAMPLES]:
        at = q["scores_at"]
        lines.append(
            f"| {evaluation._cell(q['query'])} | {_fmt(q['rank'])} | "
            f"{_fmt(q['rank_uncut'])} | {_fmt(q['expected_score'])} | "
            f"{', '.join(q['expected_paths']) or 'nur Vektor'} | "
            f"{' / '.join(_fmt(at[k]) for k in ('5', '6', '10', '11'))} | "
            f"{q['candidates']} ({q['candidates_with_path']}) |"
        )
    lines += [
        "",
        "## Selbstprüfung",
        "",
        (
            f"- max_plus offline, voller Bestand: Hit@10 {_fmt(sc['max_plus_full']['hit@10'])}, "
            f"MRR {_fmt(sc['max_plus_full']['mrr'])} (P5: Hit@10 "
            f"{_fmt(P5_MAX_PLUS_REFERENCE['hit@10'])}, MRR {_fmt(P5_MAX_PLUS_REFERENCE['mrr'])}; "
            f"innerhalb ±{_fmt(SELF_CHECK_TOLERANCE)}: {_fmt(sc['max_plus_reproduces_p5'])})"
        ),
        (
            f"- legacy offline, voller Bestand: Hit@10 {_fmt(sc['legacy_full']['hit@10'])}, "
            f"MRR {_fmt(sc['legacy_full']['mrr'])} (P5/P4: Hit@10 "
            f"{_fmt(P4_REFERENCE['hit@10'])}, MRR {_fmt(P4_REFERENCE['mrr'])}; P10: "
            f"Hit@10 {_fmt(P10_LEGACY_REFERENCE['hit@10'])}, MRR "
            f"{_fmt(P10_LEGACY_REFERENCE['mrr'])})"
        ),
        (
            f"- legacy: Anfragen mit anderem Rang als im P4/P5-Bezugsbericht "
            f"({P4_REFERENCE_FILE}): {len(sc['legacy_changes_vs_p5'])}"
        ),
        "",
    ]
    if sc["legacy_changes_vs_p5"]:
        lines += ["| Anfrage | Rang P4/P5 | Rang heute |", "|---|---|---|"]
        for c in sc["legacy_changes_vs_p5"]:
            lines.append(
                f"| {evaluation._cell(c['query'])} | {_fmt(c['old_rank'])} | "
                f"{_fmt(c['new_rank'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def run_p11_mode(args, config, entries, collected, source) -> int:
    """--diagnose bzw. --tiers-only: ohne Grid, Gewichte aus dem Profil der Konfiguration."""
    all_qfs = [c["qf"] for c in collected]
    dev_idx, test_idx = tuning.split_indices(entries)
    parts = {
        "dev": [all_qfs[i] for i in dev_idx],
        "test": [all_qfs[i] for i in test_idx],
        "full": all_qfs,
    }
    if args.diagnose:
        settings = _settings_for_formula(config, args.formula or "max_plus")
    else:
        settings = _settings_for_formula(config, args.formula)
    commit, dirty = git_info()
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(args.gold),
        "gold_count": len(entries),
        "gnd_enabled": source is not None,
        "llm_enabled": llm_enabled(config),
        "max_results": ranking.MAX_RESULTS,
        "split": {
            "dev_count": len(dev_idx),
            "test_count": len(test_idx),
            "dev": [entries[i].query for i in dev_idx],
            "test": [entries[i].query for i in test_idx],
        },
        "settings": settings.to_dict(),
        "settings_text": _settings_text(settings),
    }
    if args.diagnose:
        prefix = "scoring-diagnose"
        report["diagnose"] = {
            part: tuning.rank_diagnostics(qfs, settings) for part, qfs in parts.items()
        }
        # P12: Kennwerte je Teilmenge (ohne Einzelwerte, die stehen unter „full“)
        report["diagnose_by_subset"] = {
            name: {
                k: v
                for k, v in tuning.rank_diagnostics(qfs, settings).items()
                if k != "per_query"
            }
            for name, qfs in subset_parts(config, entries, all_qfs).items()
        }
        legacy = tuning.START_SETTINGS.replace(formula="legacy")
        legacy_per_query = tuning.per_query_for(all_qfs, legacy)
        max_plus_full = tuning.metrics_for(all_qfs, settings)
        legacy_full = evaluation.aggregate(legacy_per_query)
        legacy_full.pop("mean_response_ms", None)
        report["self_check"] = {
            "max_plus_full": max_plus_full,
            "max_plus_reproduces_p5": all(
                abs(max_plus_full[k] - P5_MAX_PLUS_REFERENCE[k]) <= SELF_CHECK_TOLERANCE
                for k in ("hit@10", "mrr")
            ),
            "p5_max_plus_reference": {
                **P5_MAX_PLUS_REFERENCE,
                "file": P5_MAX_PLUS_FILE,
            },
            "legacy_full": legacy_full,
            "p4_reference": {**P4_REFERENCE, "file": P4_REFERENCE_FILE},
            "p10_reference": {**P10_LEGACY_REFERENCE, "file": P10_LEGACY_FILE},
            "legacy_changes_vs_p5": legacy_changes_since(
                legacy_per_query, P4_REFERENCE_FILE
            ),
            "legacy_changes_vs_p10": legacy_changes_since(
                legacy_per_query, P10_LEGACY_FILE
            ),
        }
        render = render_diagnose_markdown
    else:
        prefix = "scoring-stufen"
        report["tiers"] = derive_and_check_five_tiers(parts, settings)
        render = render_tiers_markdown

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit, prefix)
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render(report), encoding="utf-8")
    print(f"Bericht: {json_path}")
    print(f"Bericht: {md_path}")
    if args.diagnose:
        for part, d in report["diagnose"].items():
            print(
                f"  {part}: Hit@5 {d['hit@5']} / Hit@10 {d['hit@10']} (FastScorer "
                f"{d['hit@5_fast']} / {d['hit@10_fast']}), Ränge 6–10: {d['rank_6_to_10']}"
            )
        sc = report["self_check"]
        print(
            f"Selbstprüfung max_plus voll: {sc['max_plus_full']['hit@10']} / "
            f"{sc['max_plus_full']['mrr']}; legacy voll: {sc['legacy_full']['hit@10']} / "
            f"{sc['legacy_full']['mrr']}"
        )
    else:
        tiers = {k: v for k, v in report["tiers"].items() if k != "shares_by_part"}
        print(f"Stufen: {json.dumps(tiers, ensure_ascii=False)}")
    return 0


# --- P12: Gewichte der eingestellten Formel neu abstimmen --------------------------------------


def subset_parts(config, entries, all_qfs) -> dict[str, list]:
    """Merkmale je Teilmenge (GOLD_SUBSET_SIZES); leer, wenn die Größen nicht passen."""
    sizes = getattr(config, "GOLD_SUBSET_SIZES", None)
    if not sizes:
        return {}
    try:
        labels = evaluation.subset_labels(len(entries), sizes)
    except ValueError:
        return {}
    parts: dict[str, list] = {}
    for qf, label in zip(all_qfs, labels, strict=True):
        parts.setdefault(label, []).append(qf)
    return parts


def render_retune_markdown(report: dict) -> str:
    decision = report["decision"]
    lines = [
        "# RVKompass – Gewichte neu abgestimmt (P12)",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Git-Stand: {report['git_commit']}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report['gold_file']} ({report['gold_count']} Anfragen)",
        (
            f"- Aufteilung: Entwicklung {report['split']['dev_count']}, Test "
            f"{report['split']['test_count']} (je Anfragetyp abwechselnd, wie in P5)"
        ),
        (
            f"- Suchwege: GND {_fmt(report['gnd_enabled'])}, Sprachmodell "
            f"{_fmt(report['llm_enabled'])}"
        ),
        f"- Formel: {report['formula']}, Bewertungen im Grid: {report['grid_size']}",
        "",
        (
            "> Relevanzmaße, keine Wahrscheinlichkeiten. Gewichte nur auf der "
            "Entwicklungshälfte abgestimmt (Ziel MRR, dann Hit@10, dann Nähe zu den "
            "aktuellen Gewichten); entschieden wird auf der Testhälfte."
        ),
        "",
        "## Gewichte",
        "",
        f"- aktuell (Profil in app/config.py): {report['current']['settings_text']}",
        f"- bestes Grid-Ergebnis: {report['candidate']['settings_text']}",
        "",
        "## Kennzahlen",
        "",
        "| Teil | Variante | "
        + " | ".join(evaluation.METRIC_LABELS[k] for k in TABLE_KEYS)
        + " |",
        "|---|---" + "|---" * len(TABLE_KEYS) + "|",
    ]
    part_titles = {
        "test": "Testhälfte",
        "dev": "Entwicklungshälfte",
        "full": "Voller Bestand",
        **{
            f"subset:{k}": f"Teilmenge {v}" for k, v in evaluation.SUBSET_LABELS.items()
        },
    }
    for part in report["current"]["metrics"]:
        for key, title in (("current", "aktuell"), ("candidate", "Grid")):
            m = report[key]["metrics"][part]
            lines.append(
                f"| {part_titles.get(part, part)} | {title} | "
                + " | ".join(_fmt(m[k]) for k in TABLE_KEYS)
                + " |"
            )
    lines += [
        "",
        "## Entscheidung",
        "",
        (
            "Regel (vorab festgelegt): Neue Gewichte werden nur Standard, wenn auf der "
            "Testhälfte Hit@10 und MRR jeweils nicht schlechter sind und mindestens eine der "
            f"beiden um ≥ {_fmt(decision['min_gain'])} besser ist (4 Stellen gerundet)."
        ),
        "",
        "| Bedingung | erfüllt |",
        "|---|---|",
    ]
    for name, ok in decision["conditions"].items():
        lines.append(f"| {name} | {_fmt(ok)} |")
    lines += ["", f"**{decision['reason']}**", ""]
    return "\n".join(lines)


def run_retune(args, config, entries, collected, source) -> int:
    """--retune: Grid nur für die eingestellte Formel, Entscheidung mit accept_p12."""
    all_qfs = [c["qf"] for c in collected]
    dev_idx, test_idx = tuning.split_indices(entries)
    parts = {
        "dev": [all_qfs[i] for i in dev_idx],
        "test": [all_qfs[i] for i in test_idx],
        "full": all_qfs,
    }
    for name, qfs in subset_parts(config, entries, all_qfs).items():
        parts[f"subset:{name}"] = qfs
    current = _settings_for_formula(config, args.formula)
    if current.formula not in scoring.NEW_FORMULAS:
        print(f"Fehler: kein Grid für Formel {current.formula!r}.", file=sys.stderr)
        return 1
    found = tuning.grid_search(parts["dev"], current.formula, current)
    candidate = found["settings"]
    metrics = {
        key: {part: tuning.metrics_for(qfs, s) for part, qfs in parts.items()}
        for key, s in (("current", current), ("candidate", candidate))
    }
    decision = tuning.accept_p12(
        metrics["candidate"]["test"], metrics["current"]["test"]
    )
    commit, dirty = git_info()
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(args.gold),
        "gold_count": len(entries),
        "gnd_enabled": source is not None,
        "llm_enabled": llm_enabled(config),
        "formula": current.formula,
        "grid_size": found["combos"],
        "grid": {
            "weights": {k: list(v) for k, v in tuning.WEIGHT_GRID.items()},
            "path_bonus": list(tuning.PATH_BONUS_GRID),
        },
        "split": {"dev_count": len(dev_idx), "test_count": len(test_idx)},
        "current": {
            "settings": current.to_dict(),
            "settings_text": _settings_text(current),
            "metrics": metrics["current"],
        },
        "candidate": {
            "settings": candidate.to_dict(),
            "settings_text": _settings_text(candidate),
            "metrics": metrics["candidate"],
            "dev_fast": found["dev_fast"],
        },
        "same_as_current": candidate.to_dict() == current.to_dict(),
        "decision": decision,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit, "scoring-retune")
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_retune_markdown(report), encoding="utf-8")
    print(f"Bericht: {json_path}")
    print(f"Bericht: {md_path}")
    for key in ("current", "candidate"):
        m = metrics[key]
        print(
            f"  {key}: Test Hit@10 {m['test']['hit@10']}, MRR {m['test']['mrr']} | "
            f"Dev MRR {m['dev']['mrr']} | voll Hit@10 {m['full']['hit@10']}, MRR "
            f"{m['full']['mrr']}"
        )
    print(f"Grid-Gewichte: {_settings_text(candidate)}")
    print(decision["reason"])
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Abstimmung des Scorings (P5/P11/P12)")
    parser.add_argument(
        "--gold", required=True, help="Goldstandard-CSV (Trennzeichen ;)"
    )
    parser.add_argument("--out", default="reports/", help="Zielverzeichnis")
    parser.add_argument("--limit", type=int, help="nur die ersten N Anfragen")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--diagnose",
        action="store_true",
        help="ohne Grid: Prüfauftrag Hit@5 = Hit@10 (Standard: Formel max_plus)",
    )
    mode.add_argument(
        "--tiers-only",
        action="store_true",
        help="ohne Grid: fünf Relevanzstufen für die eingestellte Formel ableiten",
    )
    mode.add_argument(
        "--retune",
        action="store_true",
        help="P12: Grid nur für die eingestellte Formel, Entscheidung nach accept_p12",
    )
    parser.add_argument(
        "--formula",
        choices=scoring.FORMULAS,
        help="Formel statt SCORING_FORMULA (Gewichte aus dem Profil in app/config.py)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # Claude-Aufrufe der Abstimmung zählen getrennt von echten Suchen (daily_usage)
    config = config_with_overrides(get_config(), {"CLAUDE_CALL_ORIGIN": "eval"})
    gold_path = Path(args.gold)
    if not gold_path.is_file():
        print(f"Fehler: Goldstandard {gold_path} nicht gefunden.", file=sys.stderr)
        return 1
    entries = evaluation.parse_gold(gold_path.read_text(encoding="utf-8").splitlines())
    if args.limit:
        entries = entries[: args.limit]
    out_dir = Path(args.out)
    gate_file, gate_hit10 = latest_eval_report(out_dir)

    service = build_embedding_service(config)
    conn = rvk_repository.get_connection(config.DATABASE_PATH)
    try:
        source = gnd.build_gnd_source(conn, config) if gnd_enabled(config) else None
        collected = collect_features(conn, service, entries, config, source)
    finally:
        conn.close()

    if args.diagnose or args.tiers_only:
        return run_p11_mode(args, config, entries, collected, source)
    if args.retune:
        return run_retune(args, config, entries, collected, source)

    all_qfs = [c["qf"] for c in collected]
    dev_idx, test_idx = tuning.split_indices(entries)
    parts = {
        "dev": [all_qfs[i] for i in dev_idx],
        "test": [all_qfs[i] for i in test_idx],
        "full": all_qfs,
    }

    base = tuning.START_SETTINGS
    legacy = base.replace(formula="legacy")
    variants = [
        {
            "variant": "legacy",
            "settings": legacy,
            **{k: tuning.metrics_for(qfs, legacy) for k, qfs in parts.items()},
        }
    ]
    fast_matches = True
    grid_total = 0
    for variant in scoring.NEW_FORMULAS:
        found = tuning.grid_search(parts["dev"], variant, base)
        grid_total += found["combos"]
        settings = found["settings"]
        metrics = {k: tuning.metrics_for(qfs, settings) for k, qfs in parts.items()}
        if abs(round(found["dev_fast"]["mrr"], 4) - metrics["dev"]["mrr"]) > 1e-4:
            fast_matches = False
        variants.append({"variant": variant, "settings": settings, **metrics})

    legacy_entry = variants[0]
    decision = tuning.accept(
        variants[1:], legacy_entry["test"], P4_REFERENCE, gate_hit10
    )
    chosen = next(v for v in variants if v["variant"] == decision["chosen"])
    chosen_settings = chosen["settings"]
    thresholds = tuning.derive_tier_thresholds(
        tuning.top_hits(parts["dev"], chosen_settings)
    )
    tier_settings = chosen_settings.replace(
        tier_high=thresholds["tier_high"], tier_medium=thresholds["tier_medium"]
    )
    shares = {
        part: tuning.tier_shares(
            tuning.top_hits(parts[part], tier_settings), tier_settings
        )
        for part in ("dev", "test")
    }
    # P11: dieselbe Fünf-Stufen-Ableitung wie --tiers-only, für die gewählte Formel
    five_tiers = derive_and_check_five_tiers(parts, chosen_settings)

    commit, dirty = git_info()
    now = datetime.now(UTC)
    legacy_full = legacy_entry["full"]
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(gold_path),
        "gold_count": len(entries),
        "gnd_enabled": source is not None,
        "llm_enabled": llm_enabled(config),
        "queries_with_gnd_senses": sum(1 for c in collected if c["has_senses"]),
        "queries_with_llm_terms": sum(1 for c in collected if c["has_llm_terms"]),
        "p4_reference": {**P4_REFERENCE, "file": P4_REFERENCE_FILE},
        "gate": {"file": gate_file, "hit@10": gate_hit10},
        "split": {
            "dev_count": len(dev_idx),
            "test_count": len(test_idx),
            "dev": [entries[i].query for i in dev_idx],
            "test": [entries[i].query for i in test_idx],
        },
        "grid": {
            "weights": {k: list(v) for k, v in tuning.WEIGHT_GRID.items()},
            "path_bonus": list(tuning.PATH_BONUS_GRID),
            "sum_cap": list(tuning.SUM_CAP_GRID),
            "start": base.to_dict(),
        },
        "grid_size_total": grid_total,
        "self_check": {
            "legacy_full": legacy_full,
            "diff_hit@10": round(legacy_full["hit@10"] - P4_REFERENCE["hit@10"], 4),
            "diff_mrr": round(legacy_full["mrr"] - P4_REFERENCE["mrr"], 4),
            "fast_matches_reference": fast_matches,
        },
        "variants": [
            {
                "variant": v["variant"],
                "settings": v["settings"].to_dict(),
                "settings_text": _settings_text(v["settings"]),
                "dev": v["dev"],
                "test": v["test"],
                "full": v["full"],
            }
            for v in variants
        ],
        "decision": decision,
        "tiers": {
            "formula": chosen_settings.formula,
            **thresholds,
            "shares": shares,
        },
        "chosen_settings": tier_settings.to_dict(),
        "five_tiers": five_tiers,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit)
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"Bericht: {json_path}")
    print(f"Bericht: {md_path}")
    print(f"Anfragen mit GND-Bedeutungen: {report['queries_with_gnd_senses']}")
    print(
        f"Selbstprüfung legacy voll: Hit@10 {legacy_full['hit@10']}, MRR {legacy_full['mrr']}"
    )
    for v in report["variants"]:
        print(
            f"  {v['variant']}: Test Hit@10 {v['test']['hit@10']}, MRR {v['test']['mrr']} | "
            f"Dev MRR {v['dev']['mrr']} | voll Hit@10 {v['full']['hit@10']}"
        )
    print(decision["reason"])
    print(f"Stufen: {json.dumps(report['tiers'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
