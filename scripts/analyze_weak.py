"""Schwachstellen-Analyse auf Grundlage eines Evaluations- und eines Diagnoseberichts (P12, D).

Liest reports/eval-*.json (per_query) und reports/scoring-diagnose-*.json (Diagnose je Anfrage),
ordnet jeder Anfrage eine Ursache zu (app/services/gold_analysis.py) und schreibt
reports/schwachstellen-<datum>-<hash>.json/.md: die 15 schwächsten Anfragen je Teilmenge, die
schwächsten Fachgebiete und Typen (mindestens 5 Anfragen) und die Häufigkeit der Ursachen.
Keine Suche, keine Datenbank, kein Netz.

Aufruf:
    python scripts/analyze_weak.py --eval reports/eval-….json \\
        --diagnose reports/scoring-diagnose-….json --out reports/
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
from app.services import evaluation
from app.services import gold_analysis as ga
from scripts.evaluate import git_info
from scripts.tune_scoring import report_basename


def _fmt(value) -> str:
    return evaluation._fmt(value)


def _cell(value) -> str:
    return evaluation._cell(value)


def render_markdown(report: dict) -> str:
    lines = [
        "# RVKompass – Schwachstellen (P12)",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Evaluationsbericht: {report['eval_file']}",
        f"- Diagnosebericht: {report['diagnose_file']}",
        f"- Anfragen: {report['n']}",
        "",
        (
            "Ursachen in fester Prüfreihenfolge (die erste zutreffende zählt). „GND-Bedeutung "
            "führt woanders hin“ ist eine Annäherung an „falsche Bedeutung gewählt“: Es gibt "
            "GND-Bedeutungen, die erste erwartete Stelle hat keinen GND-Fundweg und keine der "
            "ersten drei Vorschläge liegt in einer erwarteten Hauptgruppe."
        ),
        "",
        "## Häufigkeit der Ursachen (Anfragen ohne Treffer unter den ersten 10)",
        "",
        "| Ursache | alle | "
        + " | ".join(
            evaluation.SUBSET_LABELS.get(s, s) for s in report["cause_counts_by_subset"]
        )
        + " |",
        "|---|---" + "|---" * len(report["cause_counts_by_subset"]) + "|",
    ]
    for key, label in ga.CAUSES:
        total = report["cause_counts"].get(key, 0)
        if not total:
            continue
        lines.append(
            f"| {label} | {total} | "
            + " | ".join(
                str(c.get(key, 0)) for c in report["cause_counts_by_subset"].values()
            )
            + " |"
        )
    lines += [
        "",
        f"Davon Homonyme (Typ „Mehrdeutig“): {report['homonym_misses']}",
        "",
        "## Schwächste Fachgebiete (MRR, mindestens 5 Anfragen)",
        "",
        "| Fachgebiet | Anfragen | Hit@1 | Hit@10 | MRR |",
        "|---|---|---|---|---|",
    ]
    for g in report["weakest_fachgebiete"]:
        lines.append(
            f"| {_cell(g['name'])} | {g['n']} | {_fmt(g['hit@1'])} | "
            f"{_fmt(g['hit@10'])} | {_fmt(g['mrr'])} |"
        )
    lines += [
        "",
        "## Typen nach MRR (mindestens 5 Anfragen)",
        "",
        "| Typ | Anfragen | Hit@1 | Hit@10 | MRR |",
        "|---|---|---|---|---|",
    ]
    for g in report["weakest_typen"]:
        lines.append(
            f"| {_cell(g['name'])} | {g['n']} | {_fmt(g['hit@1'])} | "
            f"{_fmt(g['hit@10'])} | {_fmt(g['mrr'])} |"
        )
    for subset, rows in report["worst_by_subset"].items():
        lines += [
            "",
            f"## Die {len(rows)} schwächsten Anfragen – {evaluation.SUBSET_LABELS.get(subset, subset)}",
            "",
            "| Anfrage | Typ | erwartet | Top-3 | Rang | Rang ungekürzt | Ursache | Homonym |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {_cell(r['query'])} | {_cell(r.get('typ') or '–')} | "
                f"{_cell(', '.join(r['expected']))} | "
                f"{_cell(', '.join(r.get('top3') or []) or '(keine)')} | "
                f"{_fmt(r.get('rank'))} | {_fmt(r.get('rank_uncut'))} | "
                f"{ga.CAUSE_LABELS[r['cause']]} | {_fmt(r['homonym'])} |"
            )
    lines += ["", "## Vorschläge nach Wirkung", ""]
    for i, p in enumerate(report["proposals"], start=1):
        lines.append(f"{i}. {p['label']}: {p['queries']} Anfragen")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Schwachstellen-Analyse (P12)")
    parser.add_argument("--eval", required=True, help="reports/eval-*.json")
    parser.add_argument(
        "--diagnose", required=True, help="reports/scoring-diagnose-*.json"
    )
    parser.add_argument("--out", default="reports/")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        eval_report = json.loads(Path(args.eval).read_text(encoding="utf-8"))
        diag_report = json.loads(Path(args.diagnose).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Fehler: Bericht unlesbar: {exc}", file=sys.stderr)
        return 1
    per_query = eval_report["per_query"]
    diagnostics = {q["query"]: q for q in diag_report["diagnose"]["full"]["per_query"]}
    sizes = getattr(get_config(), "GOLD_SUBSET_SIZES", None) or {}
    try:
        labels = evaluation.subset_labels(len(per_query), sizes)
    except ValueError:
        labels = None
    rows = ga.annotate(per_query, diagnostics, labels)
    subsets = list(dict.fromkeys(r["subset"] for r in rows))
    by_subset = {s: [r for r in rows if r["subset"] == s] for s in subsets}
    counts = ga.cause_counts(rows)
    commit, dirty = git_info()
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "eval_file": args.eval,
        "diagnose_file": args.diagnose,
        "n": len(rows),
        "cause_counts": counts,
        "cause_counts_by_subset": {s: ga.cause_counts(v) for s, v in by_subset.items()},
        "cause_counts_all_queries": ga.cause_counts(rows, only_misses=False),
        "homonym_misses": sum(
            1 for r in rows if r["homonym"] and not (r.get("rank") and r["rank"] <= 10)
        ),
        "weakest_fachgebiete": ga.weakest_groups(rows, "fachgebiet"),
        "weakest_typen": ga.weakest_groups(rows, "typ", n=10),
        "weakest_fachgebiete_by_subset": {
            s: ga.weakest_groups(v, "fachgebiet") for s, v in by_subset.items()
        },
        "worst_by_subset": {
            s: [
                {
                    k: r.get(k)
                    for k in (
                        "query",
                        "typ",
                        "fachgebiet",
                        "expected",
                        "top3",
                        "rank",
                        "rank_uncut",
                        "cause",
                        "homonym",
                        "gnd_senses",
                        "expected_paths",
                    )
                }
                for r in worst
            ]
            for s, worst in ga.worst_by_subset(rows).items()
        },
        "proposals": ga.proposals_by_impact(counts),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit, "schwachstellen")
    (out_dir / f"{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{name}.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Bericht: {out_dir / f'{name}.json'}")
    print(f"Bericht: {out_dir / f'{name}.md'}")
    print(f"Ursachen: {json.dumps(counts, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
