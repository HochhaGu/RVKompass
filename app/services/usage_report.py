"""Monatsstatistik aus den anonymen Tageszählern (Mail, Jahresüberblick, Statistikseite).

Quellen, ausschließlich lesend geöffnet (sqlite-URI `mode=ro`, keine Migration, keine neue Datei):
- `usage_daily` in data/usage.sqlite3 (app/services/usage_stats.py): Suchen, Suchen ohne Treffer,
  Cache-Treffer und Abfragen von GND und Sprachmodell
- `daily_usage` in rvk.sqlite3: Aufrufe der Claude-API je Tag (Kostenbremse)

`load_days` liest beide Tabellen in ein dict {Tag: Zähler}. Alle übrigen Funktionen rechnen nur auf
diesem dict und sind ohne Dateien testbar. Tage sind UTC-Tage wie bei der Zählung.

Werte ohne Grundlage sind `None` und werden als „–“ ausgegeben:
- Monate vor dem ersten Datensatz (nicht 0)
- Anteile und Quoten ohne Nenner (keine Division durch 0)
- relative Veränderung, wenn der Vormonat keine Daten oder den Wert 0 hat
Anteile sind Dezimalzahlen 0–1; Unterschiede von Anteilen werden in Prozentpunkten angegeben.
"""

from __future__ import annotations

import calendar
import csv
import io
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.services import rvk_repository

USAGE_COLUMNS = (
    "requests",
    "zero_hit_requests",
    "gnd_cache_hits",
    "llm_cache_hits",
    "gnd_lookups",
    "llm_lookups",
)
DAY_FIELDS = (*USAGE_COLUMNS, "claude_calls")

MONTH_NAMES = (
    "Jänner",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

# Kennzahlen eines Monats, die als Anteil (0–1) verglichen werden: Unterschied in Prozentpunkten
SHARE_KEYS = ("zero_hit_share", "cache_quote_gnd", "cache_quote_llm")
COMPARE_KEYS = (
    "searches",
    "avg_per_day",
    "peak_count",
    "zero_hit_share",
    "cache_quote_gnd",
    "cache_quote_llm",
    "claude_calls",
)


# --- Lesen ------------------------------------------------------------------------------------


def _open_readonly(path: str | None) -> sqlite3.Connection | None:
    if not path or not Path(path).is_file():
        return None
    try:
        return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _empty_day() -> dict:
    return dict.fromkeys(DAY_FIELDS, 0)


def load_days(usage_path: str | None, rvk_path: str | None) -> dict[str, dict]:
    """{"2026-11-12": {requests: …, claude_calls: …}, …}; fehlende Datei/Tabelle/Spalte → 0."""
    days: dict[str, dict] = {}
    conn = _open_readonly(usage_path)
    if conn is not None:
        try:
            present = [c for c in USAGE_COLUMNS if c in _columns(conn, "usage_daily")]
            if present:
                for row in conn.execute(
                    f"SELECT day, {', '.join(present)} FROM usage_daily"
                ):
                    entry = days.setdefault(row[0], _empty_day())
                    entry.update(
                        {k: int(v or 0) for k, v in zip(present, row[1:], strict=True)}
                    )
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    conn = rvk_repository.get_readonly_connection(rvk_path or "")
    if conn is not None:
        try:
            if "claude_calls" in _columns(conn, "daily_usage"):
                for row in conn.execute("SELECT day, claude_calls FROM daily_usage"):
                    days.setdefault(row[0], _empty_day())["claude_calls"] = int(
                        row[1] or 0
                    )
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return days


def read_rvk_version(rvk_path: str | None) -> str | None:
    conn = rvk_repository.get_readonly_connection(rvk_path or "")
    if conn is None:
        return None
    try:
        return rvk_repository.latest_rvk_version(conn)
    finally:
        conn.close()


# --- Monate und Tage --------------------------------------------------------------------------


def utc_today() -> date:
    return datetime.now(UTC).date()


def parse_month(month: str) -> tuple[int, int]:
    """ "2026-11" → (2026, 11); ValueError bei anderem Format."""
    try:
        year_text, month_text = month.split("-")
        year, number = int(year_text), int(month_text)
    except ValueError as exc:
        raise ValueError(f"Monat im Format JJJJ-MM erwartet: {month!r}") from exc
    if len(year_text) != 4 or len(month_text) != 2 or not 1 <= number <= 12:
        raise ValueError(f"Monat im Format JJJJ-MM erwartet: {month!r}")
    return year, number


def shift_month(month: str, offset: int) -> str:
    year, number = parse_month(month)
    index = year * 12 + (number - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def vormonat(month: str) -> str:
    return shift_month(month, -1)


def month_of(day: date | str) -> str:
    text = day.isoformat() if isinstance(day, date) else day
    return text[:7]


def monatsname(month: str) -> str:
    year, number = parse_month(month)
    return f"{MONTH_NAMES[number - 1]} {year}"


def first_day(days: dict[str, dict]) -> str | None:
    return min(days) if days else None


def _month_bounds(month: str) -> tuple[date, date]:
    year, number = parse_month(month)
    return date(year, number, 1), date(
        year, number, calendar.monthrange(year, number)[1]
    )


def _counted_span(
    month: str, days: dict[str, dict], today: date
) -> tuple[date, date] | None:
    """Tage des Monats, die zählen: ab erstem Datensatz, bis heute. None vor dem Start."""
    start_text = first_day(days)
    if start_text is None:
        return None
    start, end = _month_bounds(month)
    start = max(start, date.fromisoformat(start_text))
    end = min(end, today)
    return (start, end) if start <= end else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _llm_parts(entry: dict) -> tuple[int, int]:
    """(Cache-Treffer, Abfragen) Sprachmodell eines Tages.

    Seit der Monatsstatistik zählt `llm_lookups` die Abfragen. Ältere Tage haben dort 0; für sie
    gilt rückwirkend Abfragen = Cache-Treffer + Claude-API-Aufrufe.
    """
    hits = entry.get("llm_cache_hits", 0)
    if entry.get("llm_lookups", 0):
        return hits, entry["llm_lookups"]
    return hits, hits + entry.get("claude_calls", 0)


def monatswerte(month: str, days: dict[str, dict], today: date | None = None) -> dict:
    """Kennzahlen eines Monats; vor dem ersten Datensatz oder in der Zukunft alles None."""
    today = today or utc_today()
    values = {
        "month": month,
        "label": monatsname(month),
        "has_data": False,
        "days_counted": 0,
        "searches": None,
        "avg_per_day": None,
        "peak_day": None,
        "peak_count": None,
        "zero_hit_requests": None,
        "zero_hit_share": None,
        "cache_quote_gnd": None,
        "cache_quote_llm": None,
        "claude_calls": None,
    }
    span = _counted_span(month, days, today)
    if span is None:
        return values
    rows = [
        (day, days[day])
        for day in sorted(days)
        if span[0].isoformat() <= day <= span[1].isoformat()
    ]
    searches = sum(entry["requests"] for _, entry in rows)
    zero = sum(entry["zero_hit_requests"] for _, entry in rows)
    # GND: nur Tage, an denen Abfragen gezählt wurden (ältere Tage haben keinen Nenner)
    gnd_rows = [entry for _, entry in rows if entry.get("gnd_lookups", 0)]
    llm_parts = [_llm_parts(entry) for _, entry in rows]
    peak = max(rows, key=lambda item: (item[1]["requests"], item[0]), default=None)
    days_counted = (span[1] - span[0]).days + 1
    values.update(
        has_data=True,
        days_counted=days_counted,
        searches=searches,
        avg_per_day=searches / days_counted,
        zero_hit_requests=zero,
        zero_hit_share=_ratio(zero, searches),
        cache_quote_gnd=_ratio(
            sum(e["gnd_cache_hits"] for e in gnd_rows),
            sum(e["gnd_lookups"] for e in gnd_rows),
        ),
        cache_quote_llm=_ratio(
            sum(h for h, _ in llm_parts), sum(n for _, n in llm_parts)
        ),
        claude_calls=sum(entry.get("claude_calls", 0) for _, entry in rows),
    )
    if peak is not None and peak[1]["requests"] > 0:
        values.update(peak_day=peak[0], peak_count=peak[1]["requests"])
    return values


def vergleich(aktuell: dict, vorher: dict) -> dict:
    """Je Kennzahl {"abs", "rel"} bzw. für Anteile {"pp"}; ohne Grundlage None.

    `aktuell` und `vorher` sind Ergebnisse von `monatswerte` (Betrachtungsmonat, Vormonat).
    """
    result = {}
    for key in COMPARE_KEYS:
        now, before = aktuell.get(key), vorher.get(key)
        if key in SHARE_KEYS:
            pp = (
                (now - before) * 100 if now is not None and before is not None else None
            )
            result[key] = {"pp": pp}
            continue
        diff = now - before if now is not None and before is not None else None
        rel = diff / before if diff is not None and before else None
        result[key] = {"abs": diff, "rel": rel}
    return result


def zwoelf_monate(month: str, days: dict[str, dict], today: date | None = None) -> dict:
    """Betrachtungsmonat und die 11 Monate davor (älteste zuerst), mit Summe und Ø.

    Summe und Ø der Suchen nur über Monate mit Daten; ohne solche Monate beide None.
    """
    months = [
        monatswerte(shift_month(month, offset), days, today) for offset in range(-11, 1)
    ]
    with_data = [m for m in months if m["has_data"]]
    total = sum(m["searches"] for m in with_data) if with_data else None
    return {
        "months": months,
        "months_with_data": len(with_data),
        "total": total,
        "average": total / len(with_data) if with_data else None,
        "max": max((m["searches"] for m in with_data), default=0),
    }


def alle_monate(days: dict[str, dict], today: date | None = None) -> list[dict]:
    """Alle Monate vom ersten Datensatz bis heute, neuester zuerst; Lücken zählen als 0."""
    today = today or utc_today()
    start = first_day(days)
    if start is None:
        return []
    first, last = month_of(start), month_of(today)
    months = []
    current = last
    while current >= first:
        months.append(monatswerte(current, days, today))
        current = vormonat(current)
    return months


def alle_tage(
    days: dict[str, dict], month: str | None = None, today: date | None = None
) -> list[dict]:
    """Tageswerte (älteste zuerst) ab erstem Datensatz bis heute; fehlende Tage mit 0.

    Mit `month` nur die Tage dieses Monats.
    """
    today = today or utc_today()
    start_text = first_day(days)
    if start_text is None:
        return []
    start, end = date.fromisoformat(start_text), today
    if month is not None:
        span = _counted_span(month, days, today)
        if span is None:
            return []
        start, end = span
    result = []
    current = start
    while current <= end:
        key = current.isoformat()
        result.append({"day": key, **(days.get(key) or _empty_day())})
        current += timedelta(days=1)
    return result


# --- Ausgabe ----------------------------------------------------------------------------------

DASH = "–"


def _de(number: float, digits: int) -> str:
    text = f"{number:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",")


def fmt_int(value) -> str:
    return DASH if value is None else _de(value, 0)


def fmt_decimal(value, digits: int = 1) -> str:
    return DASH if value is None else _de(value, digits)


def fmt_share(value) -> str:
    """Anteil 0–1 als „12,3 %“ (Anteil an Suchen bzw. Abfragen, keine Konfidenz)."""
    return DASH if value is None else f"{_de(value * 100, 1)} %"


def _signed(text: str, value: float) -> str:
    if value > 0:
        return f"+{text}"
    if value < 0:
        return f"−{text.lstrip('-')}"
    return f"±{text}"


def fmt_signed_int(value) -> str:
    return DASH if value is None else _signed(_de(abs(value), 0), value)


def fmt_signed_decimal(value, digits: int = 1) -> str:
    return DASH if value is None else _signed(_de(abs(value), digits), value)


def fmt_rel(value) -> str:
    return DASH if value is None else f"{_signed(_de(abs(value) * 100, 1), value)} %"


def fmt_pp(value) -> str:
    return DASH if value is None else f"{_signed(_de(abs(value), 1), value)} Pp."


def fmt_day(value: str | None, with_year: bool = True) -> str:
    if not value:
        return DASH
    parsed = date.fromisoformat(value)
    return parsed.strftime("%d.%m.%Y" if with_year else "%d.%m.")


def bar_percent(value, maximum) -> int:
    """Balkenbreite 0–100; ein Wert > 0 bekommt mindestens 1, damit er sichtbar bleibt."""
    if not value or not maximum:
        return 0
    return max(1, round(value / maximum * 100))


FORMATTERS = {
    "fmt_int": fmt_int,
    "fmt_decimal": fmt_decimal,
    "fmt_share": fmt_share,
    "fmt_signed_int": fmt_signed_int,
    "fmt_signed_decimal": fmt_signed_decimal,
    "fmt_rel": fmt_rel,
    "fmt_pp": fmt_pp,
    "fmt_day": fmt_day,
    "bar_percent": bar_percent,
}


def mail_context(
    month: str,
    days: dict[str, dict],
    rvk_version: str | None,
    today: date | None = None,
    stats_url: str = "",
) -> dict:
    """Alles, was die Monatsmail braucht (app/templates/mail/usage_report.*)."""
    today = today or utc_today()
    current = monatswerte(month, days, today)
    previous = monatswerte(vormonat(month), days, today)
    daily = alle_tage(days, month, today)
    return {
        "month": month,
        "current": current,
        "previous": previous,
        "delta": vergleich(current, previous),
        "daily": daily,
        "daily_max": max((d["requests"] for d in daily), default=0),
        "year": zwoelf_monate(month, days, today),
        "rvk_version": rvk_version,
        "stats_url": stats_url,
        "created": today.strftime("%d.%m.%Y"),
        **FORMATTERS,
    }


def subject_for(month: str) -> str:
    return f"RVKompass · Nutzung {monatsname(month)}"


MONTH_CSV_FIELDS = (
    "monat",
    "suchen",
    "schnitt_pro_tag",
    "staerkster_tag",
    "staerkster_tag_suchen",
    "anteil_ohne_treffer",
    "cache_quote_gnd",
    "cache_quote_llm",
    "claude_aufrufe",
)
DAY_CSV_FIELDS = (
    "tag",
    "suchen",
    "ohne_treffer",
    "gnd_abfragen",
    "gnd_cache_treffer",
    "llm_abfragen",
    "llm_cache_treffer",
    "claude_aufrufe",
)


def _csv_number(value, digits: int = 4) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def month_csv_rows(months: list[dict]) -> list[list[str]]:
    return [
        [
            m["month"],
            _csv_number(m["searches"]),
            _csv_number(m["avg_per_day"], 2),
            m["peak_day"] or "",
            _csv_number(m["peak_count"]),
            _csv_number(m["zero_hit_share"]),
            _csv_number(m["cache_quote_gnd"]),
            _csv_number(m["cache_quote_llm"]),
            _csv_number(m["claude_calls"]),
        ]
        for m in months
    ]


def day_csv_rows(day_rows: list[dict]) -> list[list[str]]:
    return [
        [
            d["day"],
            str(d["requests"]),
            str(d["zero_hit_requests"]),
            str(d["gnd_lookups"]),
            str(d["gnd_cache_hits"]),
            str(d["llm_lookups"]),
            str(d["llm_cache_hits"]),
            str(d["claude_calls"]),
        ]
        for d in day_rows
    ]


def to_csv(header: tuple[str, ...], rows: list[list[str]]) -> str:
    """Semikolon-getrennt, UTF-8 mit BOM (öffnet in Excel korrekt), Dezimalpunkt, CRLF."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return "﻿" + buffer.getvalue()
