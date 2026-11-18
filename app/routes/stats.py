"""Nutzungsstatistik aller Monate: GET /status/statistik und /status/statistik.csv.

Passwortschutz liegt nicht in der App, sondern in Caddy: deploy/caddy/rvkompass.caddy.live leitet
/status/statistik* mit derselben basicauth-Datei wie /status/ an die App weiter. Die App selbst
ist nur über 127.0.0.1:8000 erreichbar.

Daten: app/services/usage_report.py (nur lesend). Keine Suchbegriffe, keine IP-Adressen.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, current_app, render_template, request

from app.services import usage_report

bp = Blueprint("stats", __name__)

CSV_LEVELS = ("monat", "tag")


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _days() -> dict[str, dict]:
    config = current_app.config
    return usage_report.load_days(
        config.get("USAGE_DATABASE_PATH"), config.get("DATABASE_PATH")
    )


@bp.get("/status/statistik")
def statistik():
    days = _days()
    months = usage_report.alle_monate(days)
    days_by_month = {
        m["month"]: list(reversed(usage_report.alle_tage(days, m["month"])))
        for m in months
    }
    html = render_template(
        "status_statistik.html",
        months=months,
        days_by_month=days_by_month,
        **usage_report.FORMATTERS,
    )
    return _no_store(Response(html, mimetype="text/html"))


@bp.get("/status/statistik.csv")
def statistik_csv():
    level = request.args.get("ebene", "monat")
    if level not in CSV_LEVELS:
        abort(400)
    days = _days()
    if level == "monat":
        body = usage_report.to_csv(
            usage_report.MONTH_CSV_FIELDS,
            usage_report.month_csv_rows(list(reversed(usage_report.alle_monate(days)))),
        )
    else:
        body = usage_report.to_csv(
            usage_report.DAY_CSV_FIELDS,
            usage_report.day_csv_rows(usage_report.alle_tage(days)),
        )
    response = Response(body, mimetype="text/csv")
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="rvkompass-statistik-{level}.csv"'
    )
    return _no_store(response)
