"""Hilfeseite /hilfe (P6)."""

from pathlib import Path

import pytest
from flask import Flask

from app.routes import help as help_route
from app.services import scoring

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture
def html():
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.config.update(TESTING=True)
    app.register_blueprint(help_route.bp)
    response = app.test_client().get("/hilfe")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_title_and_overview_first(html):
    assert "So arbeiten Sie mit RVKompass" in html
    overview = html.index("Übersicht")
    first_section = html.index('<h3 id="vorschlag">')
    assert overview < first_section


def test_required_content(html):
    assert "Vorschlag ≠ Entscheidung" in html
    assert "Aufstellungspraxis" in html
    assert "Signaturbildung" in html
    assert "Nur über Ähnlichkeit gefunden – bitte im Register prüfen" in html
    assert "keine Wahrscheinlichkeit" in html
    for path in scoring.PATH_TYPES:
        assert scoring.PATH_LABELS[path] in html


def test_no_confidence_or_percent(html):
    assert "Konfidenz" not in html
    assert "%" not in html
