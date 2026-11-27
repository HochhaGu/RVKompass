"""Schwachstellen-Analyse (P12, D) – reine Funktionen, ohne Datenbank und Netz."""

import importlib.util
import json
from pathlib import Path

from app.services import gold_analysis as ga

ROOT = Path(__file__).resolve().parent.parent


def _q(
    query,
    rank,
    expected=("ZL 4100",),
    top3=(),
    senses=(),
    typ="Fachbegriff",
    fach="Maschinenbau",
):
    rr = round(1 / rank, 4) if rank else 0.0
    return {
        "query": query,
        "rank": rank,
        "rr": rr,
        "expected": list(expected),
        "top3": list(top3),
        "gnd_senses": list(senses),
        "typ": typ,
        "fachgebiet": fach,
        "hit@1": rank == 1,
        "hit@5": bool(rank and rank <= 5),
        "hit@10": bool(rank and rank <= 10),
        "main_group_hit@5": False,
        "response_ms": 1.0,
        "result_count": 3,
    }


def _d(rank_uncut, paths=(), vector_only=False):
    return {
        "rank_uncut": rank_uncut,
        "expected_paths": list(paths),
        "expected_vector_only": vector_only,
    }


def test_each_cause_once():
    cases = [
        (_q("a", None), _d(None), "not_candidate"),
        (_q("b", 15), _d(15, ["label_fts"]), "no_gnd_sense"),
        (
            _q("c", 12, top3=["AP 1", "AP 2", "QP 3"], senses=["Maus <Tier>"]),
            _d(12, ["register_exact"]),
            "wrong_sense",
        ),
        (
            _q("d", 14, top3=["ZL 1"], senses=["X"]),
            _d(14, [], vector_only=True),
            "vector_only",
        ),
        (
            _q("e", None, top3=["ZL 1"], senses=["X"]),
            _d(25, ["gnd_synonym"]),
            "cut_after_20",
        ),
        (
            _q("f", 12, top3=["ZL 1"], senses=["X"]),
            _d(12, ["gnd_synonym"]),
            "behind_10",
        ),
        (_q("g", 3, senses=["X"]), _d(3, ["gnd_synonym"]), "top10_not_first"),
        (_q("h", 1), _d(1), "ok"),
    ]
    for q, d, cause in cases:
        assert ga.classify_cause(q, d) == cause, q["query"]


def test_order_of_causes_first_match_wins():
    # nicht unter den Kandidaten schlägt alles andere, auch ohne GND-Bedeutung
    assert ga.classify_cause(_q("a", None), _d(None)) == "not_candidate"
    # ohne Bedeutung und ohne Register gilt „keine GND-Bedeutung“ vor „nur Vektor“
    assert (
        ga.classify_cause(_q("b", 14), _d(14, [], vector_only=True)) == "no_gnd_sense"
    )
    # mit Register-Treffer, ohne Bedeutung: weiter zu „nur Vektor“ bzw. Rang
    assert (
        ga.classify_cause(_q("c", None), _d(30, ["register_similar"])) == "cut_after_20"
    )
    # ohne Diagnose wird nach dem Rang eingeordnet
    assert ga.classify_cause(_q("d", None), None) == "not_candidate"
    assert [k for k, _ in ga.CAUSES][:6] == [
        "not_candidate",
        "no_gnd_sense",
        "wrong_sense",
        "vector_only",
        "cut_after_20",
        "behind_10",
    ]


def test_homonym_marked():
    rows = ga.annotate([_q("Satz", None, typ="Mehrdeutig"), _q("Getriebe", 1)], {})
    assert [r["homonym"] for r in rows] == [True, False]
    assert rows[0]["subset"] == "alle"


def test_worst_15_per_subset():
    per_query = [_q(f"q{i}", None if i % 2 else i + 1) for i in range(40)]
    labels = ["alt"] * 20 + ["neu_technik"] * 20
    rows = ga.annotate(per_query, {}, labels)
    worst = ga.worst_by_subset(rows)
    assert set(worst) == {"alt", "neu_technik"}
    assert all(len(v) == 15 for v in worst.values())
    assert all(r["subset"] == "alt" for r in worst["alt"])
    assert worst["alt"][0]["rr"] == 0.0


def test_weakest_groups_min_size():
    per_query = [_q(f"a{i}", None, fach="Recht") for i in range(4)]
    per_query += [_q(f"b{i}", 2, fach="Musik") for i in range(5)]
    per_query += [_q(f"c{i}", 1, fach="Geschichte") for i in range(6)]
    rows = ga.annotate(per_query, {})
    weakest = ga.weakest_groups(rows, "fachgebiet")
    assert [g["name"] for g in weakest] == ["Musik", "Geschichte"]  # Recht hat nur 4


def test_cause_counts_and_proposals():
    rows = ga.annotate(
        [_q("a", None), _q("b", None), _q("c", 15), _q("d", 3), _q("e", 1)],
        {"c": _d(15, ["label_fts"])},
    )
    counts = ga.cause_counts(rows)
    assert counts == {"not_candidate": 2, "no_gnd_sense": 1}
    assert ga.cause_counts(rows, only_misses=False)["ok"] == 1
    proposals = ga.proposals_by_impact(counts)
    assert [p["cause"] for p in proposals] == ["not_candidate", "no_gnd_sense"]
    assert proposals[0]["queries"] == 2
    # „ok“ und „unter den ersten 10“ sind keine Vorschläge
    everything = ga.proposals_by_impact(ga.cause_counts(rows, only_misses=False))
    assert {p["cause"] for p in everything} == {"not_candidate", "no_gnd_sense"}


def test_script_writes_report(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "analyze_weak", ROOT / "scripts" / "analyze_weak.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    eval_path = tmp_path / "eval.json"
    diag_path = tmp_path / "diag.json"
    eval_path.write_text(
        json.dumps({"per_query": [_q("a", None), _q("b", 1, typ="Mehrdeutig")]}),
        encoding="utf-8",
    )
    diag_path.write_text(
        json.dumps({"diagnose": {"full": {"per_query": [{"query": "a", **_d(None)}]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(script, "git_info", lambda: ("abc1234", False))
    out = tmp_path / "out"
    assert (
        script.main(
            ["--eval", str(eval_path), "--diagnose", str(diag_path), "--out", str(out)]
        )
        == 0
    )
    (json_path,) = out.glob("schwachstellen-*.json")
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["cause_counts"] == {"not_candidate": 1}
    assert set(report["worst_by_subset"]) == {"alle"}
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Vorschläge nach Wirkung" in md and "Konfidenz" not in md
