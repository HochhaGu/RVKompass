"""Ableitung GND-Sachgruppe -> RVK-Hauptgruppe (P12, C3) – ohne Datenbank und Netz."""

from app.models import Evidence, ExpansionTerm, GndSense
from app.services import ranking
from app.services import subject_mapping as sm

MAPPING = {"30": ("ST",), "10.11*": ("QP",)}


def _sense(codes, notations, hits=None):
    return sm.SenseRecord(
        tuple(codes), frozenset(notations), len(notations) if hits is None else hits
    )


def _rec(query, expected, fach, *senses):
    return sm.QueryRecord(query, tuple(expected), fach, tuple(senses))


def test_is_mapped_uses_wildcards():
    assert sm.is_mapped("30", MAPPING)
    assert sm.is_mapped("10.11b", MAPPING)
    assert not sm.is_mapped("31.6", MAPPING)


def test_filter_for_expands_assumed_main_groups():
    flt = sm.filter_for("Recht")
    assert "PU" in flt and "P" in flt and "QP" not in flt
    both = sm.filter_for("Elektrotechnik / Geographie und Raumplanung")
    assert {"ZN", "RK", "UF"} <= both
    assert sm.filter_for("Unbekannt") == set()


def test_order_matches_rank_senses():
    terms = [ExpansionTerm(f"t{i}", "gnd", "synonym") for i in range(3)]
    senses = [
        GndSense("1", "Feder", "Tier", (("12.5", "Zoologie"),), (terms[0],)),
        GndSense("2", "Feder", "Technik", (("31.6", "Maschinenbau"),), (terms[1],)),
        GndSense("3", "Feder", "Schreibgerät", (("30", "Informatik"),), (terms[2],)),
    ]
    counts = [3, 1, 3]
    evidence_by_id = {}
    nid = 0
    for term, count in zip(terms, counts, strict=True):
        for _ in range(count):
            nid += 1
            evidence_by_id[nid] = [Evidence(nid, term, "register_exact", 1.0)]
    mapping = {"31.6": ("ZL",), "30": ("ST",)}
    records = [
        _sense([s.subject_categories[0][0]], [], c)
        for s, c in zip(senses, counts, strict=True)
    ]
    for flt in ({"ZL"}, {"ST"}, {"W"}, set()):
        expected = ranking.rank_senses(senses, evidence_by_id, flt, mapping)
        own = sm.order_senses(records, flt, mapping)
        assert [records.index(r) for r in own] == [
            senses.index(r.sense) for r in expected
        ]


def test_sense_metrics_only_with_two_senses():
    records = [
        _rec(
            "Feder",
            ["ZL 4300"],
            "Maschinenbau",
            _sense(["12.5"], ["W 1"], 5),
            _sense(["31.6"], ["ZL 4300"], 1),
        ),
        _rec("Getriebe", ["ZL 4100"], "Maschinenbau", _sense(["31.6"], ["ZL 4100"])),
    ]
    before = sm.sense_metrics(records, {})
    assert before == {"n": 1, "sense@1": 0.0, "sense_mrr": 0.5}
    after = sm.sense_metrics(records, {"31.6": ("ZL",)})
    assert after == {"n": 1, "sense@1": 1.0, "sense_mrr": 1.0}


def test_sense_metrics_without_hit_counts_zero():
    records = [
        _rec("X", ["PU 1"], "Recht", _sense(["1"], ["A 1"]), _sense(["2"], ["B 1"])),
    ]
    assert sm.sense_metrics(records, {}) == {"n": 1, "sense@1": 0.0, "sense_mrr": 0.0}
    assert sm.sense_metrics([], {}) == {"n": 0, "sense@1": 0.0, "sense_mrr": 0.0}


def _records_for(code, groups_per_query):
    return [
        _rec(f"q{i}", [f"{g} 1" for g in groups], "Recht", _sense([code], []))
        for i, groups in enumerate(groups_per_query)
    ]


def test_derive_needs_min_support():
    assert sm.derive_mapping(_records_for("7.1", [["PU"], ["PU"]]), {}) == {}
    proposals = sm.derive_mapping(_records_for("7.1", [["PU"]] * 3), {})
    assert proposals["7.1"]["groups"] == ("PU",)
    assert proposals["7.1"]["share"] == 1.0
    assert proposals["7.1"]["queries"] == 3


def test_derive_needs_min_share():
    # 4 von 5 = 0.8 -> genügt; 3 von 4 = 0.75 -> nicht
    ok = sm.derive_mapping(_records_for("7.1", [["PU"]] * 4 + [["QP"]]), {})
    assert ok["7.1"]["groups"] == ("PU",)
    assert ok["7.1"]["share"] == 0.8
    assert sm.derive_mapping(_records_for("7.1", [["PU"]] * 3 + [["QP"]]), {}) == {}


def test_derive_two_groups_only_together_and_each_supported():
    data = [["PU"]] * 4 + [["PL"]] * 3 + [["X"]]
    proposals = sm.derive_mapping(_records_for("7.1", data), {})
    assert proposals["7.1"]["groups"] == ("PU", "PL")
    assert proposals["7.1"]["share"] == 0.875
    # zweite Gruppe mit nur 2 Anfragen -> kein Vorschlag
    weak = [["PU"]] * 4 + [["PL"]] * 2 + [["X"]]
    assert sm.derive_mapping(_records_for("7.1", weak), {}) == {}


def test_existing_entries_stay():
    records = _records_for("30", [["PU"]] * 5) + _records_for("10.11c", [["PU"]] * 5)
    assert sm.derive_mapping(records, MAPPING) == {}
    extended = sm.extended_mapping(
        MAPPING, {"30": {"groups": ("PU",)}, "7.1": {"groups": ("PU",)}}
    )
    assert extended["30"] == ("ST",)
    assert extended["7.1"] == ("PU",)
    assert extended["10.11*"] == ("QP",)


def test_query_counted_once_per_code():
    record = _rec(
        "q", ["PU 1", "PU 2"], "Recht", _sense(["7.1"], []), _sense(["7.1"], [])
    )
    stats = sm.code_statistics([record], {})
    assert stats["7.1"]["queries"] == 1
    assert stats["7.1"]["groups"]["PU"] == 1
