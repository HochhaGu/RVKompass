from pathlib import Path

import pytest

from app.services import evaluation as ev

GOLD_PATH = Path(__file__).resolve().parent.parent / "eval" / "goldstandard.csv"


@pytest.mark.parametrize(
    "raw,expected",
    [("zn  8330 ", "ZN 8330"), ("ST 270", "ST 270"), (" qk\t000", "QK 000")],
)
def test_normalize_notation(raw, expected):
    assert ev.normalize_notation(raw) == expected


@pytest.mark.parametrize(
    "notation,group",
    [
        ("ZN 8330", "ZN"),
        ("ST 270", "ST"),
        ("QK 000", "QK"),
        ("zl 4100", "ZL"),
        ("123", ""),
    ],
)
def test_main_group(notation, group):
    assert ev.main_group(notation) == group


RESULTS = [f"AA {i}" for i in range(1, 21)]


@pytest.mark.parametrize("rank", [1, 3, 7, 15])
def test_first_hit_rank_positions(rank):
    assert ev.first_hit_rank(RESULTS, [f"AA {rank}"]) == rank


def test_first_hit_rank_none_and_best_of_several():
    assert ev.first_hit_rank(RESULTS, ["ZZ 1"]) is None
    assert ev.first_hit_rank(RESULTS, ["AA 9", "AA 4", "ZZ 1"]) == 4
    assert ev.first_hit_rank(["ZN  8330"], ["zn 8330"]) == 1


def test_hit_at_k_and_reciprocal_rank():
    assert ev.hit_at_k(1, 1)
    assert ev.hit_at_k(5, 5)
    assert not ev.hit_at_k(6, 5)
    assert not ev.hit_at_k(None, 10)
    assert ev.reciprocal_rank(4) == 0.25
    assert ev.reciprocal_rank(None) == 0.0


def test_main_group_hit_at_k():
    assert ev.main_group_hit_at_k(["ST 100", "ZN 8300"], ["ZN 8330"], 5)
    assert not ev.main_group_hit_at_k(["ST 100", "ZL 4100"], ["ZN 8330"], 5)
    outside = ["ST 1", "ST 2", "ST 3", "ST 4", "ST 5", "ZN 8330"]
    assert not ev.main_group_hit_at_k(outside, ["ZN 8330"], 5)


def _q(query, results, expected, ms=10.0):
    entry = ev.GoldEntry(query=query, expected=expected)
    return ev.evaluate_query(entry, results, ms / 1000, [])


def test_evaluate_query_records_gnd_fields():
    entry = ev.GoldEntry(query="Mutter", expected=["ZL 4620"], typ="Mehrdeutig")
    q = ev.evaluate_query(
        entry,
        ["ZL 4620"],
        0.01,
        [],
        gnd_terms=["synonym: Schraubenmutter"],
        gnd_senses=["Mutter <Technik>", "Mutter <Familie>"],
    )
    assert q["gnd_terms"] == ["synonym: Schraubenmutter"]
    assert q["gnd_senses"] == ["Mutter <Technik>", "Mutter <Familie>"]
    # ohne Angabe leere Listen (alte Aufrufer bleiben gültig)
    assert _q("x", [], ["X 1"])["gnd_senses"] == []
    assert _q("x", [], ["X 1"])["gnd_terms"] == []


def test_markdown_lists_ambiguous_queries_with_senses():
    entry = ev.GoldEntry(query="Mutter", expected=["ZL 4620"], typ="Mehrdeutig")
    q = ev.evaluate_query(entry, ["ZL 4620"], 0.01, [], gnd_senses=["Mutter <Technik>"])
    report = {
        "metrics": ev.aggregate([q]),
        "per_query": [q],
        "expansion": {"gnd_enabled": True, "queries_with_gnd_senses": 1},
    }
    md = ev.render_markdown(report)
    assert "Anfragen mit GND-Bedeutungen: 1" in md
    assert "| Mutter | ZL 4620 | 1 | Mutter <Technik> |" in md


def test_aggregate_hand_calculated():
    per_query = [
        _q("a", ["X 1", "X 2"], ["X 1"], ms=10),  # Rang 1
        _q("b", ["Y 1", "Y 2", "X 3"], ["X 3"], ms=20),  # Rang 3
        _q("c", [f"Y {i}" for i in range(1, 8)], ["Y 7"], ms=30),  # Rang 7
        _q("d", [], ["Z 1"], ms=40),  # keine Treffer
    ]
    metrics = ev.aggregate(per_query)
    assert metrics["hit@1"] == 0.25
    assert metrics["hit@5"] == 0.5
    assert metrics["hit@10"] == 0.75
    assert metrics["mrr"] == round((1 + 1 / 3 + 1 / 7) / 4, 4)
    assert metrics["zero_result_rate"] == 0.25
    assert metrics["mean_response_ms"] == 25.0
    assert metrics["n"] == 4
    # Hauptgruppe: a (X), b (Y 1 ist nicht X, aber X 3 in Top-5), c (Y), d nein
    assert metrics["main_group_hit@5"] == 0.75


def test_aggregate_empty_raises():
    with pytest.raises(ValueError):
        ev.aggregate([])


def test_aggregate_by_typ():
    a = _q("a", ["X 1"], ["X 1"])
    a["typ"] = "Fachbegriff"
    b = _q("b", [], ["X 1"])
    b["typ"] = ""
    groups = ev.aggregate_by([a, b], "typ")
    assert groups["Fachbegriff"]["hit@1"] == 1.0
    assert groups["(ohne Angabe)"]["hit@1"] == 0.0


def test_parse_gold_with_typ_column():
    lines = [
        "query;erwartete_notationen;quelle;fachgebiet;typ;anmerkung",
        "# Kommentar",
        "",
        "Getriebe;zl 4100;RVK-Online;Maschinenbau;Fachbegriff;",
        "Faser;ZM 5000|ZI 4200 | ZI 4700;RVK-Online;Werkstoffe;Fachbegriff;mehrere",
    ]
    entries = ev.parse_gold(lines)
    assert [e.query for e in entries] == ["Getriebe", "Faser"]
    assert entries[0].expected == ["ZL 4100"]
    assert entries[0].typ == "Fachbegriff"
    assert entries[1].expected == ["ZM 5000", "ZI 4200", "ZI 4700"]
    assert entries[1].line == 5


def test_parse_gold_without_typ_column():
    lines = [
        "query;erwartete_notationen;quelle;fachgebiet;anmerkung",
        "Wärmepumpe;ZI 8694;Verbundkatalog;Technik;Kommentar",
    ]
    (entry,) = ev.parse_gold(lines)
    assert entry.typ == ""
    assert entry.anmerkung == "Kommentar"


def test_parse_gold_empty_notation_reports_line():
    lines = ["query;erwartete_notationen", "ok;AA 1", "kaputt;"]
    with pytest.raises(ValueError, match="Zeile 3"):
        ev.parse_gold(lines)


def test_parse_gold_missing_column():
    with pytest.raises(ValueError, match="erwartete_notationen"):
        ev.parse_gold(["query;quelle", "a;b"])


def test_real_goldstandard_is_parseable():
    entries = ev.parse_gold(GOLD_PATH.read_text(encoding="utf-8").splitlines())
    assert len(entries) >= 1
    assert all(e.expected for e in entries)


def test_worst_queries_order_and_limit():
    per_query = [
        _q("gut", ["X 1"], ["X 1"]),
        _q("mittel", ["Y 1", "X 1"], ["X 1"]),
        _q("schlecht-teil", ["X 9"], ["X 1"]),
        _q("schlecht-ganz", ["Y 9"], ["X 1"]),
    ]
    worst = ev.worst_queries(per_query, n=3)
    assert [q["query"] for q in worst] == ["schlecht-ganz", "schlecht-teil", "mittel"]


def test_compare_metrics():
    new = {"hit@1": 0.5, "hit@10": 0.8, "only_new": 1.0}
    old = {"metrics": {"hit@1": 0.4, "hit@10": 0.9, "only_old": 2.0}}
    diff = ev.compare_metrics(new, old)
    assert set(diff) == {"hit@1", "hit@10"}
    assert diff["hit@1"]["diff"] == 0.1
    assert diff["hit@10"]["diff"] == -0.1


def test_render_markdown_sections_and_no_confidence():
    per_query = [_q("Getriebe", ["ZL 4100"], ["ZL 4100"]), _q("Feder", [], ["ZL 4300"])]
    metrics = ev.aggregate(per_query)
    report = {
        "timestamp": "2026-11-11T10:00:00+00:00",
        "mode": "with_expansion",
        "metrics": metrics,
        "metrics_no_expansion": metrics,
        "by_typ": ev.aggregate_by(per_query, "typ"),
        "by_fachgebiet": ev.aggregate_by(per_query, "fachgebiet"),
        "worst": ev.worst_queries(per_query),
        "missing_notations": [{"notation": "ZL 4300", "query": "Feder"}],
        "compare": ev.compare_metrics(metrics, {"metrics": metrics}),
        "expansion": {
            "enabled": True,
            "api_key_present": False,
            "queries_with_terms": 0,
        },
    }
    md = ev.render_markdown(report)
    for heading in (
        "## Übersicht",
        "## Kennzahlen",
        "## Vergleich mit früherem Bericht",
        "## Kennzahlen je Anfragetyp",
        "## Kennzahlen je Fachgebiet",
        "## Die 10 schlechtesten Anfragen",
        "## Erwartete Notationen, die im RVK-Bestand fehlen",
    ):
        assert heading in md
    assert "keine Wahrscheinlichkeiten" in md
    assert "Konfidenz" not in md
    assert "%" not in md


def _pq(query, rank, top3):
    return {"query": query, "rank": rank, "top3": top3}


def test_compare_per_query_same_data_reports_nothing():
    per_query = [_pq("a", 1, ["X 1", "X 2"]), _pq("b", None, [])]
    assert ev.compare_per_query(per_query, {"per_query": per_query}) == []


def test_compare_per_query_reports_changed_rank_or_top3():
    old = {
        "per_query": [
            _pq("a", 1, ["X 1", "X 2"]),
            _pq("b", 2, ["Y 1", "Y 2"]),
            _pq("c", 3, ["Z 1"]),
        ]
    }
    new = [
        _pq("a", 1, ["X 1", "X 2"]),
        _pq("b", 2, ["Y 2", "Y 1"]),
        _pq("c", 1, ["Z 1"]),
    ]
    changed = ev.compare_per_query(new, old)
    assert [q["query"] for q in changed] == ["b", "c"]
    assert changed[1]["old_rank"] == 3 and changed[1]["new_rank"] == 1


def test_compare_per_query_without_old_per_query_is_empty():
    assert ev.compare_per_query([_pq("a", 1, [])], {"metrics": {}}) == []
