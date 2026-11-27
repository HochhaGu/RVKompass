"""Suchroute mit GND-Bedeutungen – ohne Netz, ohne Modell; Datenbank als temporäre Datei.

P10: Oberfläche nach docs/design.md – Chips, Facette mit Zahlen, Bedeutungs-Tabs,
Treffer-Karten, Start- und Keine-Treffer-Zustand.
"""

import html as html_lib
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
        "INSERT INTO notation (id, notation, label) VALUES (11, 'MS', 'Soziologie')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (12, 'AB', 'Allgemeines')"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (13, 'ZL 4600', 'Schrauben')"
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
        seen["query"] = query
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
            # ungefilterte Zahlen je Hauptgruppe (AB hat keine Fundstelle)
            main_group_counts={"ZL": 2, "MS": 3},
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


def _main(html):
    """Ergebnisbereich ohne die Out-of-band-Teile (Chips, Facette)."""
    return html.split('<div id="chips" hx-swap-oob="true">', 1)[0]


def _chips(html):
    return html.split('<div id="chips" hx-swap-oob="true">', 1)[1].split(
        '<div id="facet-list"', 1
    )[0]


def _facet(html):
    return html.split('<div id="facet-list" hx-swap-oob="true">', 1)[1].split(
        '<span id="facet-count"', 1
    )[0]


def _card(html, notation):
    return html.split(f'<code class="notation">{notation}</code>', 1)[1].split(
        "</article>", 1
    )[0]


def _unescape(text):
    return html_lib.unescape(text)


# --- Bedeutungen als Tabs -------------------------------------------------------------------


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


def _tabs(html):
    return re.findall(
        r'<button type="button" role="tab" id="tab-([^"]+)" aria-controls="([^"]+)" '
        r'aria-selected="(true|false)" tabindex="(0|-1)">([^<]*)</button>',
        html,
    )


def test_sense_tabs_select_matching_filter_and_show_counts(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter", "hg": "ZL"})
    assert (
        '<div class="sense-tabs" role="tablist" aria-label="Bedeutungen" hidden>'
        in html
    )
    tabs = _tabs(html)
    # Familie kommt in der Quelle zuerst, Technik passt zum Filter -> Technik gewählt
    assert [(t[1], t[2], t[3]) for t in tabs] == [
        ("sense-block-1", "false", "-1"),
        ("sense-block-2", "true", "0"),
        ("sense-block-rest", "false", "-1"),
    ]
    assert [t[0] for t in tabs] == [t[1] for t in tabs]
    assert html.count('aria-selected="true"') == 1
    labels = [t[4] for t in tabs]
    assert labels == [
        "Mutter (Familie) · 0",
        "Mutter (Technik) · 1",
        "Über die Eingabe gefunden · 1",
    ]
    technik = html.split('<section id="sense-block-2"', 1)[1].split("</section>", 1)[0]
    assert "ZL 4620" in technik
    assert "4 Fundstellen über diese Bedeutung insgesamt" in technik
    # Vorschlag ohne Bedeutung kommt in den Rest-Block
    rest = html.split('<section id="sense-block-rest"', 1)[1].split("</section>", 1)[0]
    assert "ZL 9999" in rest and "Über die Eingabe gefunden" in rest


def test_sense_panels_are_all_visible_without_javascript(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter"})
    panels = re.findall(
        r'<section id="(sense-block-[^"]+)" class="sense-panel"([^>]*)>', html
    )
    assert [p[0] for p in panels] == [
        "sense-block-1",
        "sense-block-2",
        "sense-block-rest",
    ]
    assert all("hidden" not in attrs for _, attrs in panels)
    # keine alten Umschalter mehr
    assert "sense-toggle" not in html


def test_without_filter_first_sense_is_selected(client):
    test_client, _ = client
    tabs = _tabs(_post(test_client, {"q": "Mutter"}))
    assert [t[2] for t in tabs] == ["true", "false", "false"]


def test_single_sense_has_no_tabs_and_shows_chips(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Schraubenmutter"})
    assert 'role="tablist"' not in html and 'role="tab"' not in html
    assert "Mehrere Bedeutungen gefunden" not in html
    chips = _chips(html)
    assert "Deine Suche wurde erweitert um:" in chips
    assert "Mutter &lt;Technik&gt;" in chips


def test_without_senses_no_gnd_chips(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe"})
    assert 'class="chip chip-gnd"' not in _chips(html)
    assert "Mehrere Bedeutungen" not in html
    assert "ZL 4620" in html
    assert seen["subject_filter"] == []
    assert seen["main_group_filter"] == []


# --- Chips: entfernen und wiederherstellen ----------------------------------------------------


def test_chips_with_type_remove_button_and_lobid_links(client):
    test_client, _ = client
    chips = _chips(_post(test_client, {"q": "Schraubenmutter"}))
    assert '<input type="hidden" name="chips_q" value="Schraubenmutter">' in chips
    assert '<input type="hidden" name="baum" value="1">' in chips
    assert 'aria-label="Schraubenverbindung entfernen"' in chips
    item = chips.split("Schraubenverbindung", 1)[1].split("</li>", 1)[0]
    # P11: Quelle und Typ als Text
    assert '<span class="chip-type">GND · Oberbegriff</span>' in item
    assert 'hx-post="/search"' in item and 'hx-include="#chips, #facet"' in item
    key = presentation.gnd_term_key(TECHNIK, SCHRAUBE)
    assert f'"entfernen": "{key}"' in _unescape(item).replace("\\u003c", "<").replace(
        "\\u003e", ">"
    )
    assert 'href="https://lobid.org/gnd/4180000-1"' in chips
    # gesuchte Begriffe als versteckte Felder
    assert f'name="begriff" value="{key}"' in _unescape(chips)


def test_remove_button_excludes_term_and_lists_it_for_restore(client):
    test_client, seen = client
    key = presentation.gnd_term_key(TECHNIK, SCHRAUBE)
    active = [presentation.gnd_term_key(t_s, t) for t_s in [TECHNIK] for t in t_s.terms]
    html = _post(
        test_client,
        {
            "chips_q": "Schraubenmutter",
            "baum": "1",
            "begriff": active,
            "entfernen": key,
            "hg_form": "1",
        },
    )
    # Suchbegriff kommt aus der Chip-Zeile
    assert seen["query"] == "Schraubenmutter"
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>"]
    chips = _chips(html)
    assert 'aria-label="Schraubenverbindung entfernen"' not in chips
    removed = chips.split('class="chips-removed"', 1)[1]
    assert "Entfernt" in removed
    assert "Schraubenverbindung wieder hinzufügen" in removed
    assert f'"zurueck": "{key}"' in _unescape(removed).replace("\\u003c", "<").replace(
        "\\u003e", ">"
    )
    assert f'name="begriff" value="{key}"' not in _unescape(chips)


def test_restore_brings_term_back(client):
    test_client, seen = client
    keep = presentation.gnd_term_key(TECHNIK, TECHNIK.terms[0])
    key = presentation.gnd_term_key(TECHNIK, SCHRAUBE)
    html = _post(
        test_client,
        {"chips_q": "Schraubenmutter", "baum": "1", "begriff": [keep], "zurueck": key},
    )
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>", "Schraubenverbindung"]
    chips = _chips(html)
    assert 'aria-label="Schraubenverbindung entfernen"' in chips
    assert 'class="chips-removed"' not in chips


def test_deselected_gnd_term_via_hidden_fields_is_not_searched(client):
    test_client, seen = client
    keep = presentation.gnd_term_key(TECHNIK, TECHNIK.terms[0])
    html = _post(test_client, {"q": "Schraubenmutter", "baum": "1", "begriff": [keep]})
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>"]
    assert "Schraubenverbindung wieder hinzufügen" in _chips(html)


def test_without_chip_marker_nothing_is_deselected(client):
    test_client, seen = client
    _post(test_client, {"q": "Schraubenmutter"})
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>", "Schraubenverbindung"]


def test_chips_of_another_query_are_ignored(client):
    # neue Eingabe im Suchfeld, Facette ändert sich: alte Abwahl gilt nicht
    test_client, seen = client
    keep = presentation.gnd_term_key(TECHNIK, TECHNIK.terms[0])
    _post(
        test_client,
        {"q": "Schraubenmutter", "chips_q": "Getriebe", "baum": "1", "begriff": [keep]},
    )
    terms = [t.term for s in seen["gnd_senses"] for t in s.terms]
    assert terms == ["Mutter <Technik>", "Schraubenverbindung"]


def test_deselected_llm_term_is_not_searched(client):
    test_client, seen = client
    keep = presentation.llm_term_key("Zahnrad")
    html = _post(test_client, {"q": "Getriebe", "baum": "1", "begriff": [keep]})
    assert [t.term for t in seen["llm_terms"]] == ["Zahnrad"]
    assert "abgewählt, nicht gesucht" in html
    assert "Welle &lt;Technik&gt; wieder hinzufügen" in _chips(html)


def test_llm_chips_show_relation_origin_and_status(client):
    test_client, seen = client
    html = _post(test_client, {"q": "Getriebe"})
    chips = _chips(html)

    zahnrad = chips.split('aria-label="Zahnrad entfernen"', 1)[0].rsplit("<li", 1)[1]
    # P11: Quelle der Prüfung als Text („im RVK-Register“ bzw. „in GND“)
    assert (
        '<span class="chip-type">KI, geprüft im RVK-Register · Unterbegriff</span>'
        in zahnrad
    )
    welle = chips.split('aria-label="Welle &lt;Technik&gt; entfernen"', 1)[0]
    assert "KI, geprüft in GND · verwandt" in welle.rsplit("<li", 1)[1]

    assert "Vorschläge des Sprachmodells" in chips
    assert "Bauteil, das Drehmoment überträgt" in chips
    assert "verifiziert – gefunden im RVK-Register" in chips
    assert "verifiziert – gefunden in GND" in chips
    assert 'class="llm-unverified"' in chips
    unverified = chips.split('class="llm-unverified"', 1)[1].split("</li>", 1)[0]
    assert "Kraftschlussdings" in unverified and "erfundener Begriff" in unverified
    # Status als Text, nicht nur als Farbe; nicht gesuchte Vorschläge ohne Chip
    assert "nicht verifiziert" in unverified and "nicht gesucht" in unverified
    assert "Kraftschlussdings entfernen" not in chips
    assert "(1 nicht gesucht)" in chips
    # Keine Prozentwerte oder Wahrscheinlichkeiten im LLM-Block
    block = chips.split('class="llm-details"', 1)[1].split("</details>", 1)[0]
    assert "%" not in block and "Konfidenz" not in block

    # gesucht werden nur die geprüften Begriffe, unter ihrer Schreibweise im Normvokabular
    assert [(t.term, t.relation) for t in seen["llm_terms"]] == [
        ("Zahnrad", "llm_verified"),
        ("Welle <Technik>", "llm_verified"),
    ]
    # eine gemeinsame GND-Quelle für GND-Erweiterung und LLM-Prüfung
    assert seen["llm_source"] is seen["gnd_source"] is not None


def test_no_llm_block_without_suggestions(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Mutter"})
    assert "Vorschläge des Sprachmodells" not in html


def test_chips_grouped_per_sense_with_several_senses(client):
    test_client, _ = client
    chips = _chips(_post(test_client, {"q": "Mutter"}))
    assert chips.count('class="chip-group-name"') == 2
    assert "Mutter &lt;Familie&gt;:" in chips and "Mutter &lt;Technik&gt;:" in chips


# --- Facette ----------------------------------------------------------------------------------


def test_facet_is_updated_out_of_band_with_counts(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL"], "hg_form": "1"})
    facet = _facet(html)
    assert "is-inactive" not in facet
    assert "Trefferzahlen erscheinen" not in facet
    zl = facet.split('value="ZL"', 1)[1].split("</li>", 1)[0]
    assert '<span class="facet-num">2<span class="visually-hidden"> Fundstellen' in zl
    ms = facet.split('value="MS"', 1)[1].split("</li>", 1)[0]
    assert '<span class="facet-num">3' in ms
    # Gruppe ohne Fundstelle: abgeschwächt, Zahl trotzdem als Text
    ab = (
        facet.split('value="AB"', 1)[0].rsplit("<li>", 1)[1]
        + facet.split('value="AB"', 1)[1].split("</li>", 1)[0]
    )
    assert 'class="facet-item is-empty"' in ab
    assert '<span class="facet-num">0' in ab
    # gewählte Gruppe angehakt und oben
    assert '<input type="checkbox" name="hg" value="ZL" checked>' in facet
    assert facet.index('value="ZL"') < facet.index('value="AB"')
    assert '<span id="facet-count" hx-swap-oob="true">1</span>' in html


def test_index_facet_greyed_out_but_not_disabled(client):
    test_client, seen = client
    seen["app"].config["DEFAULT_MAIN_GROUPS"] = ("ZL",)
    html = test_client.get("/").get_data(as_text=True)
    facet = html.split('<aside id="facet"', 1)[1].split("</aside>", 1)[0]
    assert 'class="facet-fieldset is-inactive"' in facet
    assert "Trefferzahlen erscheinen nach der ersten Suche." in facet
    assert "disabled" not in facet
    assert "facet-num" not in facet
    # Voreinstellung aus .env bleibt angehakt
    assert '<input type="checkbox" name="hg" value="ZL" checked>' in facet
    assert '<span class="facet-name">Maschinenbau</span>' in facet
    assert 'name="hg_form" value="1"' in facet
    assert 'hx-trigger="change"' in facet
    assert 'hx-include="#search-form, #facet, #chips"' in facet
    assert (
        '<button type="button" class="facet-toggle" aria-expanded="false" '
        'aria-controls="facet-body">Hauptgruppen filtern · '
        '<span id="facet-count">1</span> gewählt</button>'
    ) in facet


def test_layout_order_search_facet_results(client):
    test_client, _ = client
    html = test_client.get("/").get_data(as_text=True)
    search = html.index('class="search-card')
    facet = html.index('<aside id="facet"')
    results = html.index('<section id="results"')
    assert search < facet < results
    # Hinweiszeile exakt nach docs/design.md Abschnitt 7, direkt unter dem Suchschlitz
    hint = (
        "Vorschläge sind eine Orientierung, keine geprüfte Klassifikation – bitte die "
        "Notation vor der Vergabe in RVK-Online prüfen."
    )
    assert hint in html
    assert html.index("</form>") < html.index(hint) < html.index('<div id="chips">')
    assert ">Vorschläge anzeigen</button>" in html
    # Chips außerhalb des Formulars: eine neue Eingabe schickt keine alte Abwahl mit
    form = html.split('<form id="search-form"', 1)[1].split("</form>", 1)[0]
    assert 'id="chips"' not in form
    assert 'hx-include="#facet"' in html.split('<form id="search-form"', 1)[1]


# --- Startzustand -----------------------------------------------------------------------------


def test_start_state_on_index(client):
    test_client, _ = client
    html = test_client.get("/").get_data(as_text=True)
    start = html.split('<section id="results"', 1)[1].split("</section>", 1)[0]
    assert "So kommst du zur passenden RVK-Stelle" in start
    assert (
        start.split('<ol class="steps">', 1)[1].split("</ol>", 1)[0].count("<li>") == 3
    )
    examples = re.findall(
        r'class="btn-secondary example-search" href="([^"]+)" data-query="([^"]+)"',
        start,
    )
    assert [q for _, q in examples] == [
        "Klimawandel",
        "Umwelttechnik",
        "Konfliktmanagement",
        "Bank",
    ]
    assert examples[3][0] == "/?q=Bank"
    assert "(mehrdeutig)" in start
    assert "Häufig gesucht" not in html
    assert 'hx-trigger="load"' not in html


def test_empty_query_returns_start_state(client):
    test_client, _ = client
    html = _post(test_client, {"q": "   "})
    assert "So kommst du zur passenden RVK-Stelle" in _main(html)
    assert "is-inactive" in _facet(html)


def test_index_with_query_prefills_and_searches(client):
    test_client, _ = client
    html = test_client.get("/?q=ZL%204600").get_data(as_text=True)
    assert 'id="q" name="q" value="ZL 4600"' in html
    results = html.split('<section id="results"', 1)[1].split(">", 1)[0]
    assert 'hx-post="/search"' in results and 'hx-trigger="load"' in results
    assert 'hx-include="#search-form, #facet"' in results


# --- Keine Treffer ----------------------------------------------------------------------------


def _no_hits(counts):
    def search_result(conn, service, query, expanded_terms, **kwargs):
        return ranking.SearchResult(hits=[], main_group_counts=counts)

    return search_result


def test_no_hits_with_filter_offers_reset_with_count(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        search_route.ranking, "search_result", _no_hits({"MS": 3, "AB": 2})
    )
    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL", "ZM"], "hg_form": "1"})
    main = _main(html)
    assert "Keine Vorschläge in den gewählten Hauptgruppen (ZL, ZM)." in main
    button = main.split('class="btn-primary facet-reset"', 1)[1].split("</button>", 1)[
        0
    ]
    assert "Hauptgruppen-Auswahl zurücksetzen (5 Vorschläge)" in button
    assert 'hx-include="#chips"' in button
    assert 'hx-vals=\'{"hg_form": "1"}\'' in button
    assert "allgemeineren Begriff" in main
    assert 'href="mailto:rvkompass@ghochhauser.at">Feedback senden</a>' in main


def test_counts_above_list_limit_show_plus(client, monkeypatch):
    # P11: die Liste zeigt höchstens MAX_RESULTS (20) Vorschläge – größere Zahlen als „20+“
    test_client, seen = client
    seen["app"].config["MAX_RESULTS"] = 20
    monkeypatch.setattr(
        search_route.ranking, "search_result", _no_hits({"MS": 30, "AB": 2})
    )
    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL"], "hg_form": "1"})
    button = (
        _main(html)
        .split('class="btn-primary facet-reset"', 1)[1]
        .split("</button>", 1)[0]
    )
    assert "Hauptgruppen-Auswahl zurücksetzen (20+ Vorschläge)" in button
    facet = _facet(html)
    ms = facet.split('value="MS"', 1)[1].split("</li>", 1)[0]
    assert '<span class="facet-num">20+<span class="visually-hidden"> Fundstellen' in ms
    ab = facet.split('value="AB"', 1)[1].split("</li>", 1)[0]
    assert '<span class="facet-num">2<span' in ab
    # Summe der Buchstabengruppe ebenfalls gedeckelt
    m_head = facet.split('data-letter="M"', 1)[1].split("</summary>", 1)[0]
    assert "20+" in m_head


def test_list_limit_comes_from_config(client, monkeypatch):
    test_client, seen = client
    seen["app"].config["MAX_RESULTS"] = 5
    monkeypatch.setattr(search_route.ranking, "search_result", _no_hits({"MS": 6}))
    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL"], "hg_form": "1"})
    assert "zurücksetzen (5+ Vorschläge)" in _main(html)
    assert "20+" not in html


def test_facet_groups_letters_by_subject_with_titles(client):
    test_client, seen = client
    conn = sqlite3.connect(seen["db_path"])
    # oberste Ebene der Systematik (level 0) wie im RVK-Abzug, dazu eine zweite Z-Gruppe
    conn.execute(
        "INSERT INTO notation (id, notation, label, level) "
        "VALUES (20, 'ZG - ZS', 'Technik', 0), (21, 'ZX - ZY', 'Sport', 0)"
    )
    conn.execute(
        "INSERT INTO notation (id, notation, label) VALUES (22, 'ZM', 'Werkstoffe')"
    )
    conn.commit()
    conn.close()

    start = test_client.get("/").get_data(as_text=True)
    facet = start.split('<div id="facet-list">', 1)[1]
    # natives details/summary, standardmäßig eingeklappt, Titel aus der Datenbank
    z_group = facet.split('<details class="facet-group" data-letter="Z"', 1)[1]
    assert z_group.startswith(">")  # kein open
    assert "Z – Technik · Sport</span>" in z_group.split("</summary>", 1)[0]
    # vor der ersten Suche keine Summe
    assert "facet-group-num" not in z_group.split("</summary>", 1)[0]
    # Buchstabe ohne Eintrag der obersten Ebene in der Datenbank: Rückfall aus der Konfiguration
    assert "A – Allgemeines</span>" in facet

    html = _post(test_client, {"q": "Getriebe", "hg": ["ZL"], "hg_form": "1"})
    facet = _facet(html)
    # Gruppe mit gewählter Hauptgruppe ist offen; „Gewählt“ bleibt oben
    assert '<details class="facet-group" data-letter="Z" open>' in facet
    assert facet.index("Gewählt") < facet.index('data-letter="A"')
    assert '<details class="facet-group" data-letter="A">' in facet
    # Summe der Fundstellen der Gruppe in der Überschrift (MS: 3), mit Text für Screenreader
    m_head = facet.split('data-letter="M"', 1)[1].split("</summary>", 1)[0]
    assert (
        '<span class="facet-num facet-group-num">3<span class="visually-hidden">'
        " Fundstellen</span></span>" in m_head
    )


def test_reset_clears_filter_and_searches_query_of_chips(client):
    test_client, seen = client
    _post(test_client, {"chips_q": "Getriebe", "hg_form": "1"})
    assert seen["query"] == "Getriebe"
    assert seen["main_group_filter"] == []


def test_no_hits_without_filter_names_cause(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(search_route.ranking, "search_result", _no_hits({}))
    html = _post(test_client, {"q": "Getriebe"})
    main = _main(html)
    assert "Für „Getriebe“ wurde nichts gefunden." in main
    assert "facet-reset" not in main
    assert "allgemeineren Begriff" in main
    assert "Feedback senden" in main
    # Erweiterung bleibt sichtbar, auch ohne Vorschläge
    assert "Vorschläge des Sprachmodells" in _chips(html)


def test_no_hits_mentions_removed_terms(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(search_route.ranking, "search_result", _no_hits({}))
    keep = presentation.gnd_term_key(TECHNIK, TECHNIK.terms[0])
    html = _post(test_client, {"q": "Schraubenmutter", "baum": "1", "begriff": [keep]})
    main = _main(html)
    assert "1 Begriff entfernt – wieder hinzufügen" in main


# --- Treffer-Karte ----------------------------------------------------------------------------


def test_hit_card_relevance_block_without_percent(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    card = _card(html, "ZL 4620")
    # Standard max_plus (P11): Score 0.5 liegt unter „mittel“ (0.5218) und über „schwach“
    # (0.4304); bei legacy wäre es ebenfalls „schwach“ (0.5754 − 0.0797 ≤ 0.5 < 0.5754)
    assert (
        '<div class="relevance" role="img" aria-label="Relevanz schwach, Stufe 2 von 5" '
        'title="Relevanzwert 0.50">'
    ) in card
    assert len(re.findall(r'<span class="seg( is-on)?" aria-hidden="true">', card)) == 5
    assert card.count('<span class="seg is-on"') == 2
    assert '<span class="relevance-word">schwach</span>' in card
    # kein Prozentzeichen im Text (Links enthalten URL-Kodierung wie %20)
    assert "%" not in re.sub(r'href="[^"]*"', "", card) and "Konfidenz" not in card
    vector = _card(html, "ZL 9999")
    assert 'aria-label="Relevanz gering, Stufe 1 von 5"' in vector
    assert "keine Wahrscheinlichkeit" in html


def test_hits_are_grouped_by_main_group_with_label_from_database(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    assert '<h3 class="hit-group-title">ZL Maschinenbau – 2 Vorschläge</h3>' in html
    hits = html.split('<ol class="hits">', 1)[1].split('<p class="relevance-note"', 1)[
        0
    ]
    assert hits.count('<article class="hit-card"') == 2
    assert '<li value="1">' in hits and '<li value="2">' in hits


def test_why_label_rvk_note_links_and_actions(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    card = _card(html, "ZL 4620")
    assert "<summary>Warum? · RVK-Bemerkung</summary>" in card
    assert "Eingabe „Getriebe“" in card
    assert "GND-Begriff" in card and "Registereintrag mit GND-Nummer an" in card
    assert "<strong>RVK-Bemerkung:</strong> Hier nur Normteile" in card
    assert (
        '<strong>siehe auch:</strong> <a class="notation-link" href="/?q=ZL%204600" '
        'data-query="ZL 4600">→ ZL 4600 anzeigen</a>'
    ) in card
    assert (
        "Bestandteile der Relevanz (Relevanzwert 0.50, keine Wahrscheinlichkeit)"
        in card
    )
    assert "<li>Vektor: 0.46</li>" in card and "<li>GND: 0.04</li>" in card
    assert '<p class="hierarchy">Hierarchie: Technik &gt; Maschinenelemente</p>' in card
    assert '<p class="hit-title">Muttern</p>' in card
    # gleichrangige Textknöpfe statt Symbolen
    assert re.search(
        r'<button type="button" class="action copy-notation"[^>]*>kopieren</button>',
        card,
    )
    assert re.search(
        r'<a class="action rvk-online-link"[^>]*>in RVK-Online prüfen', card
    )
    assert "📋" not in html and "↗" not in html
    other = _card(html, "ZL 9999")
    assert "<summary>Warum dieser Vorschlag?</summary>" in other
    assert "RVK-Bemerkung" not in other


def test_warning_only_for_pure_vector_evidence(client):
    test_client, _ = client
    html = _post(test_client, {"q": "Getriebe"})
    warning = "Nur über Ähnlichkeit gefunden – bitte im Register prüfen"
    assert warning not in _card(html, "ZL 4620")
    vector_card = _card(html, "ZL 9999")
    assert warning in vector_card
    assert 'role="note"' in vector_card


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
    assert "geändert in Version 2026/1" in _card(html, "ZL 4620")


# --- Notation als Eingabe ---------------------------------------------------------------------


def test_entered_notation_card_is_separate_from_suggestions(client):
    test_client, seen = client
    html = _post(test_client, {"q": "zl 4620"})
    main = _main(html)
    card = main.split('<article class="entered-notation card"', 1)[1].split(
        "</article>", 1
    )[0]
    assert "Eingegebene Notation" in card
    assert '<code class="notation">ZL 4620</code>' in card
    assert "Herkunft: Direkt: Notation eingegeben" in card
    assert "→ ZL 4600 anzeigen" in card
    assert "Hierarchie: Technik &gt; Maschinenelemente" in card
    # die Vorschlagsliste kommt unverändert darunter
    assert main.index("entered-notation") < main.index('class="results-intro"')
    assert seen["query"] == "zl 4620"


def test_no_entered_notation_card_for_unknown_or_plain_query(client):
    test_client, _ = client
    assert "entered-notation" not in _post(test_client, {"q": "ZL 1234"})
    assert "entered-notation" not in _post(test_client, {"q": "Getriebe"})


# --- Filter, Voreinstellung, Wortwahl, Grundgerüst --------------------------------------------


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
    # Kommt die Anfrage aus der Facette ohne Häkchen, gilt „kein Filter“
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


def test_index_footer_header_and_navigation(client):
    test_client, _ = client
    html = test_client.get("/").get_data(as_text=True)
    assert "RVK-Stand: 2026/1" in html
    assert '<label for="q"' in html and 'id="q"' in html
    assert 'id="results" aria-live="polite"' in html
    assert 'href="#inhalt"' in html and 'href="/hilfe"' in html
    assert '<a href="/" aria-current="page">Suche</a>' in html
    assert '<a href="/hilfe">Hilfe</a>' in html
    assert '<a href="mailto:rvkompass@ghochhauser.at">Feedback</a>' in html
    assert html.count('href="https://ghochhauser.at') == 1
    assert (
        '<a href="https://ghochhauser.at/">Ein Projekt von ghochhauser.at</a>' in html
    )
    assert 'alt="RVKompass"' in html and "Klassifizieren mit Richtung" in html
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
    # Abfragen insgesamt (Nenner der Cache-Quote): hier ein Cache-Treffer, kein API-Aufruf
    assert stats["llm_lookups"] == 1


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
