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


def report_basename(out_dir: Path, date: str, commit: str) -> str:
    """scoring-<datum>-<hash>; existiert die Datei schon, wird -2, -3 ... angehängt."""
    base = f"scoring-{date}-{commit}"
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Abstimmung des Scorings (P5)")
    parser.add_argument(
        "--gold", required=True, help="Goldstandard-CSV (Trennzeichen ;)"
    )
    parser.add_argument("--out", default="reports/", help="Zielverzeichnis")
    parser.add_argument("--limit", type=int, help="nur die ersten N Anfragen")
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
