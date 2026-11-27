import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.config import Config
from app.models import SCHEMA_SQL
from app.services import evaluation, gold_merge

ROOT = Path(__file__).resolve().parent.parent

HEADER = "query;erwartete_notationen;quelle;fachgebiet;typ;anmerkung"


def _entries(*rows: str) -> list[evaluation.GoldEntry]:
    return evaluation.parse_gold([HEADER, *rows])


# --- doppelte Anfragen und Format ------------------------------------------------------------


def test_duplicates_within_extension_and_against_old():
    old = _entries("Getriebe;ZL 4100;;Maschinenbau;Fachbegriff;")
    new = _entries(
        "getriebe ;ZL 4140;;Maschinenbau;Fachbegriff;",
        "Kupplung;ZL 4230;;Maschinenbau;Fachbegriff;",
        "KUPPLUNG;ZL 4230;;Maschinenbau;Fachbegriff;",
        "Pneumatik;ZL 4680;;Maschinenbau;Fachbegriff;",
    )
    dups = gold_merge.find_duplicates(old, new)
    by_query = {gold_merge.query_key(d["query"]): d for d in dups}
    assert set(by_query) == {"getriebe", "kupplung"}
    assert by_query["getriebe"]["old_lines"] == [2]
    assert by_query["kupplung"]["new_lines"] == [3, 4]


def test_no_duplicates():
    old = _entries("Getriebe;ZL 4100;;Maschinenbau;Fachbegriff;")
    new = _entries("Getriebeöl;ZL 4100;;Maschinenbau;Fachbegriff;")
    assert gold_merge.find_duplicates(old, new) == []


def test_query_key_folds_case_and_whitespace():
    assert gold_merge.query_key("  Straße   und  Weg ") == "strasse und weg"


def test_format_errors():
    entries = _entries(
        "A;ZL 4100|PU 2345.4560;;Recht;Fachbegriff;",
        "B;ZL4100|ZLXX 1;;Recht;Fachbegriff;",
    )
    errors = gold_merge.format_errors(entries)
    assert [e["notation"] for e in errors] == ["ZL4100", "ZLXX 1"]


def test_notation_format_regex():
    ok = ["ZL 4100", "F 4320", "PU 2345.4560", "ZE 37100", "ST 170"]
    bad = ["ZL4100", "zl 4100", "ZL 4100 B", "ABCD 1", "ZL 1234567", "ZL 41.", ""]
    assert all(evaluation.NOTATION_FORMAT_RE.fullmatch(n) for n in ok)
    assert not any(evaluation.NOTATION_FORMAT_RE.fullmatch(n) for n in bad)


# --- Vermutungen ------------------------------------------------------------------------------


def test_candidates_cover_swap_insert_delete_and_letter():
    cands = gold_merge.notation_candidates("F 4320")
    assert "F 3420" in cands  # vertauscht
    assert "F 432" in cands  # eine Ziffer weniger
    assert "F 43200" in cands  # eine Ziffer mehr
    assert "G 4320" in cands  # Hauptgruppe ein Buchstabe anders
    assert "F 4320" not in cands
    assert "F 5320" not in cands  # Ersetzen einer Ziffer gehört nicht dazu


def test_guess_only_when_unique():
    assert gold_merge.unique_guess("ZL 4010", {"ZL 4100", "ZL 9999"}) == "ZL 4100"
    assert gold_merge.unique_guess("ZL 4010", {"ZL 4100", "ZL 401"}) is None
    assert gold_merge.unique_guess("ZL 4010", {"ZL 9999"}) is None
    assert gold_merge.unique_guess("kaputt", {"ZL 4100"}) is None


# --- Hauptgruppen -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "spec", "expected"),
    [
        ("AN", "A", True),
        ("BA", "A", False),
        ("CL", "CA - CK", False),
        ("CK", "CA - CK", True),
        ("CL", "CL - CZ", True),
        ("LD", "LD,LH - LO", True),
        ("LK", "LD,LH - LO", True),
        ("LP", "LD,LH - LO", False),
        ("LS", "LD,LP - LY", True),
        ("MT", "MT", True),
        ("XB", "X - Y", True),
        ("YZ", "X - Y", True),
        ("ZA", "X - Y", False),
        ("ST", "SQ - SU", True),
        ("SP", "SQ - SU", False),
        ("ZL", "ZG - ZS", True),
        ("ZX", "ZG - ZS", False),
        ("", "A", False),
    ],
)
def test_main_group_in_range(group, spec, expected):
    assert gold_merge.main_group_in_range(group, spec) is expected


def test_main_group_in_range_covers_all_fallback_specs():
    for spec, _title in Config.MAIN_GROUP_TOP_LEVEL_FALLBACK:
        first = spec.split(",")[0].split("-")[0].strip()
        group = first if len(first) > 1 else first + "A"
        assert gold_merge.main_group_in_range(group, spec), spec


def test_fachgebiet_table_uses_valid_specs():
    for name, spec in gold_merge.FACHGEBIET_MAIN_GROUPS.items():
        for part in spec.split(","):
            assert part.strip(), name
            for code in part.split("-"):
                assert code.strip().isalpha() and code.strip().isupper(), name


def test_outlier_detection_with_multiple_fachgebiete():
    table = {"Informatik": "SQ - SU", "Musik": "LD,LP - LY"}
    maus, homonym, ok, unknown = _entries(
        "Maus;AP 99492;;Informatik;Mehrdeutig;",
        "Satz;LS 1000|ST 100;;Musik / Informatik;Mehrdeutig;",
        "Satz2;AP 1;;Informatik / Musik;Mehrdeutig;",
        "X;ZL 4100;;Unbekannt;Fachbegriff;",
    )
    assert gold_merge.is_main_group_outlier(maus, table) is True
    assert gold_merge.is_main_group_outlier(homonym, table) is False
    assert gold_merge.is_main_group_outlier(ok, table) is True
    assert gold_merge.is_main_group_outlier(unknown, table) is None
    assert gold_merge.split_fachgebiete("Musik / Informatik") == ["Musik", "Informatik"]


# --- Datenbank und Prüfliste ------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT INTO notation (notation, label, hierarchy_path) VALUES (?, ?, ?)",
        [
            ("ZL 4100", "Getriebe", "Technik > Maschinenbau"),
            ("ZL 4230", "Kupplungen", "Technik > Maschinenbau"),
            ("AP 99492", "Eingabegeräte", "Allgemeines > Medien"),
            ("F 3420", "Etwas", "Klassische Philologie"),
        ],
    )
    conn.execute(
        "INSERT INTO sync_log (run_at, source_file, status) VALUES "
        "('2026-11-10', 'rvko_marcxml_2026_2.xml', 'success')"
    )
    conn.commit()
    conn.close()
    return path


def test_lookup_notations_readonly(db):
    from app.services import rvk_repository

    conn = rvk_repository.get_readonly_connection(str(db))
    try:
        found = gold_merge.lookup_notations(conn, ["ZL 4100", "F 4320"])
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO notation (notation) VALUES ('X 1')")
    finally:
        conn.close()
    assert found == {
        "ZL 4100": {"label": "Getriebe", "hierarchy_path": "Technik > Maschinenbau"}
    }


def _load_merge_script():
    spec = importlib.util.spec_from_file_location(
        "merge_gold_extension", ROOT / "scripts" / "merge_gold_extension.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checklist_data(db):
    from app.services import rvk_repository

    entries = _entries(
        "Getriebe;ZL 4100;;Maschinenbau;Fachbegriff;",
        "Maus;AP 99492;;Informatik;Mehrdeutig;",
        "Frauenwahlrecht;ZL 4230|F 4320;;Geschichte;Spezifisch/Kompositum;",
    )
    conn = rvk_repository.get_readonly_connection(str(db))
    try:
        result = _load_merge_script().check_extension(conn, [], entries)
    finally:
        conn.close()
    return result, gold_merge.ChecklistData(
        checks=result["checks"],
        present=result["present"],
        guesses=result["guesses"],
        rvk_version=result["rvk_version"],
        source_file="eval/erweiterung.csv",
    )


def test_checklist_sections_in_order(db):
    result, data = _checklist_data(db)
    assert result["rvk_version"] == "2026/2"
    assert result["guesses"] == {"F 4320": "F 3420"}
    text = gold_merge.render_checklist(data)
    overview = text.index("## Übersicht")
    a = text.index("## a) Notationen, die es im RVK-Stand nicht gibt")
    b = text.index("## b) Hauptgruppen-Ausreißer")
    c = text.index("## c) Alle übrigen Zeilen")
    assert overview < a < b < c
    assert "| Zeilen gesamt | 3 |" in text
    assert "| davon im RVK-Stand gefunden | 3 |" in text
    assert "| übernommen mit gekürzten Notationen | 1 |" in text
    assert "F 3420 (Etwas)" in text[a:b]
    assert "Annahme von Claude" in text[b:c]
    assert "Maus" in text[b:c] and "Allgemeines > Medien" in text[b:c]
    assert "- Getriebe → ZL 4100 (Getriebe)" in text[c:]
    assert "Maus" not in text[c:]


# --- Übernahme --------------------------------------------------------------------------------


def _checks(rows, present):
    lines = [HEADER, *rows]
    return lines, gold_merge.check_rows(evaluation.parse_gold(lines), present)


PRESENT = {"ZL 4100": {}, "ZL 4230": {}, "MS 3155": {}}


def test_plan_merge_keeps_raw_lines_and_marks_trimmed():
    rows = [
        "Getriebe;ZL 4100;RVK-Online;Maschinenbau;Fachbegriff;",
        "Frauenwahlrecht;MS 3155|F 4320;Verbundkatalog;Geschichte;Spezifisch/Kompositum;Kernbegriff: X",
        "Nur Fehler;F 4320|ZZ 1;;Geschichte;Fachbegriff;",
        "Ohne Anmerkung;ZL 4230|F 4320;;Maschinenbau;Fachbegriff",
    ]
    lines, checks = _checks(rows, PRESENT)
    plan = gold_merge.plan_merge(lines, checks, "2026/2")
    assert plan.statuses == ["unchanged", "trimmed", "open", "trimmed"]
    assert plan.append_lines[0] == rows[0]
    assert plan.append_lines[1] == (
        "Frauenwahlrecht;MS 3155;Verbundkatalog;Geschichte;Spezifisch/Kompositum;"
        "Kernbegriff: X – nicht im RVK-Stand 2026/2: F 4320, siehe Prüfliste"
    )
    assert plan.append_lines[2] == (
        "Ohne Anmerkung;ZL 4230;;Maschinenbau;Fachbegriff;"
        "nicht im RVK-Stand 2026/2: F 4320, siehe Prüfliste"
    )
    assert plan.open_lines == [
        (
            "Nur Fehler;F 4320|ZZ 1;;Geschichte;Fachbegriff;"
            "nicht im RVK-Stand 2026/2: F 4320, ZZ 1, siehe Prüfliste"
        )
    ]
    assert plan.header == HEADER
    reparsed = evaluation.parse_gold([HEADER, *plan.append_lines])
    assert [e.expected for e in reparsed] == [["ZL 4100"], ["MS 3155"], ["ZL 4230"]]
    assert reparsed[1].fachgebiet == "Geschichte"
    assert reparsed[1].typ == "Spezifisch/Kompositum"


def test_append_keeps_old_part_byte_identical():
    old = (HEADER + "\nGetriebe;ZL 4100;;;;\n").encode("utf-8")
    new = gold_merge.append_bytes(old, ["Kupplung;ZL 4230;;;;"])
    assert new.startswith(old)
    assert new == old + b"Kupplung;ZL 4230;;;;\n"


def test_append_without_trailing_newline_adds_only_newline():
    old = (HEADER + "\r\nGetriebe;ZL 4100;;;;").encode("utf-8")
    new = gold_merge.append_bytes(old, ["Kupplung;ZL 4230;;;;"])
    assert new == old + b"\nKupplung;ZL 4230;;;;\n"
    assert gold_merge.append_bytes(old, []) == old


def test_open_file_has_same_header():
    data = gold_merge.open_file_bytes(HEADER, ["A;F 1;;;;x"]).decode("utf-8")
    assert data.splitlines() == [HEADER, "A;F 1;;;;x"]


def test_subset_sizes_skip_open_rows():
    script = _load_merge_script()
    entries = _entries(
        "A;ZL 1;;;;",
        "Firmware;ST 170;;;;",
        "Mietrecht;PU 1;;;;",
        "B;PU 2;;;;",
    )
    sizes = script.subset_sizes(
        97, entries, ["unchanged", "open", "trimmed", "open"], "Firmware"
    )
    assert sizes == {"alt": 97, "neu_technik": 1, "neu_andere": 1}
    with pytest.raises(ValueError):
        script.subset_sizes(97, entries, ["unchanged"] * 4, "Gibtsnicht")
