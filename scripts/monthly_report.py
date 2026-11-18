"""Monatliche Nutzungsstatistik per Info-Mail – Aufruf per systemd-Timer am 1. jedes Monats.

Kennzahlen und Rechenregeln: app/services/usage_report.py. Die Datenbanken (USAGE_DATABASE_PATH,
DATABASE_PATH) werden nur lesend geöffnet; das Skript startet die App nicht.

Aufruf:
    python scripts/monthly_report.py                     # Vormonat, Mail senden
    python scripts/monthly_report.py --month 2026-11     # bestimmter Monat
    python scripts/monthly_report.py --dry-run           # keine Mail, HTML nach reports/

Probelauf mit den Live-Daten (nur lesend):
    USAGE_DATABASE_PATH=/srv/rvkompass/data/usage.sqlite3 \\
    DATABASE_PATH=/srv/rvkompass/data/rvk.sqlite3 \\
    python scripts/monthly_report.py --month 2026-11 --dry-run

Rückgabecode: 0 = gesendet oder Probelauf geschrieben, 1 = Fehler (auch: Mail nur als Datei).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.services import mailer, usage_report

logger = logging.getLogger("monthly_report")

DEFAULT_REPORT_DIR = ROOT / "reports"


def build_report(config, month: str, today: date | None = None) -> tuple[str, str, str]:
    """(Betreff, HTML, Text) der Monatsstatistik für `month` (JJJJ-MM)."""
    days = usage_report.load_days(config.USAGE_DATABASE_PATH, config.DATABASE_PATH)
    context = usage_report.mail_context(
        month,
        days,
        usage_report.read_rvk_version(config.DATABASE_PATH),
        today=today,
        stats_url=config.USAGE_STATS_URL,
    )
    html, text = mailer.render_usage_report(context)
    return usage_report.subject_for(month), html, text


def default_month(today: date | None = None) -> str:
    return usage_report.vormonat(
        usage_report.month_of(today or usage_report.utc_today())
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monatliche Nutzungsstatistik")
    parser.add_argument(
        "--month", help="Betrachtungsmonat JJJJ-MM (Standard: Vormonat)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="keine Mail; HTML nach reports/monatsstatistik-<JJJJ-MM>.html",
    )
    parser.add_argument(
        "--report-dir", default=str(DEFAULT_REPORT_DIR), help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if args.month:
        try:
            usage_report.parse_month(args.month)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def main(argv=None, config=None, deliver=mailer.deliver) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)
    config = config or get_config()
    month = args.month or default_month()
    try:
        subject, html, text = build_report(config, month)
    except Exception as exc:  # noqa: BLE001 – Meldung ins Log, Rückgabecode 1
        logger.error("Monatsstatistik nicht erstellt (%s: %s)", type(exc).__name__, exc)
        return 1
    if args.dry_run:
        target = Path(args.report_dir) / f"monatsstatistik-{month}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        print(
            json.dumps(
                {"status": "dry_run", "subject": subject, "file": str(target)},
                ensure_ascii=False,
            )
        )
        return 0
    outcome = deliver(config, subject, html, text, fallback_prefix="usage-mail")
    print(json.dumps({"status": outcome, "subject": subject}, ensure_ascii=False))
    return 0 if outcome == "sent" else 1


if __name__ == "__main__":
    sys.exit(main())
