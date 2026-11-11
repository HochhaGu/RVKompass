import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from app.services import gold_candidates as gc

ROOT = Path(__file__).resolve().parent.parent

SRU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:version>1.1</zs:version>
  <zs:numberOfRecords>2</zs:numberOfRecords>
  <zs:records>
    <zs:record><zs:recordData>
      <record xmlns="http://www.loc.gov/MARC21/slim">
        <controlfield tag="001">111</controlfield>
        <datafield tag="245" ind1="1" ind2="0"><subfield code="a">Photovoltaik</subfield></datafield>
        <datafield tag="084" ind1=" " ind2=" ">
          <subfield code="a">ZP 3730</subfield><subfield code="2">rvk</subfield>
        </datafield>
        <datafield tag="084" ind1=" " ind2=" ">
          <subfield code="a">52.56</subfield><subfield code="2">bkl</subfield>
        </datafield>
      </record>
    </zs:recordData></zs:record>
    <zs:record><zs:recordData>
      <record xmlns="http://www.loc.gov/MARC21/slim">
        <controlfield tag="001">222</controlfield>
        <datafield tag="245" ind1="1" ind2="0"><subfield code="a">Solarzellen</subfield></datafield>
        <datafield tag="084" ind1=" " ind2=" ">
          <subfield code="a">53.36</subfield><subfield code="2">bkl</subfield>
        </datafield>
      </record>
    </zs:recordData></zs:record>
  </zs:records>
</zs:searchRetrieveResponse>
"""


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "build_gold_candidates", ROOT / "scripts" / "build_gold_candidates.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def test_parse_marcxml_takes_only_rvk():
    records = gc.parse_marcxml_records(SRU_XML)
    assert [r.ppn for r in records] == ["111", "222"]
    assert records[0].title == "Photovoltaik"
    assert records[0].rvk == ["ZP 3730"]
    assert records[1].rvk == []


def test_aggregate_candidates_counts_and_limits():
    records = [
        gc.Record("1", "a", ["ZP 3730", "ZN 8340"]),
        gc.Record("2", "b", ["ZP 3730"]),
        gc.Record("3", "c", ["ZP 3730", "ZN 8340"]),
        gc.Record("4", "d", ["QP 340"]),
    ]
    result = gc.aggregate_candidates(records, min_count=2, top_n=3)
    assert [(c.notation, c.count) for c in result] == [("ZP 3730", 3), ("ZN 8340", 2)]
    assert result[0].ppns == ["1", "2", "3"]
    assert len(gc.aggregate_candidates(records, min_count=1, top_n=1)) == 1


def test_build_sru_url_escapes_umlauts_and_quotes():
    url = gc.build_sru_url('Wärme "pumpe"', 50)
    assert url.startswith("https://sru.k10plus.de/opac-de-627?")
    assert "%C3%A4" in url
    params = parse_qs(urlparse(url).query)
    assert params["query"] == ['pica.all="Wärme \\"pumpe\\""']
    assert params["recordSchema"] == ["marcxml"]
    assert params["maximumRecords"] == ["50"]


def test_candidate_rows_format():
    rows = gc.candidate_rows(
        "Photovoltaik", [gc.Candidate("ZP 3730", 2, ["1", "2"])], 5
    )
    assert rows == [
        "Photovoltaik;ZP 3730;K10plus-SRU (CC0) PPN 1, 2;;;Kandidat: 2 von 5 Titeln, manuell prüfen"
    ]


def test_refuses_to_write_goldstandard(tmp_path):
    gold = ROOT / "eval" / "goldstandard.csv"
    before = hashlib.sha256(gold.read_bytes()).hexdigest()
    terms = tmp_path / "terms.txt"
    terms.write_text("Photovoltaik\n", encoding="utf-8")
    assert script.main(["--terms", str(terms), "--out", str(gold)]) == 2
    assert (
        script.main(
            ["--terms", str(terms), "--out", str(tmp_path / "goldstandard.csv")]
        )
        == 2
    )
    assert hashlib.sha256(gold.read_bytes()).hexdigest() == before
    assert not (tmp_path / "goldstandard.csv").exists()


class FakeResponse:
    def __init__(self, content: str):
        self.content = content.encode("utf-8")

    def raise_for_status(self):
        pass


def test_network_error_is_a_warning(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise requests.Timeout("zu langsam")

    monkeypatch.setattr(script.requests, "get", boom)
    terms = tmp_path / "terms.txt"
    terms.write_text("# Kommentar\nPhotovoltaik\n", encoding="utf-8")
    out = tmp_path / "kandidaten.csv"
    assert script.main(["--terms", str(terms), "--out", str(out), "--delay", "0"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == [gc.CANDIDATE_HEADER]
    assert "Warnung" in capsys.readouterr().err


def test_success_writes_candidates_without_overwriting(tmp_path, monkeypatch):
    seen = {}

    def fake_get(url, timeout, headers):
        seen["timeout"] = timeout
        seen["ua"] = headers["User-Agent"]
        return FakeResponse(SRU_XML)

    monkeypatch.setattr(script.requests, "get", fake_get)
    terms = tmp_path / "terms.txt"
    terms.write_text("Photovoltaik\n", encoding="utf-8")
    out = tmp_path / "kandidaten.csv"
    out.write_text("alt\n", encoding="utf-8")

    args = [
        "--terms",
        str(terms),
        "--out",
        str(out),
        "--delay",
        "0",
        "--min-count",
        "1",
    ]
    assert script.main(args) == 0
    assert out.read_text(encoding="utf-8") == "alt\n"
    (new_file,) = [p for p in tmp_path.glob("kandidaten*.csv") if p != out]
    lines = new_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == gc.CANDIDATE_HEADER
    assert lines[1].startswith("Photovoltaik;ZP 3730;K10plus-SRU (CC0) PPN 111;")
    assert seen["timeout"] == (5, 30)
    assert seen["ua"] == "RVKompass-gold-candidates"


@pytest.mark.parametrize("bad", ["<kaputt", ""])
def test_broken_xml_is_a_warning(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: FakeResponse(bad))
    terms = tmp_path / "terms.txt"
    terms.write_text("Photovoltaik\n", encoding="utf-8")
    out = tmp_path / "kandidaten.csv"
    assert script.main(["--terms", str(terms), "--out", str(out), "--delay", "0"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == [gc.CANDIDATE_HEADER]
