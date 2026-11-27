"""Leitet GND-Sachgruppe -> RVK-Hauptgruppen aus dem Goldstandard ab und misst sie (P12, C3).

Ablauf:
1. Je Anfrage die GND-Bedeutungen wie in scripts/evaluate.py holen und mit
   ranking.search_result die Notationen je Bedeutung bestimmen (Fachfilter simuliert über
   gold_merge.FACHGEBIET_MAIN_GROUPS – eine Annahme von Claude).
2. Nur auf der Entwicklungshälfte für bisher nicht zugeordnete Codes Vorschläge ableiten
   (subject_mapping.derive_mapping, Schwellen vorab fest: ≥ 3 Anfragen, ≥ 80 % in einer
   Hauptgruppe bzw. erst zwei zusammen, jede mit ≥ 3 Anfragen).
3. Jeden vorgeschlagenen Code im Sachgruppen-Vokabular der DNB prüfen – lokal und nur lesend in
   GND_DATABASE_PATH (Tabelle gnd_category_label, geladen aus GND_SC_VOCAB_URL). Ein Code, der
   sich nicht prüfen lässt, wird nicht vorgeschlagen.
4. Auf der Testhälfte Bedeutung@1 und Bedeutungs-MRR mit alter und erweiterter Zuordnung
   vergleichen; Entscheidung mit scoring_tuning.accept_p12 (gleiche Regel wie C2).
5. Gegenprobe: Die Trefferliste (Grundlage von Hit@k und MRR) ist mit beiden Zuordnungen gleich.

Das Skript trägt nichts in app/config.py ein; es schreibt nur den Bericht
reports/sachgruppen-<datum>-<hash>.json/.md.

Aufruf:
    python scripts/derive_subject_mapping.py --gold eval/goldstandard.csv --out reports/
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
from app.services import evaluation, ranking, rvk_repository
from app.services import scoring_tuning as tuning
from app.services import subject_mapping as sm
from app.services.expansion import gnd
from scripts.evaluate import (
    build_embedding_service,
    config_with_overrides,
    git_info,
    gnd_enabled,
)
from scripts.tune_scoring import report_basename

SENSE_KEYS = ("sense@1", "sense_mrr")


def collect(conn, service, entries, config, source, mapping) -> tuple[list, list, bool]:
    """QueryRecords, Trefferlisten je Anfrage und ob die eigene Reihenfolge der Bedeutungen
    mit ranking.rank_senses übereinstimmt."""
    records, hits, order_ok = [], [], True
    for entry in entries:
        senses = (
            gnd.gnd_senses_for_query(conn, config, entry.query, source=source)
            if source is not None
            else []
        )
        flt = sm.filter_for(entry.fachgebiet)
        result = ranking.search_result(
            conn,
            service,
            entry.query,
            [],
            gnd_senses=senses,
            subject_filter=flt,
            mapping=mapping,
        )
        by_id = {id(r.sense): r for r in result.senses}
        ids = [nid for r in result.senses for nid in r.notation_ids]
        notations = rvk_repository.get_notations_by_ids(conn, list(dict.fromkeys(ids)))
        sense_records = []
        for sense in senses:
            r = by_id[id(sense)]
            sense_records.append(
                sm.SenseRecord(
                    codes=tuple(code for code, _ in sense.subject_categories),
                    notations=frozenset(
                        notations[nid].notation
                        for nid in r.notation_ids
                        if nid in notations
                    ),
                    hit_count=r.hit_count,
                )
            )
        record = sm.QueryRecord(
            entry.query, tuple(entry.expected), entry.fachgebiet, tuple(sense_records)
        )
        own = sm.order_senses(record.senses, flt, mapping)
        expected_order = [sense_records[senses.index(r.sense)] for r in result.senses]
        if [id(s) for s in own] != [id(s) for s in expected_order]:
            order_ok = False
        records.append(record)
        hits.append([h.notation.notation for h in result.hits])
    return records, hits, order_ok


def vocabulary(config, codes) -> tuple[dict[str, str], dict]:
    """Benennungen aus dem lokalen DNB-Sachgruppen-Vokabular (nur lesend) und dessen Stand."""
    conn = rvk_repository.get_readonly_connection(config.GND_DATABASE_PATH)
    if conn is None:
        return {}, {"available": False}
    try:
        codes = sorted(set(codes))
        labels = {}
        if codes:
            rows = conn.execute(
                "SELECT code, label FROM gnd_category_label WHERE code IN "
                f"({','.join('?' * len(codes))})",
                codes,
            ).fetchall()
            labels = {r[0]: r[1] for r in rows}
        meta_row = conn.execute(
            "SELECT version, source_file, loaded_at FROM gnd_meta ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM gnd_category_label").fetchone()[0]
    finally:
        conn.close()
    meta = {
        "available": True,
        "source": getattr(config, "GND_SC_VOCAB_URL", ""),
        "labels_total": count,
        "gnd_version": meta_row[0] if meta_row else None,
        "gnd_source_file": meta_row[1] if meta_row else None,
        "loaded_at": meta_row[2] if meta_row else None,
    }
    return labels, meta


def _fmt(value) -> str:
    return evaluation._fmt(value)


def render_markdown(report: dict) -> str:
    vocab = report["vocabulary"]
    lines = [
        "# RVKompass – GND-Sachgruppen → RVK-Hauptgruppen (P12)",
        "",
        "## Übersicht",
        "",
        f"- Zeitpunkt: {report['timestamp']}",
        f"- Git-Stand: {report['git_commit']}"
        + (" (mit ungespeicherten Änderungen)" if report.get("git_dirty") else ""),
        f"- Goldstandard: {report['gold_file']} ({report['gold_count']} Anfragen)",
        (
            f"- Aufteilung: Entwicklung {report['split']['dev_count']}, Test "
            f"{report['split']['test_count']} (wie in P5)"
        ),
        (
            f"- Sachgruppen-Vokabular: lokal, nur lesend ({vocab.get('labels_total', '–')} "
            f"Codes, geladen am {vocab.get('loaded_at') or '–'} aus {vocab.get('source') or '–'})"
        ),
        "",
        (
            "> Die Zuordnung wirkt nur auf die Reihenfolge der GND-Bedeutungen bei gesetztem "
            "Fachfilter. Der Fachfilter je Anfrage ist simuliert: Hauptgruppen ihres "
            "Fachgebiets laut `FACHGEBIET_MAIN_GROUPS` – **eine Annahme von Claude**. Die "
            "Messung von Bedeutung@1 beruht auf dieser Annahme."
        ),
        "",
        "## Regeln (vorab festgelegt)",
        "",
        (
            f"- Vorschlag je bisher nicht zugeordnetem Code nur auf der Entwicklungshälfte: "
            f"mindestens {sm.MIN_QUERIES} Anfragen, mindestens {round(sm.MIN_SHARE * 100)} % "
            "davon mit erwarteter Notation in einer Hauptgruppe; eine zweite Hauptgruppe nur, "
            f"wenn erst beide zusammen {round(sm.MIN_SHARE * 100)} % erreichen und jede "
            f"mindestens {sm.MIN_QUERIES} Anfragen hat. Bestehende Einträge bleiben."
        ),
        "- Code muss im DNB-Sachgruppen-Vokabular stehen, sonst kein Vorschlag.",
        (
            "- Übernahme, wenn auf der Testhälfte Bedeutung@1 und Bedeutungs-MRR nicht "
            "schlechter sind und eine um ≥ 0.01 besser ist (gleiche Regel wie bei den "
            f"Gewichten). Gezählt nur Anfragen mit mindestens {sm.MIN_SENSES} Bedeutungen."
        ),
        "",
        "## Vorschläge",
        "",
    ]
    proposals = report["proposals"]
    if proposals:
        lines += [
            "| Code | DNB-Benennung | Hauptgruppen | Anfragen | gestützt | Anteil | im Vokabular | Beispiele |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for code, p in proposals.items():
            lines.append(
                f"| {code} | {evaluation._cell(p.get('label') or '–')} | "
                f"{', '.join(p['groups'])} | {p['queries']} | {p['support']} | "
                f"{_fmt(p['share'])} | {_fmt(p['verified'])} | "
                f"{evaluation._cell(', '.join(p['examples']))} |"
            )
    else:
        lines.append("Keine – kein Code erfüllt die Schwellen.")
    lines += [
        "",
        "## Messung",
        "",
        "| Teil | Zuordnung | Anfragen (≥ 2 Bedeutungen) | Bedeutung@1 | Bedeutungs-MRR |",
        "|---|---|---|---|---|",
    ]
    titles = {
        "test": "Testhälfte",
        "dev": "Entwicklungshälfte",
        "full": "Voller Bestand",
    }
    for part, title in titles.items():
        for key, name in (("current", "bisher"), ("extended", "erweitert")):
            m = report["metrics"][key][part]
            lines.append(
                f"| {title} | {name} | {m['n']} | {_fmt(m['sense@1'])} | "
                f"{_fmt(m['sense_mrr'])} |"
            )
    decision = report["decision"]
    lines += [
        "",
        f"**{decision['reason']}**",
        "",
        "## Gegenproben",
        "",
        (
            "- Eigene Reihenfolge der Bedeutungen gleich ranking.rank_senses (bisherige "
            f"Zuordnung): {_fmt(report['self_check']['order_matches_current'])}"
        ),
        (
            "- dasselbe mit erweiterter Zuordnung (Testhälfte): "
            f"{_fmt(report['self_check']['order_matches_extended'])}"
        ),
        (
            "- Trefferliste (Grundlage von Hit@k und MRR) mit beiden Zuordnungen gleich "
            f"(Testhälfte, {report['self_check']['hits_compared']} Anfragen): "
            f"{_fmt(report['self_check']['hits_identical'])}"
        ),
        "",
    ]
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="GND-Sachgruppen → RVK-Hauptgruppen (P12)"
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", default="reports/")
    parser.add_argument("--limit", type=int)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config = config_with_overrides(get_config(), {"CLAUDE_CALL_ORIGIN": "eval"})
    gold_path = Path(args.gold)
    entries = evaluation.parse_gold(gold_path.read_text(encoding="utf-8").splitlines())
    if args.limit:
        entries = entries[: args.limit]
    current = {k: tuple(v) for k, v in config.GND_SUBJECT_CATEGORY_TO_RVK.items()}
    dev_idx, test_idx = tuning.split_indices(entries)

    service = build_embedding_service(config)
    conn = rvk_repository.get_connection(config.DATABASE_PATH)
    try:
        source = gnd.build_gnd_source(conn, config) if gnd_enabled(config) else None
        records, hits, order_ok = collect(
            conn, service, entries, config, source, current
        )
        dev = [records[i] for i in dev_idx]
        test = [records[i] for i in test_idx]
        raw = sm.derive_mapping(dev, current)
        labels, vocab = vocabulary(config, raw)
        proposals = {
            code: {**p, "label": labels.get(code), "verified": code in labels}
            for code, p in raw.items()
        }
        verified = {c: p for c, p in proposals.items() if p["verified"]}
        extended = sm.extended_mapping(current, verified)
        # Gegenprobe auf der Testhälfte: Trefferliste und Reihenfolge mit erweiterter Zuordnung
        test_entries = [entries[i] for i in test_idx]
        _, hits_ext, order_ext_ok = collect(
            conn, service, test_entries, config, source, extended
        )
    finally:
        close = getattr(source, "close", None) if gnd_enabled(config) else None
        if callable(close):
            close()
        conn.close()
    hits_identical = all(hits[i] == h for i, h in zip(test_idx, hits_ext, strict=True))
    parts = {"dev": dev, "test": test, "full": records}
    metrics = {
        "current": {p: sm.sense_metrics(r, current) for p, r in parts.items()},
        "extended": {p: sm.sense_metrics(r, extended) for p, r in parts.items()},
    }
    decision = tuning.accept_p12(
        metrics["extended"]["test"], metrics["current"]["test"], keys=SENSE_KEYS
    )
    if not verified:
        decision = {
            **decision,
            "accepted": False,
            "reason": "Nicht übernommen: kein geprüfter Vorschlag.",
        }
    commit, dirty = git_info()
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(gold_path),
        "gold_count": len(entries),
        "split": {"dev_count": len(dev_idx), "test_count": len(test_idx)},
        "thresholds": {
            "min_queries": sm.MIN_QUERIES,
            "min_share": sm.MIN_SHARE,
            "min_senses": sm.MIN_SENSES,
        },
        "vocabulary": vocab,
        "current_mapping": {k: list(v) for k, v in current.items()},
        "proposals": {
            c: {**p, "groups": list(p["groups"])} for c, p in proposals.items()
        },
        "extended_mapping": {k: list(v) for k, v in extended.items()},
        "metrics": metrics,
        "decision": decision,
        "queries_with_senses": sum(1 for r in records if r.senses),
        "queries_with_2_senses": sum(1 for r in records if len(r.senses) >= 2),
        "self_check": {
            "order_matches_current": order_ok,
            "order_matches_extended": order_ext_ok,
            "hits_identical": hits_identical,
            "hits_compared": len(test_idx),
        },
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit, "sachgruppen")
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Bericht: {json_path}")
    print(f"Bericht: {md_path}")
    print(f"Vorschläge: {json.dumps(report['proposals'], ensure_ascii=False)}")
    for key in ("current", "extended"):
        print(f"  {key}: {metrics[key]['test']} (Test), {metrics[key]['full']} (voll)")
    print(decision["reason"])
    print(f"Gegenproben: {report['self_check']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
