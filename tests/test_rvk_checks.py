"""Prüfungen vor dem Tausch (app/services/rvk_sync/checks.py) und exclude_notations."""

from app.services import evaluation
from app.services.rvk_sync import checks
from app.services.rvk_sync.diff import DiffResult
from tests.rvk_sync_helpers import make_db


def test_count_just_below_and_above_threshold():
    ok, message = checks.check_count(1000, 1049, 0.05)
    assert ok and "0.0490" in message
    ok, _ = checks.check_count(1000, 951, 0.05)
    assert ok
    assert checks.check_count(1000, 1051, 0.05)[0] is False
    assert checks.check_count(1000, 949, 0.05)[0] is False
    assert checks.check_count(1000, 1050, 0.05)[0] is False  # „weniger als“ 5 %


def test_count_without_old_and_empty_new():
    assert checks.check_count(None, 10, 0.05)[0] is True
    assert checks.check_count(1000, 0, 0.05)[0] is False


def test_sample(tmp_path):
    conn = make_db(tmp_path / "rvk.sqlite3")
    groups = checks.main_groups(conn, "notation")
    assert groups == ["ZL", "ZM"]
    ok, message, missing = checks.check_sample(conn, [*groups, "ZL 4630"])
    assert ok and missing == [] and "3" in message
    ok, message, missing = checks.check_sample(conn, ["ZL", "ZL 9999"])
    assert not ok and missing == ["ZL 9999"]
    assert checks.check_sample(conn, [])[0] is False


GOLD = [
    evaluation.GoldEntry("Mutter", ["ZL 4620"]),
    evaluation.GoldEntry("Veraltet", ["ZL 9999"]),
    evaluation.GoldEntry("Beides", ["ZL 9999", "ZL 4630"]),
    evaluation.GoldEntry("Fehlt", ["XX 1"]),
    evaluation.GoldEntry("Kommt", ["ZL 0001"]),
]


def test_mark_gold_separates_deleted_and_missing_before():
    old_present = {"ZL 4620", "ZL 9999", "ZL 4630"}
    new_present = {"ZL 4620", "ZL 4630", "ZL 0001"}
    marks = checks.mark_gold(
        GOLD, old_present, new_present, "2026/3", DiffResult(deleted=["ZL 9999"])
    )
    by_key = {(m.query, m.notation): m for m in marks}
    assert by_key[("Veraltet", "ZL 9999")].status == checks.GOLD_DELETED
    assert by_key[("Veraltet", "ZL 9999")].label == "gelöscht in 2026/3"
    assert by_key[("Beides", "ZL 9999")].status == checks.GOLD_DELETED
    assert by_key[("Fehlt", "XX 1")].status == checks.GOLD_MISSING_BEFORE
    assert "fehlte schon vorher" in by_key[("Fehlt", "XX 1")].label
    assert by_key[("Kommt", "ZL 0001")].status == checks.GOLD_NEW
    assert ("Mutter", "ZL 4620") not in by_key
    # Nur „gelöscht“ fällt aus der Wertung; „fehlte schon vorher“ bleibt in der Messbasis
    assert checks.excluded_by_marks(marks) == {"ZL 9999"}


def test_exclude_notations():
    filtered = evaluation.exclude_notations(GOLD, {"zl  9999"})
    assert [e.query for e in filtered] == ["Mutter", "Beides", "Fehlt", "Kommt"]
    beides = filtered[1]
    assert beides.expected == ["ZL 4630"]
    assert GOLD[2].expected == ["ZL 9999", "ZL 4630"]  # Original unverändert
    assert evaluation.exclude_notations(GOLD, []) == GOLD


def test_real_gold_standard_in_sync_checks():
    """P12/A5: Der Monatsabgleich liest den vollen Goldstandard (SYNC_GOLD_PATH)."""
    import time

    from app.config import Config
    from app.services.rvk_sync import pipeline

    start = time.perf_counter()
    gold = pipeline.load_gold(Config.SYNC_GOLD_PATH)
    assert len(gold) == sum(Config.GOLD_SUBSET_SIZES.values())
    present = {n for e in gold for n in e.expected}
    # alle Notationen in beiden Ständen vorhanden -> keine Markierung
    assert checks.mark_gold(gold, present, present, "2026/3") == []
    # eine Notation im neuen Stand gelöscht -> genau ihre Einträge markiert bzw. gestrichen
    target = "ST 170"
    affected = {e.query for e in gold if target in e.expected}
    assert affected
    marks = checks.mark_gold(gold, present, present - {target}, "2026/3")
    assert {m.query for m in marks} == affected
    assert {m.status for m in marks} == {checks.GOLD_DELETED}
    assert checks.excluded_by_marks(marks) == {target}
    filtered = evaluation.exclude_notations(gold, checks.excluded_by_marks(marks))
    only_target = {e.query for e in gold if e.expected == [target]}
    assert len(filtered) == len(gold) - len(only_target)
    assert all(target not in e.expected for e in filtered)
    assert time.perf_counter() - start < 1.0


def test_eval_drop_threshold():
    assert checks.check_eval({"hit@10": 0.5}, {"hit@10": 0.451}, 0.05)[0] is True
    assert checks.check_eval({"hit@10": 0.5}, {"hit@10": 0.449}, 0.05)[0] is False
    assert checks.check_eval({"hit@10": 0.5}, {"hit@10": 0.6}, 0.05)[0] is True
    assert checks.check_eval(None, {"hit@10": 0.6}, 0.05)[0] is False


def test_report():
    report = checks.CheckReport()
    assert report.passed is False  # ohne Prüfung nie „bestanden“
    report.add("a", True, "ok")
    assert report.passed
    report.add("b", False, "schlecht")
    assert not report.passed
    assert [r.name for r in report.failed()] == ["b"]
