"""Suchroute mit GND-Bedeutungen – ohne Netz, ohne Modell; Datenbank als temporäre Datei."""

import re
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from app import limiter, register_template_context
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
from app.services import presentation, ranking

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def _sense(gnd_id, name, qualifier, category, extra_terms=()):
    display = f"{name} <{qualifier}>"
    return GndSense(
        gnd_id=gnd_id,
        preferred_name=name,
        qualifier=qualifier,
        subject_categories=(category,),
        terms=(ExpansionTerm(display, "gnd", "synonym", gnd_id, display), *extra_terms),
    )


SCHRAUBE = ExpansionTerm(
    "Schraubenverbindung", "gnd", "broader", "4180000-1", "Mutter <Technik>"
)
TECHNIK = _sense(
    "0000001-1", "Mutter", "Technik", ("31.6", "Maschinenbau"), (SCHRAUBE,)
)
FAMILIE = _sense("0000002-1", "Mutter", "Familie", ("99.9", "Familie"))
INPUT = ExpansionTerm("Getriebe", "input", "direct")


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "rvk.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO notation (id, notation, label, hierarchy_path, note, see_also) "
        "VALUES (1, 'ZL 4620', 'Muttern', 'Technik > Maschinenelemente', "
        "'Hier nur Normteile', 'ZL 4600')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (2, 'MS 2000', 'Familie')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (3, 'ZL 9999', 'Allerlei')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (10, 'ZL', 'Maschinenbau')"
    )
    conn.execute(
        "INSERT INTO sync_log (run_at, source_file, status) "
        "VALUES ('2026-11-10T13:37:59', 'rvko_marcxml_2026_1.xml', 'success')"
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
        conn, service, query, expanded_terms, gnd_senses=(), subject_filter=None, **kw
    ):
        seen["subject_filter"] = subject_filter
        seen["main_group_filter"] = kw.get("main_group_filter")
        seen["llm_terms"] = list(expanded_terms)
        seen["gnd_senses"] = list(gnd_senses)
        notation = Notation(
            1,
            "ZL 4620",
            "Muttern",
            "Technik > Maschinenelemente",
            None,
            None,
            see_also="ZL 4600",
            note="Hier nur Normteile",
        )
        hit = Hit(
            notation,
            [Evidence(1, TECHNIK.terms[0], "register_gnd_id", 1.0)],
            0.5,
            {"cosine": 0.46, "gnd_bonus": 0.04},
        )
        vector_hit = Hit(
            Notation(3, "ZL 9999", "Allerlei", None, None, None),
            [Evidence(3, ExpansionTerm(query, "input", "direct"), "vector", 0.4)],
            0.4,
            {"cosine": 0.4},
        )
        senses = [
            ranking.SenseResult(
                sense=s,
                notation_ids=[1] if s.gnd_id == TECHNIK.gnd_id else [2],
                hit_count=4 if s.gnd_id == TECHNIK.gnd_id else 1,
                matches_filter=s.gnd_id == TECHNIK.gnd_id
                and "ZL" in (subject_filter or []),
            )
            for s in gnd_senses
        ]
        return ranking.SearchResult(
            hits=[hit, vector_hit],
            senses=senses,
            filtered_out=3 if kw.get("main_group_filter") else 0,
        )

    def fake_llm(conn, config, query, source=None, stats=None):
        seen["llm_source"] = source
        if stats is not None and query == "Getriebe":
            stats["cache_hits"] = stats.get("cache_hits", 0) + 1
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
        USAGE_DATABASE_PATH=str(tmp_path / "usage.sqlite3"),
        ENABLE_QUERY_EXPANSION=False,
        ENABLE_GND_EXPANSION=True,
        DEFAULT_MAIN_GROUPS=(),
    )
    limiter.init_app(app)
    app.register_blueprint(search_route.bp)
    register_template_context(app)
    seen["app"] = app
    seen["db_path"] = db_path
    seen["usage_path"] = tmp_path / "usage.sqlite3"
    return app.test_client(), seen


def _post(test_client, data):
    return test_client.post("/search", data=data).get_data(as_text=True)


def _tree(html):
    return html.split('id="expansion-tree"', 1)[1].split("</section>", 1)[0]


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
    assert seen["main_group_filter"] == ["ZL", "ZM"]
    assert seen["config_enabled"] is True


def test_sense_blocks_open_matching_filter_and_show_counts(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter", "hg": "ZL"})
    blocks = re.findall(
        r'<details id="(sense-block-[^"]+)" class="sense-block"( open)?>', html
    )
    # Familie kommt in der Quelle zuerst, Technik passt zum Filter -> nur Technik aufgeklappt
    assert blocks[0] == ("sense-block-1", "")
    assert blocks[1] == ("sense-block-2", " open")
    technik = html.split('id="sense-block-2"', 1)[1].split("</details>", 1)[0]
    assert "ZL 4620" in technik
    assert "4 Fundstellen über diese Bedeutung insgesamt" in technik
    # Checkboxen zum Ein- und Ausblenden je Bedeutung
    # (zwei Bedeutungen und der Block „Über die Eingabe gefunden“)
    assert html.count('class="sense-toggle"') == 3
    assert 'aria-controls="sense-block-1"' in html
    # Vorschlag ohne Bedeutung kommt in den Rest-Block
    rest = html.split('id="sense-block-rest"', 1)[1].split("</details>", 1)[0]
    assert "ZL 9999" in rest and "Über die Eingabe gefunden" in rest


def test_without_filter_first_sense_is_open(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter"})
    assert '<details id="sense-block-1" class="sense-block" open>' in html
    assert '<details id="sense-block-2" class="sense-block">' in html


def test_single_sense_is_shown_as_gnd_term(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Schraubenmutter"})
    assert "Ihre Suche wurde erweitert" in html
    assert "GND-Begriff „Mutter &lt;Technik&gt;“" in _tree(html)
    assert "Mehrere Bedeutungen gefunden" not in html


def test_without_senses_no_gnd_block(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe"})
    assert "GND-Begriff „" not in _tree(html)
    assert "Mehrere Bedeutungen" not in html
    assert "ZL 4620" in html
    assert seen["subject_filter"] == []
    assert seen["main_group_filter"] == []


def test_expansion_tree_with_lobid_links_and_relations(client):
    test_client, _ = client
    tree = _tree(_post(test_client, {"q": "Schraubenmutter"}))
    assert "Eingabe „Schraubenmutter“" in tree
    assert 'href="https://lobid.org/gnd/0000001-1"' in tree
    assert "Oberbegriffe:" in tree and "Schraubenverbindung" in tree
    assert 'href="https://lobid.org/gnd/4180000-1"' in tree
    assert 'hx-post="/search"' in tree and 'hx-trigger="change"' in tree
    assert 'name="baum" value="1"' in tree
    key = presentation.gnd_term_key(TECHNIK, SCHRAUBE)
    assert f'value="{key}" checked' in tree.replace("&lt;", "<").replace("&gt;", ">")


def test_deselected_gnd_term_is_not_searched(client):
    test_client, seen = client
    keep = presentation.gnd_term_key(TECHNIK, TECHNIK.terms[0])
    html = _post(test_client, {"q": "Schraubenmutter", "baum": "1", "begriff": [keep]})
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>"]
    # bleibt im Baum sichtbar, aber ohne Häkchen
    tree = _tree(html)
    assert "Schraubenverbindung" in tree
    assert "abgewählt, nicht gesucht" in tree


def test_without_tree_marker_nothing_is_deselected(client):
    test_client, seen = client
    _post(test_client, {"q": "Schraubenmutter"})
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>", "Schraubenverbindung"]


def test_deselected_llm_term_is_not_searched(client):
    test_client, seen = client
    keep = presentation.llm_term_key("Zahnrad")
    html = _post(test_client, {"q": "Getriebe", "baum": "1", "begriff": [keep]})
    assert [t.term for t in seen["llm_terms"]] == ["Zahnrad"]
    assert "abgewählt, nicht gesucht" in html


def test_llm_block_shows_verified_and_grey_unverified(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe"})

    assert "Vorschläge des Sprachmodells" in html
    assert "Bauteil, das Drehmoment überträgt" in html
    assert "verifiziert – gefunden im RVK-Register" in html
    assert "verifiziert – gefunden in GND" in html
    assert "Welle &lt;Technik&gt;" in html
    assert 'class="llm-unverified"' in html
    unverified = html.split('class="llm-unverified"', 1)[1].split("</li>", 1)[0]
    assert "Kraftschlussdings" in unverified and "erfundener Begriff" in unverified
    # Status als Text, nicht nur als Farbe
    assert "nicht verifiziert" in unverified and "nicht gesucht" in unverified
    assert 'name="begriff"' not in unverified
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

    def no_hits(conn, service, query, expanded_terms, **kwargs):
        return ranking.SearchResult(hits=[])

    monkeypatch.setattr(search_route.ranking, "search_result", no_hits)
    html = _post(test_client, {"q": "Getriebe"})
    assert "Keine Vorschläge für „Getriebe“ gefunden." in html
    assert "Vorschläge des Sprachmodells" in html


def test_results_show_relevance_tier_and_bar_without_percent(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    table = html.split('<table class="hits">', 1)[1].split("</table>", 1)[0]
    assert '<th scope="col">Relevanz</th>' in table
    assert "Konfidenz" not in html
    cells = re.findall(r'<td class="relevance-cell">(.*?)</td>', table, re.DOTALL)
    assert cells
    assert all("%" not in cell for cell in cells)
    assert "Wahrscheinlichkeit" not in cells[0]
    assert re.search(r'class="tier tier-(hoch|mittel|niedrig)"', table)
    assert 'title="Relevanzwert 0.50"' in table
    assert 'class="seg seg-cosine"' in table and 'title="Vektor: 0.46"' in table
    assert 'class="seg seg-gnd_bonus"' in table and 'title="GND: 0.04"' in table
    # Balken mit Textalternative, Bestandteile auch als Text
    assert (
        'role="img" aria-label="Bestandteile der Relevanz: Vektor 0.46, GND 0.04"'
        in table
    )
    assert "keine Wahrscheinlichkeit" in html


def test_hits_are_grouped_by_main_group_with_label_from_database(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    assert "Fachgebiete:" in html
    assert "ZL Maschinenbau (2)" in html
    assert "<caption>ZL Maschinenbau – 2 Vorschläge</caption>" in html


def test_why_chain_and_rvk_note_and_actions(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    row = html.split("<code>ZL 4620</code>", 1)[1].split("</tr>", 1)[0]
    assert "<summary>Warum?</summary>" in row
    assert "Eingabe „Getriebe“" in row
    assert "GND-Begriff" in row and "Registereintrag mit GND-Nummer an" in row
    assert "RVK-Bemerkung: Hier nur Normteile" in row
    assert "siehe auch: ZL 4600" in row
    assert "Hierarchie: Technik &gt; Maschinenelemente" in row
    # gleichrangige Textknöpfe statt Symbolen
    assert re.search(
        r'<button type="button" class="action copy-notation"[^>]*>kopieren</button>',
        row,
    )
    assert re.search(
        r'<a class="action rvk-online-link"[^>]*>in RVK-Online prüfen', row
    )
    assert "📋" not in html and "↗" not in html


def test_warning_only_for_pure_vector_evidence(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    gnd_row = html.split("<code>ZL 4620</code>", 1)[1].split("</tr>", 1)[0]
    vector_row = html.split("<code>ZL 9999</code>", 1)[1].split("</tr>", 1)[0]
    warning = "Nur über Ähnlichkeit gefunden – bitte im Register prüfen"
    assert warning not in gnd_row
    assert warning in vector_row
    assert 'role="note"' in vector_row


def test_changed_label_only_with_data(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe"})
    assert "geändert in Version" not in html
    conn = sqlite3.connect(seen["db_path"])
    conn.execute(
        "INSERT INTO notation_change (notation_id, notation, version, change_type) "
        "VALUES (1, 'ZL 4620', '2026/1', 'changed')"
    )
    conn.commit()
    conn.close()
    html = _post(test_client, {"q": "Getriebe"})
    row = html.split("<code>ZL 4620</code>", 1)[1].split("</tr>", 1)[0]
    assert "geändert in Version 2026/1" in row


def test_main_group_filter_hint(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL", "zm"], "hg_form": "1"})
    assert seen["main_group_filter"] == ["ZL", "ZM"]
    assert "3 weitere Fundstellen außerhalb der gewählten Hauptgruppen (ZL, ZM)" in html


def test_hg_and_old_fach_are_combined(client):
    test_client, seen = client
    _post(test_client, {"q": "Getriebe", "hg": ["ST"], "fach": "zl, st"})
    assert seen["main_group_filter"] == ["ST", "ZL"]


def test_default_main_groups_apply_without_form_selection(client):
    test_client, seen = client
    seen["app"].config["DEFAULT_MAIN_GROUPS"] = ("ZL",)
    _post(test_client, {"q": "Getriebe"})
    assert seen["main_group_filter"] == ["ZL"]
    # Kommt die Anfrage aus dem Formular ohne Häkchen, gilt „kein Filter“
    _post(test_client, {"q": "Getriebe", "hg_form": "1"})
    assert seen["main_group_filter"] == []


def test_wording_uses_vorschlaege(client):
    test_client, _ = client
    for query in ("Getriebe", "Mutter"):
        html = _post(test_client, {"q": query})
        assert "Ergebnis" not in html
        assert "Konfidenz" not in html
        # kein Prozentzeichen im Text (Links enthalten URL-Kodierung wie %20)
        assert "%" not in re.sub(r'href="[^"]*"', "", html)
        assert "Vorschläge" in html


def test_no_llm_block_without_suggestions(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter"})
    assert "Vorschläge des Sprachmodells" not in html


def test_index_footer_shows_rvk_version_and_main_groups(client):
    test_client, seen = client
    seen["app"].config["DEFAULT_MAIN_GROUPS"] = ("ZL",)
    html = test_client.get("/").get_data(as_text=True)
    assert "RVK-Stand: 2026/1" in html
    assert '<label for="q"' in html and 'id="q"' in html
    assert "Hauptgruppen einschränken" in html
    assert (
        '<input type="checkbox" name="hg" value="ZL" checked> ZL – Maschinenbau' in html
    )
    assert 'id="results" aria-live="polite"' in html
    assert 'href="#inhalt"' in html and 'href="/hilfe"' in html
    assert "Konfidenz" not in html and "Ergebnis" not in html


def test_index_footer_without_sync_log_says_unknown(client):
    test_client, seen = client
    conn = sqlite3.connect(seen["db_path"])
    conn.execute("DROP TABLE sync_log")
    conn.commit()
    conn.close()
    html = test_client.get("/").get_data(as_text=True)
    assert "RVK-Stand: unbekannt" in html


def test_index_without_database_still_renders(client, tmp_path):
    test_client, seen = client
    missing = tmp_path / "fehlt.sqlite3"
    seen["app"].config["DATABASE_PATH"] = str(missing)
    response = test_client.get("/")
    assert response.status_code == 200
    assert "RVK-Stand: unbekannt" in response.get_data(as_text=True)
    # nur lesend: es wird keine leere Datenbankdatei angelegt
    assert not missing.exists()


def test_usage_statistics_are_recorded(client):
    from app.services import usage_stats

    test_client, seen = client
    _post(test_client, {"q": "Getriebe"})
    _post(test_client, {"q": "   "})  # leere Eingabe zählt nicht
    stats = usage_stats.stats_for_day(str(seen["usage_path"]))
    assert stats["requests"] == 1
    assert stats["zero_hit_requests"] == 0
    assert stats["llm_cache_hits"] == 1


def test_usage_statistics_count_zero_hits(client, monkeypatch):
    from app.services import usage_stats

    test_client, seen = client
    monkeypatch.setattr(
        search_route.ranking,
        "search_result",
        lambda *a, **k: ranking.SearchResult(hits=[], senses=[]),
    )
    _post(test_client, {"q": "Unsinnswort"})
    stats = usage_stats.stats_for_day(str(seen["usage_path"]))
    assert (stats["requests"], stats["zero_hit_requests"]) == (1, 1)
    assert stats["zero_hit_rate"] == 1.0
