"""Monatsstatistik (app/services/usage_report.py): Kennzahlen, Vergleich, 12 Monate, Lesen."""

import hashlib
import re
import sqlite3
from datetime import date

import pytest

from app.models import SCHEMA_SQL
from app.services import mailer, usage_report

TODAY = date(2026, 11, 18)


def _day(requests=0, zero=0, gnd_hits=0, llm_hits=0, gnd_n=0, llm_n=0, claude=0):
    return {
        "requests": requests,
        "zero_hit_requests": zero,
        "gnd_cache_hits": gnd_hits,
        "llm_cache_hits": llm_hits,
        "gnd_lookups": gnd_n,
        "llm_lookups": llm_n,
        "claude_calls": claude,
    }


# --- Monate -----------------------------------------------------------------------------------


def test_month_helpers_across_year_change():
    assert usage_report.vormonat("2026-01") == "2025-12"
    assert usage_report.shift_month("2025-12", 1) == "2026-01"
    assert usage_report.shift_month("2026-03", -11) == "2025-04"
    assert usage_report.monatsname("2026-11") == "November 2026"
    assert usage_report.monatsname("2027-01") == "Jänner 2027"


@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "26-11", "2026/11", "abc"])
def test_parse_month_rejects_bad_format(bad):
    with pytest.raises(ValueError):
        usage_report.parse_month(bad)


# --- monatswerte ------------------------------------------------------------------------------


def test_month_values():
    days = {
        "2026-10-01": _day(10, 2, gnd_hits=1, gnd_n=4, llm_hits=1, llm_n=2),
        "2026-10-05": _day(30, 3, gnd_hits=3, gnd_n=4, llm_hits=1, llm_n=2, claude=1),
        "2026-10-31": _day(20, 0),
    }
    values = usage_report.monatswerte("2026-10", days, TODAY)
    assert values["has_data"] is True
    assert values["searches"] == 60
    # 1. bis 31. Oktober: 31 Kalendertage
    assert values["days_counted"] == 31
    assert values["avg_per_day"] == pytest.approx(60 / 31)
    assert (values["peak_day"], values["peak_count"]) == ("2026-10-05", 30)
    assert values["zero_hit_share"] == pytest.approx(5 / 60)
    assert values["cache_quote_gnd"] == pytest.approx(4 / 8)
    assert values["cache_quote_llm"] == pytest.approx(2 / 4)
    assert values["claude_calls"] == 1


def test_empty_month_after_start_is_zero_not_dash():
    days = {"2026-09-15": _day(5)}
    values = usage_report.monatswerte("2026-10", days, TODAY)
    assert values["has_data"] is True
    assert values["searches"] == 0
    assert values["avg_per_day"] == 0
    # ohne Suchen kein stärkster Tag und kein Anteil (keine Division durch 0)
    assert values["peak_day"] is None and values["peak_count"] is None
    assert values["zero_hit_share"] is None
    assert values["cache_quote_gnd"] is None and values["cache_quote_llm"] is None
    assert usage_report.fmt_share(values["zero_hit_share"]) == "–"


def test_months_before_start_and_in_future_have_no_data():
    days = {"2026-11-10": _day(4)}
    before = usage_report.monatswerte("2026-10", days, TODAY)
    future = usage_report.monatswerte("2026-12", days, TODAY)
    for values in (before, future):
        assert values["has_data"] is False
        assert values["searches"] is None
        assert usage_report.fmt_int(values["searches"]) == "–"


def test_no_data_at_all():
    values = usage_report.monatswerte("2026-11", {}, TODAY)
    assert values["has_data"] is False


def test_start_month_and_running_month_count_only_real_days():
    # Start am 10.11., heute 18.11.: 9 Tage zählen, nicht 30
    days = {"2026-11-10": _day(9), "2026-11-18": _day(9)}
    values = usage_report.monatswerte("2026-11", days, TODAY)
    assert values["days_counted"] == 9
    assert values["avg_per_day"] == pytest.approx(2.0)


def test_llm_quote_falls_back_to_claude_calls_for_old_days():
    # Altbestand ohne llm_lookups: Abfragen = Cache-Treffer + Claude-Aufrufe
    days = {"2026-10-01": _day(5, llm_hits=3, claude=1)}
    values = usage_report.monatswerte("2026-10", days, TODAY)
    assert values["cache_quote_llm"] == pytest.approx(3 / 4)


def test_gnd_quote_ignores_days_without_lookup_count():
    days = {
        "2026-10-01": _day(5, gnd_hits=9),  # alter Tag: Treffer ohne Nenner
        "2026-10-02": _day(5, gnd_hits=1, gnd_n=2),
    }
    values = usage_report.monatswerte("2026-10", days, TODAY)
    assert values["cache_quote_gnd"] == pytest.approx(0.5)


# --- vergleich --------------------------------------------------------------------------------


def test_compare_absolute_relative_and_points():
    days = {
        "2026-09-10": _day(40, 4),
        "2026-10-10": _day(50, 10),
    }
    current = usage_report.monatswerte("2026-10", days, TODAY)
    previous = usage_report.monatswerte("2026-09", days, TODAY)
    delta = usage_report.vergleich(current, previous)
    assert delta["searches"]["abs"] == 10
    assert delta["searches"]["rel"] == pytest.approx(0.25)
    # 20 % gegenüber 10 %: +10 Prozentpunkte
    assert delta["zero_hit_share"]["pp"] == pytest.approx(10.0)
    assert usage_report.fmt_pp(delta["zero_hit_share"]["pp"]) == "+10,0 Pp."
    assert usage_report.fmt_rel(delta["searches"]["rel"]) == "+25,0 %"


def test_previous_month_without_data_gives_dash():
    days = {"2026-10-10": _day(50, 10)}
    current = usage_report.monatswerte("2026-10", days, TODAY)
    previous = usage_report.monatswerte("2026-09", days, TODAY)
    delta = usage_report.vergleich(current, previous)
    assert delta["searches"] == {"abs": None, "rel": None}
    assert delta["zero_hit_share"] == {"pp": None}
    assert usage_report.fmt_signed_int(delta["searches"]["abs"]) == "–"
    assert usage_report.fmt_rel(delta["searches"]["rel"]) == "–"


def test_previous_month_with_zero_has_no_relative_change():
    days = {"2026-09-01": _day(0), "2026-10-10": _day(7)}
    current = usage_report.monatswerte("2026-10", days, TODAY)
    previous = usage_report.monatswerte("2026-09", days, TODAY)
    delta = usage_report.vergleich(current, previous)
    assert delta["searches"] == {"abs": 7, "rel": None}


# --- zwoelf_monate ----------------------------------------------------------------------------


def test_twelve_months_across_year_change_and_before_start():
    days = {
        "2025-11-03": _day(10),
        "2026-01-15": _day(30),
        "2026-03-01": _day(20),
    }
    year = usage_report.zwoelf_monate("2026-03", days, TODAY)
    months = [m["month"] for m in year["months"]]
    assert months[0] == "2025-04" and months[-1] == "2026-03"
    assert months[8:10] == ["2025-12", "2026-01"]
    assert len(months) == 12
    by_month = {m["month"]: m for m in year["months"]}
    # vor dem ersten Datensatz (November 2025): „–“, nicht 0
    assert by_month["2025-10"]["searches"] is None
    assert by_month["2025-04"]["has_data"] is False
    # Lücke nach dem Start zählt als 0
    assert by_month["2025-12"]["searches"] == 0
    # Summe und Ø nur über die 5 Monate mit Daten (11/2025 bis 03/2026)
    assert year["months_with_data"] == 5
    assert year["total"] == 60
    assert year["average"] == pytest.approx(12.0)
    assert year["max"] == 30


def test_twelve_months_without_any_data():
    year = usage_report.zwoelf_monate("2026-10", {}, TODAY)
    assert year["total"] is None and year["average"] is None
    assert year["months_with_data"] == 0


# --- Historie ---------------------------------------------------------------------------------


def test_all_months_newest_first_and_all_days_fill_gaps():
    days = {"2026-09-29": _day(1), "2026-11-02": _day(2)}
    months = usage_report.alle_monate(days, TODAY)
    assert [m["month"] for m in months] == ["2026-11", "2026-10", "2026-09"]
    assert months[1]["searches"] == 0
    all_days = usage_report.alle_tage(days, today=TODAY)
    assert all_days[0]["day"] == "2026-09-29" and all_days[-1]["day"] == "2026-11-18"
    assert len(all_days) == 51
    october = usage_report.alle_tage(days, "2026-10", TODAY)
    assert len(october) == 31 and all(d["requests"] == 0 for d in october)
    assert usage_report.alle_tage(days, "2026-08", TODAY) == []
    assert usage_report.alle_monate({}, TODAY) == []


# --- Formate ----------------------------------------------------------------------------------


def test_formatters():
    assert usage_report.fmt_int(12345) == "12 345"
    assert usage_report.fmt_decimal(2.25, 1) in {"2,2", "2,3"}
    assert usage_report.fmt_share(0.125) == "12,5 %"
    assert usage_report.fmt_signed_int(-3) == "−3"
    assert usage_report.fmt_signed_int(0) == "±0"
    assert usage_report.fmt_pp(-1.25) in {"−1,2 Pp.", "−1,3 Pp."}
    assert usage_report.fmt_day("2026-11-03") == "03.11.2026"
    assert usage_report.bar_percent(1, 1000) == 1
    assert usage_report.bar_percent(0, 10) == 0
    assert usage_report.bar_percent(5, 0) == 0


def test_csv_format():
    days = {"2026-10-30": _day(4, 1, gnd_hits=1, gnd_n=2), "2026-11-01": _day(2)}
    months = list(reversed(usage_report.alle_monate(days, TODAY)))
    text = usage_report.to_csv(
        usage_report.MONTH_CSV_FIELDS, usage_report.month_csv_rows(months)
    )
    assert text.startswith("﻿")
    lines = text.lstrip("﻿").split("\r\n")
    assert lines[0] == ";".join(usage_report.MONTH_CSV_FIELDS)
    assert lines[1].split(";") == [
        "2026-10",
        "4",
        "2.00",
        "2026-10-30",
        "4",
        "0.2500",
        "0.5000",
        "",
        "0",
    ]
    assert lines[2].startswith("2026-11;2;")
    assert lines[-1] == ""


# --- Lesen ------------------------------------------------------------------------------------


OLD_USAGE_SCHEMA = """
CREATE TABLE usage_daily (
    day TEXT PRIMARY KEY,
    requests INTEGER NOT NULL DEFAULT 0,
    zero_hit_requests INTEGER NOT NULL DEFAULT 0,
    gnd_cache_hits INTEGER NOT NULL DEFAULT 0,
    llm_cache_hits INTEGER NOT NULL DEFAULT 0
)
"""


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_days_reads_old_schema_without_changing_files(tmp_path):
    usage = tmp_path / "usage.sqlite3"
    conn = sqlite3.connect(usage)
    conn.execute(OLD_USAGE_SCHEMA)
    conn.execute("INSERT INTO usage_daily VALUES ('2026-11-12', 4, 1, 2, 3)")
    conn.commit()
    conn.close()
    rvk = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(rvk)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO daily_usage VALUES ('2026-11-12', 5)")
    conn.execute("INSERT INTO daily_usage VALUES ('2026-11-13', 1)")
    conn.commit()
    conn.close()
    before = (_digest(usage), _digest(rvk))
    files_before = sorted(p.name for p in tmp_path.iterdir())

    days = usage_report.load_days(str(usage), str(rvk))

    assert days["2026-11-12"] == _day(4, 1, gnd_hits=2, llm_hits=3, claude=5)
    assert days["2026-11-13"] == _day(claude=1)
    assert (_digest(usage), _digest(rvk)) == before
    # keine -journal/-wal-Dateien, keine neuen Spalten
    assert sorted(p.name for p in tmp_path.iterdir()) == files_before


def test_load_days_with_missing_files_creates_nothing(tmp_path):
    assert usage_report.load_days(str(tmp_path / "a"), str(tmp_path / "b")) == {}
    assert list(tmp_path.iterdir()) == []


# --- Mail -------------------------------------------------------------------------------------


def _mail(days, month="2026-10"):
    context = usage_report.mail_context(
        month, days, "2026/2", today=TODAY, stats_url="https://example.invalid/s"
    )
    return mailer.render_usage_report(context)


def test_mail_html_inline_only_with_colors_and_link():
    days = {"2026-09-03": _day(10), "2026-10-05": _day(30, 3), "2026-10-06": _day(5)}
    html, text = _mail(days)
    assert "<img" not in html and "<style" not in html and "class=" not in html
    assert "<link" not in html and "<script" not in html
    assert "#1C3A6B" in html and "#C9A24B" in html
    assert 'href="https://example.invalid/s"' in html
    assert "Alle Daten" in html
    assert "RVK-Stand: 2026/2" in html and "RVK-Stand: 2026/2" in text
    assert "Oktober 2026 im Vergleich zu September 2026" in html
    assert "Jahresüberblick: November 2025 bis Oktober 2026" in html
    # 31 Tageszeilen im Tagesverlauf
    assert len(re.findall(r">\d\d\.10\.<", html)) == 31
    assert "+25 (+250,0 %)" in html  # 35 Suchen gegenüber 10


def test_mail_for_month_without_data_and_previous_without_data():
    html, text = _mail({"2026-11-02": _day(3)}, month="2026-10")
    assert "Für Oktober 2026 liegen keine Daten vor." in html
    assert "Für Oktober 2026 liegen keine Daten vor." in text
    assert "–" in html


def test_subject():
    assert usage_report.subject_for("2026-11") == "RVKompass · Nutzung November 2026"
