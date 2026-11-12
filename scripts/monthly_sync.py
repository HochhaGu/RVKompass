"""Monatlicher RVK-Abgleich (P8) – Aufruf per Cron im Container oder über /internal/sync.

Ablauf und Prüfungen: app/services/rvk_sync/pipeline.py. Dieses Skript verdrahtet nur die echten
Bausteine (Download, Import, Embeddings, Evaluation, Mail, Neustart der Worker).

Aufruf:
    python scripts/monthly_sync.py              # nur bei neuem RVK-Stand
    python scripts/monthly_sync.py --force      # Abgleich auch ohne neuen Stand
    python scripts/monthly_sync.py --dry-run    # prüfen, aber nichts tauschen, keine Mail

Rückgabecode: 0 = kein neuer Stand, getauscht, Probelauf ok oder läuft schon;
1 = nicht bestanden oder Fehler.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_marcxml_dump

from app.config import get_config
from app.services import mailer
from app.services.rvk_sync import pipeline, swap


def build_deps(config) -> pipeline.SyncDeps:
    # Schwere Importe (Modell, lxml) erst hier, damit --help und Tests schnell bleiben
    import build_embeddings
    import evaluate
    import sync_rvk

    def run_embeddings(db_path, paths):
        build_embeddings.build(
            Path(db_path),
            build_embeddings.EmbeddingPaths.from_mapping(paths),
            config.EMBEDDING_MODEL_NAME,
        )

    def run_evaluation(paths, entries):
        overrides = {key: str(path) for key, path in paths.items()}
        return evaluate.evaluate_database(overrides, entries, base_config=config)

    return pipeline.SyncDeps(
        find_dump_url=fetch_marcxml_dump.find_full_dump_url,
        version_from_url=fetch_marcxml_dump.version_from_url,
        download=fetch_marcxml_dump.download_and_extract,
        import_dump=sync_rvk.import_into_new_tables,
        swap_tables=sync_rvk.swap_in_new_tables,
        build_embeddings=run_embeddings,
        evaluate=run_evaluation,
        send_mail=lambda subject, html, text: mailer.deliver(
            config, subject, html, text
        ),
        signal_workers=lambda: swap.signal_gunicorn(config.GUNICORN_PID_PATH),
    )


def summary(result: dict) -> str:
    keys = (
        "status",
        "old_version",
        "new_version",
        "old_count",
        "new_count",
        "diff",
        "backup_dir",
        "mail",
        "error",
    )
    return json.dumps(
        {k: result.get(k) for k in keys if k in result}, ensure_ascii=False
    )


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="Monatlicher RVK-Abgleich")
    parser.add_argument("--force", action="store_true", help="auch ohne neuen Stand")
    parser.add_argument(
        "--dry-run", action="store_true", help="prüfen, nichts tauschen, keine Mail"
    )
    args = parser.parse_args(argv)
    config = get_config()
    result = pipeline.run_monthly_sync(
        config, build_deps(config), force=args.force, dry_run=args.dry_run
    )
    print(summary(result))
    return (
        0
        if result["status"]
        in {
            "no_new_version",
            "swapped",
            "dry_run",
            "already_running",
        }
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
