"""Suchroute mit GND-Bedeutungen – ohne Netz, ohne Modell; Datenbank als temporäre Datei."""

import re
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from app import limiter
from app.models import (
    SCHEMA_SQL,
    Evidence,
    ExpansionTerm,
    GndSense,
    Hit,
    LlmSuggestion,
    Notation,
)
from app.routes import search as search_route
from app.services import ranking

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def _sense(gnd_id, name, qualifier, category):
    display = f"{name} <{qualifier}>"
    return GndSense(
        gnd_id=gnd_id,
        preferred_name=name,
        qualifier=qualifier,
        subject_categories=(category,),
        terms=(ExpansionTerm(display, "gnd", "synonym", gnd_id, display),),
    )


TECHNIK = _sense("0000001-1", "Mutter", "Technik", ("31.6", "Maschinenbau"))
FAMILIE = _sense("0000002-1", "Mutter", "Familie", ("99.9", "Familie"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (1, 'ZL 4620', 'Muttern')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (2, 'MS 2000', 'Familie')"
    )
    conn.commit()
    conn.close()

    seen = {}

    def fake_senses(conn, config, query, source=None):
        seen["config_enabled"] = config["ENABLE_GND_EXPANSION"]
        seen["gnd_source"] = source
        return (
            [FAMILIE, TECHNIK]
            if query == "Mutter"
            else [TECHNIK]
            if query == "Schraubenmutter"
            else []
        )

    def fake_search_result(
        conn, service, query, expanded_terms, gnd_senses=(), subject_filter=None
    ):
        seen["subject_filter"] = subject_filter
        seen["llm_terms"] = list(expanded_terms)
        notation = Notation(1, "ZL 4620", "Muttern", None, None, None)
        hit = Hit(
            notation,
            [Evidence(1, TECHNIK.terms[0], "register_gnd_id", 1.0)],
            0.5,
            {"cosine": 0.46, "gnd_bonus": 0.04},
        )
        senses = [
            ranking.SenseResult(
                sense=s,
                notation_ids=[1] if s is TECHNIK else [2],
                hit_count=1,
                matches_filter=False,
            )
            for s in gnd_senses
        ]
        return ranking.SearchResult(hits=[hit], senses=senses)

    def fake_llm(conn, config, query, source=None):
        seen["llm_source"] = source
        if query != "Getriebe":
            return []
        return [
            LlmSuggestion(
                "Zahnrad",
                "unterbegriff",
                "Bauteil, das Drehmoment überträgt",
                True,
                "rvk_register",
                "Zahnrad",
            ),
            LlmSuggestion(
                "Antriebswelle",
                "verwandt",
                "überträgt Drehbewegung",
                True,
                "gnd",
                "Welle <Technik>",
                "4189530-9",
            ),
            LlmSuggestion("Kraftschlussdings", "fachkontext", "erfundener Begriff"),
        ]

    monkeypatch.setattr(search_route.gnd, "gnd_senses_for_query", fake_senses)
    monkeypatch.setattr(search_route.llm, "llm_suggestions_for_query", fake_llm)
    monkeypatch.setattr(search_route.ranking, "search_result", fake_search_result)
    monkeypatch.setattr(search_route.embedding_service, "get_service", lambda: object())

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        DATABASE_PATH=str(db_path),
        ENABLE_QUERY_EXPANSION=False,
        ENABLE_GND_EXPANSION=True,
    )
    limiter.init_app(app)
    app.register_blueprint(search_route.bp)
    return app.test_client(), seen


def test_several_senses_are_shown(client):
    test_client, seen = client
    response = test_client.post("/search", data={"q": "Mutter", "fach": "zl, zm"})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Mehrere Bedeutungen gefunden" in html
    assert "Mutter &lt;Technik&gt;" in html
    assert "Mutter &lt;Familie&gt;" in html
    assert "Sachgruppe: Maschinenbau" in html
    assert "MS 2000" in html
    assert seen["subject_filter"] == ["ZL", "ZM"]
    assert seen["config_enabled"] is True


def test_single_sense_is_shown_as_gnd_term(client):
    test_client, _ = client
    html = test_client.post("/search", data={"q": "Schraubenmutter"}).get_data(
        as_text=True
    )
    assert "GND-Begriff:" in html
    assert "Mehrere Bedeutungen gefunden" not in html


def test_without_senses_no_gnd_block(client):
    test_client, seen = client
    html = test_client.post("/search", data={"q": "Getriebe"}).get_data(as_text=True)
    assert "GND-Begriff" not in html
    assert "Mehrere Bedeutungen" not in html
    assert "ZL 4620" in html
    assert seen["subject_filter"] == []


def test_llm_block_shows_verified_and_grey_unverified(client):
    test_client, seen = client
    html = test_client.post("/search", data={"q": "Getriebe"}).get_data(as_text=True)

    assert "Vorschläge des Sprachmodells" in html
    assert "Bauteil, das Drehmoment überträgt" in html
    assert "gefunden im RVK-Register" in html
    assert "gefunden in GND" in html
    assert "Welle &lt;Technik&gt;" in html
    assert 'class="llm-unverified"' in html
    assert "nicht im Normvokabular gefunden" in html
    unverified = html.split('class="llm-unverified"', 1)[1].split("</li>", 1)[0]
    assert "Kraftschlussdings" in unverified and "erfundener Begriff" in unverified
    # Keine Prozentwerte oder Wahrscheinlichkeiten im LLM-Block
    block = html.split('class="llm-suggestions"', 1)[1].split("</section>", 1)[0]
    assert "%" not in block and "Konfidenz" not in block

    # gesucht werden nur die geprüften Begriffe, unter ihrer Schreibweise im Normvokabular
    assert [(t.term, t.relation) for t in seen["llm_terms"]] == [
        ("Zahnrad", "llm_verified"),
        ("Welle <Technik>", "llm_verified"),
    ]
    # eine gemeinsame GND-Quelle für GND-Erweiterung und LLM-Prüfung
    assert seen["llm_source"] is seen["gnd_source"] is not None


def test_llm_block_without_hits(client, monkeypatch):
    test_client, _ = client

    def no_hits(
        conn, service, query, expanded_terms, gnd_senses=(), subject_filter=None
    ):
        return ranking.SearchResult(hits=[])

    monkeypatch.setattr(search_route.ranking, "search_result", no_hits)
    html = test_client.post("/search", data={"q": "Getriebe"}).get_data(as_text=True)
    assert "Keine passenden RVK-Systemstellen" in html
    assert "Vorschläge des Sprachmodells" in html


def test_results_show_relevance_tier_and_bar_without_percent(client):
    test_client, _ = client
    html = test_client.post("/search", data={"q": "Getriebe"}).get_data(as_text=True)
    table = html.split("<table>", 1)[1].split("</table>", 1)[0]
    assert "<th>Relevanz</th>" in table
    assert "Konfidenz" not in html
    cells = re.findall(r'<td class="relevance-cell">(.*?)</td>', table, re.DOTALL)
    assert cells
    assert all("%" not in cell for cell in cells)
    assert "Wahrscheinlichkeit" not in table
    assert re.search(r'class="tier tier-(hoch|mittel|niedrig)"', table)
    assert 'title="Relevanzwert 0.50"' in table
    assert 'class="seg seg-cosine"' in table and 'title="Vektor: 0.46"' in table
    assert 'class="seg seg-gnd_bonus"' in table and 'title="GND: 0.04"' in table
    assert "keine Wahrscheinlichkeit" in html


def test_no_llm_block_without_suggestions(client):
    test_client, _ = client
    html = test_client.post("/search", data={"q": "Mutter"}).get_data(as_text=True)
    assert "Vorschläge des Sprachmodells" not in html
