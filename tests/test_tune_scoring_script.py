"""scripts/tune_scoring.py – Datenbank, Modell und GND werden ersetzt (ohne Netz)."""

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app.models import SCHEMA_SQL
from app.services import ranking, scoring

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "tune_scoring_script", ROOT / "scripts" / "tune_scoring.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tune = _load_script()


class FakeConfig:
    def __init__(self, db_path):
        self.DATABASE_PATH = str(db_path)
        self.ENABLE_GND_EXPANSION = False
        self.ENABLE_QUERY_EXPANSION = False


def _feat(nid, notation, cosine, strengths):
    legacy = scoring.legacy_components(cosine, False, False, None, False)
    return ranking.CandidateFeatures(nid, notation, cosine, strengths, legacy)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db_path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.close()
    gold = tmp_path / "gold.csv"
    gold.write_text(
        "query;erwartete_notationen;quelle;fachgebiet;typ;anmerkung\n"
        "Getriebe;ZL 4100;x;Maschinenbau;Fachbegriff;\n"
        "Zahnrad;ZL 4175;x;Maschinenbau;Fachbegriff;\n"
        "Feder;ZL 4300;x;Maschinenbau;Mehrdeutig;\n"
        "Mutter;ZL 4620;x;Maschinenbau;Mehrdeutig;\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "eval-2026-11-11-abc.json").write_text(
        json.dumps(
            {"timestamp": "2026-11-11T10:00:00+00:00", "metrics": {"hit@10": 0.5}}
        )
    )

    def fake_candidates(conn, service, query, terms, gnd_senses=()):
        expected = {
            "Getriebe": "ZL 4100",
            "Zahnrad": "ZL 4175",
            "Feder": "ZL 4300",
            "Mutter": "ZL 4620",
        }[query]
        features = [
            _feat(1, expected, 0.3, {"label_fts": 1.0, "vector": 0.3}),
            _feat(2, "ZX 1", 0.5, {"vector": 0.5}),
        ]
        return ranking.Candidates(None, {}, {1, 2}, features)

    monkeypatch.setattr(tune, "get_config", lambda: FakeConfig(db_path))
    monkeypatch.setattr(tune, "build_embedding_service", lambda config: object())
    monkeypatch.setattr(tune.ranking, "build_candidates", fake_candidates)
    monkeypatch.setattr(tune, "git_info", lambda: ("abc1234", False))
    return gold, out


def test_report_name_has_no_eval_prefix(tmp_path):
    name = tune.report_basename(tmp_path, "2026-11-11", "abc1234")
    assert name == "scoring-2026-11-11-abc1234"
    (tmp_path / f"{name}.json").write_text("{}")
    assert tune.report_basename(tmp_path, "2026-11-11", "abc1234") == f"{name}-2"


def test_latest_eval_report_uses_timestamp(tmp_path):
    for name, stamp, value in (
        ("eval-b.json", "2026-11-11T09:00:00+00:00", 0.4),
        ("eval-a.json", "2026-11-11T11:00:00+00:00", 0.6),
    ):
        (tmp_path / name).write_text(
            json.dumps({"timestamp": stamp, "metrics": {"hit@10": value}})
        )
    (tmp_path / "eval-kaputt.json").write_text("{")
    path, hit10 = tune.latest_eval_report(tmp_path)
    assert path.endswith("eval-a.json") and hit10 == 0.6


def test_main_writes_scoring_report(setup):
    gold, out = setup
    assert tune.main(["--gold", str(gold), "--out", str(out)]) == 0

    (json_path,) = out.glob("scoring-*.json")
    assert re.fullmatch(r"scoring-\d{4}-\d{2}-\d{2}-abc1234\.json", json_path.name)
    assert len(list(out.glob("eval-*.json"))) == 1  # kein neuer eval-Bericht
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert [v["variant"] for v in report["variants"]] == [
        "legacy",
        "sum",
        "max_plus",
        "sum_capped",
    ]
    assert report["split"]["dev_count"] == 2 and report["split"]["test_count"] == 2
    assert report["gate"]["hit@10"] == 0.5
    assert report["self_check"]["fast_matches_reference"] is True
    assert report["decision"]["chosen"] in {"legacy", "sum", "max_plus", "sum_capped"}
    assert set(report["tiers"]["shares"]) == {"dev", "test"}

    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    for heading in (
        "## Selbstprüfung",
        "### Testhälfte",
        "## Entscheidung",
        "## Stufen „hoch / mittel / niedrig“",
    ):
        assert heading in md
    test_legacy = report["variants"][0]["test"]
    assert f"| legacy (Stand P4) | {tune._fmt(test_legacy['hit@1'])} |" in md
    assert "Konfidenz" not in md
