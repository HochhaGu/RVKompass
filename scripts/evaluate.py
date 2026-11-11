"""Misst die Suchqualität von RVKompass gegen den Goldstandard (eval/goldstandard.csv).

Ruft app.services.ranking.search(...) direkt auf (kein HTTP), standardmäßig zweimal je Anfrage:
mit Begriffserweiterung (wie in app/routes/search.py) und ohne. Ergebnis:
reports/eval-<datum>-<git-shorthash>.json und .md. Vorhandene Berichte werden nie
überschrieben (Zusatz -2, -3 ...).

Aufruf:
    python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/
    python scripts/evaluate.py --gold eval/goldstandard.csv --no-expansion
    python scripts/evaluate.py --gold eval/goldstandard.csv --compare reports/eval-....json

Die Datenbank wird nur über App-Code berührt: Mit API-Schlüssel schreibt die
Begriffserweiterung wie im Betrieb in expansion_cache und daily_usage.
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
from app.services import evaluation, query_expansion, ranking, rvk_repository


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


def run_mode(conn, service, entries, expand, config) -> list[dict]:
    """Wertet alle Anfragen aus; expand=True nutzt die Begriffserweiterung wie die App."""
    per_query = []
    for entry in entries:
        start = time.perf_counter()
        terms: list[str] = []
        if expand and config.ENABLE_QUERY_EXPANSION:
            terms = query_expansion.expand_query(
                conn,
                config.CLAUDE_API_KEY,
                entry.query,
                daily_limit=config.CLAUDE_DAILY_LIMIT,
            )
        hits = ranking.search(conn, service, entry.query, terms)
        seconds = time.perf_counter() - start
        notations = [hit.notation.notation for hit in hits]
        per_query.append(evaluation.evaluate_query(entry, notations, seconds, terms))
    return per_query


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

    conn = rvk_repository.get_connection(config.DATABASE_PATH)
    try:
        no_exp = run_mode(conn, service, entries, expand=False, config=config)
        with_exp = None
        if not args.no_expansion:
            with_exp = run_mode(conn, service, entries, expand=True, config=config)
        missing_notations = find_missing_notations(conn, entries)
        rvk_version = latest_rvk_version(conn)
    finally:
        conn.close()

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
            "enabled": bool(config.ENABLE_QUERY_EXPANSION) and not args.no_expansion,
            "api_key_present": bool(config.CLAUDE_API_KEY),
            "queries_with_terms": sum(1 for q in main_run if q["expanded_terms"]),
        },
        "rvk_version": rvk_version,
        "embedding_model": config.EMBEDDING_MODEL_NAME,
        "by_typ": evaluation.aggregate_by(main_run, "typ"),
        "by_fachgebiet": evaluation.aggregate_by(main_run, "fachgebiet"),
        "worst": evaluation.worst_queries(main_run, 10),
        "missing_notations": missing_notations,
        "per_query": main_run,
    }
    if with_exp is not None:
        report["per_query_no_expansion"] = no_exp
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
            line += f" (ohne Erweiterung: {report['metrics_no_expansion'][key]})"
        print(line)
    if missing_notations:
        print(
            f"Hinweis: {len(missing_notations)} erwartete Notation(en) fehlen im RVK-Bestand."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
