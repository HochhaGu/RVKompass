from pathlib import Path

import pytest

from app.config import get_config
from app.services import ranking, rvk_repository

config = get_config()


def test_build_rvk_online_url_matches_verified_deep_link_scheme():
    # Verifiziert gegen ein echtes Beispiel: #notation/ZG%209287
    assert (
        ranking.build_rvk_online_url("ZG 9287")
        == "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online"
        "?view=rvkonline#notation/ZG%209287"
    )


def test_build_rvk_online_url_encodes_range_notations():
    # Auch für Notationsbereiche verifiziert (z.B. "AA 10000 - AA 19900")
    url = ranking.build_rvk_online_url("AA 10000 - AA 19900")
    assert url.endswith("#notation/AA%2010000%20-%20AA%2019900")


@pytest.mark.parametrize(
    "notation,expected",
    [
        ("UT 8970", True),
        ("AI 2250", True),
        ("A", False),
        ("AA", False),
        ("ZG", False),
        ("AI 2200 - AI 2300", False),
        ("AA 10000 - AA 19900", False),
    ],
)
def test_is_assignable_notation(notation, expected):
    assert ranking.is_assignable_notation(notation) is expected


@pytest.mark.skipif(
    not (Path(config.DATABASE_PATH).exists() and Path(config.EMBEDDINGS_PATH).exists()),
    reason="Benötigt lokale RVK-Datenbank und Embeddings (sync_rvk.py + build_embeddings.py).",
)
class TestSearchIntegration:
    @pytest.fixture(scope="class")
    def service(self):
        from app.services import embedding_service

        return embedding_service.EmbeddingService(
            model_name=config.EMBEDDING_MODEL_NAME,
            embeddings_path=config.EMBEDDINGS_PATH,
            embeddings_meta_path=config.EMBEDDINGS_META_PATH,
            register_term_embeddings_path=config.REGISTER_TERM_EMBEDDINGS_PATH,
            register_term_meta_path=config.REGISTER_TERM_META_PATH,
        )

    @pytest.fixture
    def conn(self):
        connection = rvk_repository.get_connection(config.DATABASE_PATH)
        yield connection
        connection.close()

    def test_search_returns_ranked_results_for_known_term(self, service, conn):
        hits = ranking.search(conn, service, "Wörterbücher", [])

        assert hits
        assert all(hit.evidences for hit in hits)
        rows = ranking.hits_to_rows(conn, hits)
        assert all(
            "notation" in r and "label" in r and "rvk_online_url" in r for r in rows
        )

    def test_results_are_sorted_by_displayed_value_descending(self, service, conn):
        # Kernanforderung: die Reihenfolge muss exakt dem angezeigten Wert folgen,
        # damit Nutzer*innen sich auf "oben = passender" verlassen können.
        hits = ranking.search(conn, service, "Photosynthese", [])

        scores = [hit.score for hit in hits]
        assert scores == sorted(scores, reverse=True)
        rows = ranking.hits_to_rows(conn, hits)
        displayed = [r["score"] for r in rows]
        assert displayed == sorted(displayed, reverse=True)
        # Stufen folgen der Reihenfolge: nie „hoch“ nach „mittel“ oder „niedrig“
        order = {"hoch": 0, "mittel": 1, "niedrig": 2}
        tiers = [order[r["tier"]] for r in rows]
        assert tiers == sorted(tiers)

    def test_results_page_renders_with_real_data(self, service, monkeypatch):
        """P6: Vorschlagsliste mit echten Daten rendern – ohne Netz (GND und LLM aus)."""
        import re

        from flask import Flask

        from app import limiter, register_template_context
        from app.routes import search as search_route

        templates = Path(__file__).resolve().parent.parent / "app" / "templates"
        app = Flask(__name__, template_folder=str(templates))
        app.config.update(
            TESTING=True,
            RATELIMIT_ENABLED=False,
            DATABASE_PATH=config.DATABASE_PATH,
            ENABLE_QUERY_EXPANSION=False,
            ENABLE_GND_EXPANSION=False,
            DEFAULT_MAIN_GROUPS=(),
        )
        limiter.init_app(app)
        app.register_blueprint(search_route.bp)
        register_template_context(app)
        monkeypatch.setattr(
            search_route.embedding_service, "get_service", lambda: service
        )
        client = app.test_client()

        html = client.post("/search", data={"q": "Wärmepumpe"}).get_data(as_text=True)
        assert "<summary>Warum?</summary>" in html
        assert "Fachgebiete:" in html
        assert "in RVK-Online prüfen" in html
        # Hauptgruppen tragen ihre Bezeichnung aus der Datenbank
        assert re.search(r"<caption>[A-Z]{2} [^<]+ – \d+ Vorschl", html)
        assert "Konfidenz" not in html and "Ergebnis" not in html

        filtered = client.post(
            "/search", data={"q": "Wärmepumpe", "hg": "ZP", "hg_form": "1"}
        ).get_data(as_text=True)
        captions = re.findall(r"<caption>([A-Z]+) ", filtered)
        assert captions and set(captions) == {"ZP"}

        start = client.get("/").get_data(as_text=True)
        assert re.search(r"RVK-Stand: \d{4}/\d+", start)
        assert 'name="hg" value="ZP"' in start
