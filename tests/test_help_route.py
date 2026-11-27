"""Hilfeseite /hilfe (P6)."""

import re
from pathlib import Path

import pytest
from flask import Flask

from app.routes import help as help_route
from app.services import scoring

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def _help(**config):
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config.update(TESTING=True, **config)
    app.register_blueprint(help_route.bp)
    response = app.test_client().get("/hilfe")
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.fixture
def html():
    # Standard seit P4: Sprachmodell aus
    return _help(ENABLE_QUERY_EXPANSION=False)


@pytest.fixture
def html_llm():
    return _help(ENABLE_QUERY_EXPANSION=True)


def test_title_and_overview_first(html):
    assert "So arbeitest du mit RVKompass" in html
    # „du“-Form: keine Höflichkeitsform mehr („Ihr Begriff“, „treffen Sie“)
    main = html.split("<main", 1)[1]
    assert not re.search(r"\bIhr(e|en|em|er)?\b|\b\w+en Sie\b", main)
    overview = html.index("Übersicht")
    first_section = html.index('<h3 id="was-ist">')
    assert overview < first_section
    assert "img/logo-mark.svg" in html


def test_required_content(html):
    assert "Was ist RVKompass?" in html
    assert "Vorschlag ≠ Entscheidung" in html
    assert "Aufstellungspraxis" in html
    assert "Signaturbildung" in html
    assert "Nur über Ähnlichkeit gefunden – bitte im Register prüfen" in html
    assert "keine Wahrscheinlichkeit" in html
    for path in scoring.PATH_TYPES:
        if path != "llm_verified":
            assert scoring.PATH_LABELS[path] in html


def test_help_without_llm_promises_no_ai_expansion(html):
    # P11: Sprachmodell aus – kein Fundweg „LLM-verifiziert“, keine KI-Chips, kein Versprechen
    assert scoring.PATH_LABELS["llm_verified"] not in html
    for text in ("KI, geprüft", "Sprachmodell", "semantische Unterstützung mit KI"):
        assert text not in html
    assert "semantische" in html and "Text-Embeddings" in html
    assert "über ein Sprachmodell für Texte" not in html


def test_help_with_llm_explains_ai_chips_and_path(html_llm):
    for path in scoring.PATH_TYPES:
        assert scoring.PATH_LABELS[path] in html_llm
    bedienung = html_llm.split('<h3 id="bedienung">', 1)[1].split("<h3", 1)[0]
    assert "KI, geprüft in GND" in bedienung
    assert "KI, geprüft im RVK-Register" in bedienung
    assert "geprüfte Vorschläge eines Sprachmodells" in html_llm


def test_help_explains_new_operation(html):
    bedienung = html.split('<h3 id="bedienung">', 1)[1].split("<h3", 1)[0]
    # Chips: entfernen und wiederherstellen, Quelle als Text (P11)
    assert "Chip" in bedienung and "×" in bedienung
    assert "wieder hinzufügen" in bedienung and "GND · Synonym" in bedienung
    # P11: Facette nach Fachgebieten, einklappbar, Obergrenze als „20+“
    assert "S – Mathematik · Informatik" in bedienung
    assert "auf und zu" in bedienung
    assert "20+" in bedienung
    # Facette mit Zahlen, Tabs, Verweis-Links
    assert "Hauptgruppen filtern" in bedienung and "Fundstellen" in bedienung
    assert "Tabs" in bedienung and "Pfeiltasten" in bedienung
    assert "→ ST 301 anzeigen" in bedienung
    assert "Warum? · RVK-Bemerkung" in html and "Warum dieser Vorschlag?" in html


def test_help_names_five_relevance_words(html):
    relevanz = html.split('<h3 id="relevanz">', 1)[1].split("<h3", 1)[0]
    for word in ("gering", "schwach", "mittel", "hoch", "sehr hoch"):
        assert f"<strong>{word}</strong>" in relevanz
    assert "keine Wahrscheinlichkeit" in relevanz
    # keine Aussagen über Treffsicherheit (äußere Stufen sind nicht kalibriert)
    for claim in ("Genauigkeit", "Trefferquote", "zuverlässig richtig"):
        assert claim not in relevanz


def test_help_in_new_layout(html):
    assert '<article class="card prose help">' in html
    assert 'alt="RVKompass"' in html and "Ein Projekt von ghochhauser.at" in html


def _legal(path):
    from app.routes import legal

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config.update(TESTING=True)
    app.register_blueprint(legal.bp)
    response = app.test_client().get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_legal_pages_in_card_with_unchanged_core_text():
    impressum = _legal("/impressum")
    assert '<article class="card prose">' in impressum
    assert "Offenlegung gemäß § 25 Mediengesetz" in impressum
    assert "Günther Hochhauser, BA<br>4707 Schlüßlberg<br>Österreich" in impressum
    assert "Die Ergebnisse sind Vorschläge und ersetzen keine fachliche Prüfung." in (
        " ".join(impressum.split())
    )
    datenschutz = _legal("/datenschutz")
    assert '<article class="card prose">' in datenschutz
    assert "Es werden keine Cookies gesetzt." in datenschutz
    assert "Art. 6 Abs. 1 lit. f DSGVO" in datenschutz


def test_privacy_page_mentions_no_ip_storage():
    from app.routes import legal

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config.update(TESTING=True)
    app.register_blueprint(legal.bp)
    response = app.test_client().get("/datenschutz")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "keine IP-Adressen" in html
    assert "Tageszähler" in html
    assert "Anthropic" in html
    assert "Konfidenz" not in html


def test_no_confidence_or_percent(html):
    assert "Konfidenz" not in html
    assert "%" not in html
