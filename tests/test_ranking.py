from pathlib import Path

import pytest

from app.config import get_config
from app.services import ranking, rvk_repository

config = get_config()


def test_reciprocal_rank_fusion_prefers_items_ranked_high_in_multiple_lists():
    scores = ranking.reciprocal_rank_fusion([[1, 2, 3], [2, 3, 1], [3, 1, 2]])
    # Alle drei erscheinen in jeder Liste - Reihenfolge muss durch frühere Ränge entschieden werden
    assert scores[1] == pytest.approx(scores[2]) == pytest.approx(scores[3])


def test_reciprocal_rank_fusion_top_rank_wins_when_only_in_one_list():
    scores = ranking.reciprocal_rank_fusion([[1], []])
    assert scores[1] > 0


@pytest.mark.skipif(
    not (Path(config.DATABASE_PATH).exists() and Path(config.EMBEDDINGS_PATH).exists()),
    reason="Benötigt lokale RVK-Datenbank und Embeddings (sync_rvk.py + build_embeddings.py).",
)
def test_search_returns_ranked_results_for_known_term():
    from app.services import embedding_service

    service = embedding_service.EmbeddingService(
        model_name=config.EMBEDDING_MODEL_NAME,
        embeddings_path=config.EMBEDDINGS_PATH,
        embeddings_meta_path=config.EMBEDDINGS_META_PATH,
        register_term_embeddings_path=config.REGISTER_TERM_EMBEDDINGS_PATH,
        register_term_meta_path=config.REGISTER_TERM_META_PATH,
    )
    conn = rvk_repository.get_connection(config.DATABASE_PATH)
    try:
        results = ranking.search(conn, service, "Wörterbücher", [])
    finally:
        conn.close()

    assert results
    assert all("notation" in r and "label" in r for r in results)
