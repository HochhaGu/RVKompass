"""Diagnose (P8): GND-Bedeutungen je Goldstandard-Anfrage – lokaler Abzug gegen lobid-gnd.

lobid wird nur aus dem Zwischenspeicher `gnd_cache` der RVK-Datenbank gelesen (kein Netz);
fehlt dort eine Antwort, zählt die Anfrage als „lobid nicht im Cache“. Beide Datenbanken werden
nur lesend geöffnet.

Aufruf:
    python scripts/compare_gnd_sources.py --gold eval/goldstandard.csv --out reports/
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
from app.services import evaluation, rvk_repository
from app.services.expansion import gnd


class _NoNetwork:
    def __call__(self, *args, **kwargs):
        raise ConnectionError("Diagnose ohne Netz")


def sense_names(senses) -> list[str]:
    return [s.display_name for s in senses]


def compare(entries, local_source, lobid_source, config) -> dict:
    rows = []
    for entry in entries:
        hits_before = lobid_source.cache_hits
        lobid = gnd.expand_gnd(
            lobid_source,
            entry.query,
            max_senses=config.GND_MAX_SENSES,
            narrower_limit=config.GND_NARROWER_LIMIT,
            search_size=config.GND_SEARCH_SIZE,
        )
        lobid_cached = lobid_source.cache_hits > hits_before
        local = gnd.expand_gnd(
            local_source,
            entry.query,
            max_senses=config.GND_MAX_SENSES,
            narrower_limit=config.GND_NARROWER_LIMIT,
            search_size=config.GND_SEARCH_SIZE,
        )
        local_names, lobid_names = sense_names(local), sense_names(lobid)
        local_terms = {t.term for s in local for t in s.terms}
        lobid_terms = {t.term for s in lobid for t in s.terms}
        rows.append(
            {
                "query": entry.query,
                "lobid_cached": lobid_cached,
                "local_senses": local_names,
                "lobid_senses": lobid_names,
                "same_senses": local_names == lobid_names,
                "same_sense_set": set(local_names) == set(lobid_names),
                "local_terms": len(local_terms),
                "lobid_terms": len(lobid_terms),
                "common_terms": len(local_terms & lobid_terms),
            }
        )
    cached = [r for r in rows if r["lobid_cached"]]
    return {
        "queries": len(rows),
        "lobid_cached": len(cached),
        "same_senses_in_order": sum(r["same_senses"] for r in cached),
        "same_sense_set": sum(r["same_sense_set"] for r in cached),
        "local_with_senses": sum(1 for r in rows if r["local_senses"]),
        "lobid_with_senses": sum(1 for r in cached if r["lobid_senses"]),
        "only_local_senses": sum(
            1 for r in cached if r["local_senses"] and not r["lobid_senses"]
        ),
        "only_lobid_senses": sum(
            1 for r in cached if r["lobid_senses"] and not r["local_senses"]
        ),
        "rows": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", default="reports/")
    args = parser.parse_args(argv)
    config = get_config()
    entries = evaluation.parse_gold(
        Path(args.gold).read_text(encoding="utf-8").splitlines()
    )
    conn = rvk_repository.get_readonly_connection(config.DATABASE_PATH)
    if conn is None:
        print("RVK-Datenbank fehlt.", file=sys.stderr)
        return 1
    lobid = gnd.LobidGndSource(
        conn,
        base_url=config.GND_API_URL,
        cache_days=100000,  # Diagnose: auch ältere Cache-Einträge nutzen
        http_get=_NoNetwork(),
    )
    # Ohne Netz würde lobid nach 3 Fehlern aufhören – hier soll nur der Cache zählen
    lobid.consecutive_failures = -(10**9)
    local = gnd.LocalGndSource(config.GND_DATABASE_PATH, fallback=None)
    try:
        result = compare(entries, local, lobid, config)
    finally:
        local.close()
        conn.close()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gnd-vergleich-{datetime.now(UTC).date().isoformat()}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, ensure_ascii=False))
    print(f"Bericht: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
