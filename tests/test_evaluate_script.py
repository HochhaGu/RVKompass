import importlib.util
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app.models import SCHEMA_SQL
from orchestrator.gates import extract_metrics

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "evaluate_script", ROOT / "scripts" / "evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = _load_script()


class FakeService:
    def embed_query(self, text):
        return text


class FakeConfig:
    def __init__(self, tmp_path: Path, db_path: Path):
        self.DATABASE_PATH = str(db_path)
        for name in (
            "EMBEDDINGS_PATH",
            "EMBEDDINGS_META_PATH",
            "REGISTER_TERM_EMBEDDINGS_PATH",
            "REGISTER_TERM_META_PATH",
        ):
            path = tmp_path / f"{name.lower()}.dat"
            path.write_text("x")
            setattr(self, name, str(path))
        self.EMBEDDING_MODEL_NAME = "fake-model"
        self.CLAUDE_API_KEY = ""
        self.ENABLE_QUERY_EXPANSION = True
        self.CLAUDE_DAILY_LIMIT = 10


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db_path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO notation (notation, label) VALUES ('ZL 4100', 'Getriebe')"
    )
    conn.commit()
    conn.close()

    gold = tmp_path / "gold.csv"
    gold.write_text(
        "query;erwartete_notationen;quelle;fachgebiet;typ;anmerkung\n"
        "Getriebe;ZL 4100;RVK-Online;Maschinenbau;Fachbegriff;\n"
        "Feder;ZL 4300;RVK-Online;Maschinenbau;Mehrdeutig;\n",
        encoding="utf-8",
    )

    calls = {"expand": 0}

    def fake_expand(conn, api_key, query, daily_limit):
        calls["expand"] += 1
        return ["Zahnrad"] if query == "Getriebe" else []

    def fake_search(conn, service, query, expanded_terms):
        if query == "Getriebe":
            return [{"notation": "ZL 4100"}, {"notation": "ZL 4140"}]
        return []

    monkeypatch.setattr(evaluate, "get_config", lambda: FakeConfig(tmp_path, db_path))
    monkeypatch.setattr(
        evaluate, "build_embedding_service", lambda config: FakeService()
    )
    monkeypatch.setattr(evaluate.query_expansion, "expand_query", fake_expand)
    monkeypatch.setattr(evaluate.ranking, "search", fake_search)
    return gold, tmp_path / "out", calls


def test_report_basename_format_and_no_overwrite(tmp_path):
    name = evaluate.report_basename(tmp_path, "2026-11-11", "abc1234")
    assert name == "eval-2026-11-11-abc1234"
    (tmp_path / f"{name}.md").write_text("alt")
    assert evaluate.report_basename(tmp_path, "2026-11-11", "abc1234") == f"{name}-2"
    (tmp_path / f"{name}-2.json").write_text("{}")
    assert evaluate.report_basename(tmp_path, "2026-11-11", "abc1234") == f"{name}-3"


def test_report_fulfils_contract(setup):
    gold, out, calls = setup
    assert evaluate.main(["--gold", str(gold), "--out", str(out)]) == 0

    (json_path,) = out.glob("eval-*.json")
    assert re.fullmatch(r"eval-\d{4}-\d{2}-\d{2}-[\w]+\.json", json_path.name)
    assert json_path.with_suffix(".md").exists()
    report = json.loads(json_path.read_text(encoding="utf-8"))

    for key in ("hit@1", "hit@5", "hit@10", "mrr"):
        assert 0.0 <= report["metrics"][key] <= 1.0
    datetime.fromisoformat(report["timestamp"])
    assert report["mode"] == "with_expansion"
    assert report["metrics"]["hit@1"] == 0.5
    assert report["expansion"]["queries_with_terms"] == 1
    assert report["expansion"]["api_key_present"] is False
    assert report["missing_notations"] == [{"notation": "ZL 4300", "query": "Feder"}]
    assert "metrics_no_expansion" in report
    assert calls["expand"] == 2

    gate_metrics = extract_metrics(report)
    assert gate_metrics["hit@10"] == report["metrics"]["hit@10"]


def test_second_run_does_not_overwrite(setup):
    gold, out, _ = setup
    evaluate.main(["--gold", str(gold), "--out", str(out)])
    evaluate.main(["--gold", str(gold), "--out", str(out)])
    assert len(list(out.glob("eval-*.json"))) == 2


def test_no_expansion_mode(setup):
    gold, out, calls = setup
    assert (
        evaluate.main(["--gold", str(gold), "--out", str(out), "--no-expansion"]) == 0
    )
    (json_path,) = out.glob("eval-*.json")
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["mode"] == "no_expansion"
    assert calls["expand"] == 0
    assert report["expansion"]["enabled"] is False


def test_compare_adds_differences(setup, tmp_path):
    gold, out, _ = setup
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps({"metrics": {"hit@1": 0.25, "hit@10": 1.0}}), encoding="utf-8"
    )
    assert (
        evaluate.main(["--gold", str(gold), "--out", str(out), "--compare", str(old)])
        == 0
    )
    (json_path,) = out.glob("eval-*.json")
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["compare"]["hit@1"]["diff"] == 0.25
    assert report["compare"]["hit@10"]["diff"] == -0.5
    assert "Vergleich" in json_path.with_suffix(".md").read_text(encoding="utf-8")


def test_missing_data_exits_with_error(setup, monkeypatch, tmp_path):
    gold, out, _ = setup
    config = FakeConfig(tmp_path, tmp_path / "gibt-es-nicht.sqlite3")
    monkeypatch.setattr(evaluate, "get_config", lambda: config)
    assert evaluate.main(["--gold", str(gold), "--out", str(out)]) == 1
    assert not out.exists()
