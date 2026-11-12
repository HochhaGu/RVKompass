"""Misst die Suchqualität von RVKompass gegen den Goldstandard (eval/goldstandard.csv).

Ruft app.services.ranking.search_result(...) direkt auf (kein HTTP), standardmäßig in vier
Varianten je Anfrage (P4): ohne Erweiterung, nur GND, nur LLM (geprüft), GND+LLM (wie in
app/routes/search.py). „metrics“ ist der Lauf GND+LLM. Ergebnis:
reports/eval-<datum>-<git-shorthash>.json und .md. Vorhandene Berichte werden nie
überschrieben (Zusatz -2, -3 ...).

Aufruf:
    python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/
    python scripts/evaluate.py --gold eval/goldstandard.csv --no-expansion
    python scripts/evaluate.py --gold eval/goldstandard.csv --compare reports/eval-....json

Die Datenbank wird nur über App-Code berührt: Mit API-Schlüssel schreibt die
Begriffserweiterung wie im Betrieb in expansion_cache und daily_usage; die GND-Erweiterung (P3)
schreibt erfolgreiche lobid-Antworten in die Laufzeit-Tabelle gnd_cache.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services import (
    evaluation,
    query_expansion,
    ranking,
    rvk_repository,
    scoring,
    scoring_tuning,
)
from app.services.expansion import gnd, llm


def git_info(cwd: Path = ROOT) -> tuple[str, bool]:
    """Kurz-Hash und ob es ungespeicherte Änderungen gibt; ohne git: ("nogit", False)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "nogit", False
    return commit or "nogit", bool(status)


def report_basename(out_dir: Path, date: str, commit: str) -> str:
    """eval-<datum>-<hash>; existiert .json oder .md schon, wird -2, -3 ... angehängt."""
    base = f"eval-{date}-{commit}"
    name = base
    counter = 2
    while (out_dir / f"{name}.json").exists() or (out_dir / f"{name}.md").exists():
        name = f"{base}-{counter}"
        counter += 1
    return name


def find_missing_notations(conn, entries: list[evaluation.GoldEntry]) -> list[dict]:
    """Erwartete Notationen, die es in der Tabelle notation nicht gibt (nur lesend)."""
    missing = []
    for entry in entries:
        for notation in entry.expected:
            row = conn.execute(
                "SELECT 1 FROM notation WHERE notation = ?", (notation,)
            ).fetchone()
            if row is None:
                missing.append({"notation": notation, "query": entry.query})
    return missing


def latest_rvk_version(conn) -> str | None:
    try:
        row = conn.execute(
            "SELECT run_at, source_file, status FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return " ".join(str(v) for v in tuple(row) if v)


def gnd_enabled(config) -> bool:
    return bool(getattr(config, "ENABLE_GND_EXPANSION", False))


def llm_enabled(config) -> bool:
    return bool(getattr(config, "ENABLE_QUERY_EXPANSION", False))


# Varianten der Evaluation (P4): Schlüssel -> (GND-Bedeutungen nutzen, LLM-Begriffe nutzen)
VARIANTS = {
    "none": (False, False),
    "gnd": (True, False),
    "llm": (False, True),
    "gnd_llm": (True, True),
}


def _suggestion_record(suggestion) -> dict:
    return {
        "begriff": suggestion.term,
        "beziehung": suggestion.llm_relation,
        "begruendung": suggestion.rationale,
        "verified": suggestion.verified,
        "verified_via": suggestion.verified_via,
        "matched_term": suggestion.matched_term,
        "gnd_id": suggestion.gnd_id,
    }


def run_mode(
    conn,
    service,
    entries,
    config,
    use_gnd: bool = False,
    use_llm: bool = False,
    source=None,
    llm_stats: dict | None = None,
) -> list[dict]:
    """Wertet alle Anfragen in einer Variante aus.

    `use_gnd` nutzt GND-Bedeutungen wie die App, `use_llm` die geprüften LLM-Begriffe. Die
    Prüfung der LLM-Vorschläge nutzt die GND-Quelle `source` auch in der Variante „nur LLM“.
    Die Vorschläge des Sprachmodells sind je Anfrage gecacht; der zweite LLM-Lauf kostet daher
    keine weiteren Aufrufe.
    """
    per_query = []
    for entry in entries:
        start = time.perf_counter()
        senses = []
        suggestions = []
        if use_gnd and source is not None:
            senses = gnd.gnd_senses_for_query(conn, config, entry.query, source=source)
        if use_llm and llm_enabled(config):
            suggestions = llm.llm_suggestions_for_query(
                conn, config, entry.query, source=source, stats=llm_stats
            )
        llm_terms = llm.verified_terms(suggestions, entry.query)
        result = ranking.search_result(
            conn, service, entry.query, llm_terms, gnd_senses=senses
        )
        seconds = time.perf_counter() - start
        notations = [hit.notation.notation for hit in result.hits]
        gnd_terms = [
            f"{term.relation}: {term.term}" for sense in senses for term in sense.terms
        ]
        per_query.append(
            evaluation.evaluate_query(
                entry,
                notations,
                seconds,
                [term.term for term in llm_terms],
                gnd_terms=gnd_terms,
                gnd_senses=[sense.display_name for sense in senses],
                llm_suggestions=[_suggestion_record(s) for s in suggestions],
            )
        )
    return per_query


def llm_summary(config, per_query: list[dict], stats: dict) -> dict:
    """Kennzahlen zum Sprachmodell-Echttest (ohne Schlüsselinhalt)."""
    suggestions = [s for q in per_query for s in q.get("llm_suggestions", [])]
    verified = [s for s in suggestions if s["verified"]]
    return {
        "enabled": llm_enabled(config),
        "model": getattr(config, "CLAUDE_MODEL", None),
        "sdk_version": query_expansion.sdk_version(),
        "structured_output": query_expansion.sdk_supports_output_config(),
        "api_key_present": bool(getattr(config, "CLAUDE_API_KEY", "")),
        "api_calls": stats.get("api_calls", 0),
        "api_errors": stats.get("api_errors", 0),
        "cache_hits": stats.get("cache_hits", 0),
        "limit_reached": stats.get("limit_reached", 0),
        "queries_with_suggestions": sum(
            1 for q in per_query if q.get("llm_suggestions")
        ),
        "suggestions_total": len(suggestions),
        "suggestions_verified": len(verified),
        "verified_via_register": sum(
            1 for s in verified if s["verified_via"] == "rvk_register"
        ),
        "verified_via_gnd": sum(1 for s in verified if s["verified_via"] == "gnd"),
    }


def metrics_by_split(entries, per_query: list[dict]) -> dict[str, dict]:
    """Kennzahlen getrennt nach Entwicklungs- und Testhälfte (Aufteilung wie in P5)."""
    dev_idx, test_idx = scoring_tuning.split_indices(entries)
    result = {}
    for key, indices in (("dev", dev_idx), ("test", test_idx)):
        if indices:
            result[key] = evaluation.aggregate([per_query[i] for i in indices])
    return result


def build_embedding_service(config):
    from app.services import embedding_service

    return embedding_service.EmbeddingService(
        model_name=config.EMBEDDING_MODEL_NAME,
        embeddings_path=config.EMBEDDINGS_PATH,
        embeddings_meta_path=config.EMBEDDINGS_META_PATH,
        register_term_embeddings_path=config.REGISTER_TERM_EMBEDDINGS_PATH,
        register_term_meta_path=config.REGISTER_TERM_META_PATH,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluation der RVK-Suche gegen den Goldstandard"
    )
    parser.add_argument(
        "--gold", required=True, help="Goldstandard-CSV (Trennzeichen ;)"
    )
    parser.add_argument(
        "--out", default="reports/", help="Zielverzeichnis für die Berichte"
    )
    parser.add_argument(
        "--no-expansion",
        action="store_true",
        help="nur ohne Begriffserweiterung messen",
    )
    parser.add_argument(
        "--compare", help="älterer Bericht (JSON) für Vorher/Nachher-Differenz"
    )
    parser.add_argument(
        "--limit", type=int, help="nur die ersten N Anfragen (zum Entwickeln)"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config = get_config()

    gold_path = Path(args.gold)
    if not gold_path.is_file():
        print(f"Fehler: Goldstandard {gold_path} nicht gefunden.", file=sys.stderr)
        return 1
    try:
        entries = evaluation.parse_gold(
            gold_path.read_text(encoding="utf-8").splitlines()
        )
    except ValueError as exc:
        print(f"Fehler im Goldstandard {gold_path}: {exc}", file=sys.stderr)
        return 1
    if args.limit:
        entries = entries[: args.limit]

    old_report = None
    if args.compare:
        try:
            old_report = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"Fehler: Vergleichsbericht {args.compare} unlesbar: {exc}",
                file=sys.stderr,
            )
            return 1

    needed = [
        config.DATABASE_PATH,
        config.EMBEDDINGS_PATH,
        config.EMBEDDINGS_META_PATH,
        config.REGISTER_TERM_EMBEDDINGS_PATH,
        config.REGISTER_TERM_META_PATH,
    ]
    missing_files = [p for p in needed if not Path(p).exists()]
    if missing_files:
        print(
            "Fehler: Datenbestand fehlt (erst scripts/sync_rvk.py und "
            "scripts/build_embeddings.py ausführen): " + ", ".join(missing_files),
            file=sys.stderr,
        )
        return 1

    service = build_embedding_service(config)
    service.embed_query(
        "Aufwärmen"
    )  # Modell-Ladezeit nicht in die erste Messung einrechnen

    llm_stats: dict = {}
    runs: dict[str, list[dict]] = {}
    conn = rvk_repository.get_connection(config.DATABASE_PATH)
    try:
        # Eine gemeinsame GND-Quelle für alle Läufe: Nach mehreren Fehlern in Folge (z. B. ohne
        # Netz) fragt sie nicht mehr an, damit die Evaluation nicht an Timeouts hängt.
        source = (
            gnd.build_gnd_source(conn, config)
            if gnd_enabled(config) and not args.no_expansion
            else None
        )
        variants = ["none"] if args.no_expansion else list(VARIANTS)
        for key in variants:
            use_gnd, use_llm = VARIANTS[key]
            runs[key] = run_mode(
                conn,
                service,
                entries,
                config,
                use_gnd=use_gnd,
                use_llm=use_llm,
                source=source,
                llm_stats=llm_stats,
            )
        missing_notations = find_missing_notations(conn, entries)
        rvk_version = latest_rvk_version(conn)
    finally:
        conn.close()

    no_exp = runs["none"]
    with_exp = None if args.no_expansion else runs["gnd_llm"]
    main_run = no_exp if args.no_expansion else with_exp
    commit, dirty = git_info()
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(timespec="seconds"),
        "mode": "no_expansion" if args.no_expansion else "with_expansion",
        "metrics": evaluation.aggregate(main_run),
        "metrics_no_expansion": evaluation.aggregate(no_exp),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(gold_path),
        "gold_count": len(entries),
        "expansion": {
            "enabled": llm_enabled(config) and not args.no_expansion,
            "api_key_present": bool(config.CLAUDE_API_KEY),
            "queries_with_terms": sum(1 for q in main_run if q["expanded_terms"]),
            "gnd_enabled": gnd_enabled(config) and not args.no_expansion,
            "queries_with_gnd_senses": sum(1 for q in main_run if q["gnd_senses"]),
        },
        "rvk_version": rvk_version,
        "embedding_model": config.EMBEDDING_MODEL_NAME,
        "scoring": scoring.ScoringSettings.from_config(config).to_dict(),
        "metrics_by_split": metrics_by_split(entries, main_run),
        "by_typ": evaluation.aggregate_by(main_run, "typ"),
        "by_fachgebiet": evaluation.aggregate_by(main_run, "fachgebiet"),
        "worst": evaluation.worst_queries(main_run, 10),
        "missing_notations": missing_notations,
        "per_query": main_run,
    }
    if with_exp is not None:
        report["per_query_no_expansion"] = no_exp
        report["metrics_variants"] = {
            key: evaluation.aggregate(run) for key, run in runs.items()
        }
        report["by_typ_variants"] = evaluation.aggregate_variants_by(runs, "typ")
        report["llm"] = llm_summary(config, with_exp, llm_stats)
        report["llm_effect"] = {
            "llm_vs_none": evaluation.rank_changes(runs["none"], runs["llm"]),
            "gnd_llm_vs_gnd": evaluation.rank_changes(runs["gnd"], runs["gnd_llm"]),
        }
    if old_report is not None:
        report["compare_file"] = args.compare
        report["compare"] = evaluation.compare_metrics(report["metrics"], old_report)
        report["compare_per_query"] = evaluation.compare_per_query(main_run, old_report)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report_basename(out_dir, now.date().isoformat(), commit)
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(evaluation.render_markdown(report), encoding="utf-8")

    print(f"Bericht: {json_path}")
    print(f"Bericht: {md_path}")
    print(f"Modus: {report['mode']}")
    for key in evaluation.METRIC_KEYS:
        line = f"  {evaluation.METRIC_LABELS[key]}: {report['metrics'][key]}"
        if with_exp is not None:
            variants = report["metrics_variants"]
            line += (
                f" (ohne: {variants['none'][key]}, nur GND: {variants['gnd'][key]},"
                f" nur LLM: {variants['llm'][key]})"
            )
        print(line)
    if with_exp is not None:
        info = report["llm"]
        print(
            f"Sprachmodell: {info['model']} (anthropic {info['sdk_version']}), "
            f"Schlüssel vorhanden: {info['api_key_present']}, Aufrufe: {info['api_calls']}, "
            f"Fehler: {info['api_errors']}, Vorschläge: {info['suggestions_total']}, "
            f"davon geprüft: {info['suggestions_verified']}"
        )
    if missing_notations:
        print(
            f"Hinweis: {len(missing_notations)} erwartete Notation(en) fehlen im RVK-Bestand."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
