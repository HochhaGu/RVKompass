"""Kombiniert Keyword- und Vektor-Treffer zu einer einzigen Trefferliste.

Nutzt Reciprocal Rank Fusion (RRF) statt gewichteter Score-Summen, weil bm25 (SQLite FTS5)
und Cosine-Similarity unterschiedliche, nicht direkt vergleichbare Skalen haben. RRF
kombiniert allein anhand der Rangposition pro Quelle - robust ohne Normalisierungsaufwand.
"""

from __future__ import annotations

from collections import defaultdict

from app.services import rvk_repository

RRF_K = 60
MAX_RESULTS = 20


def reciprocal_rank_fusion(ranked_id_lists: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = defaultdict(float)
    for ranked_ids in ranked_id_lists:
        for rank, item_id in enumerate(ranked_ids):
            scores[item_id] += 1.0 / (k + rank + 1)
    return scores


def search(conn, embedding_service, query: str, expanded_terms: list[str]) -> list[dict]:
    keyword_hits = [nid for nid, _ in rvk_repository.keyword_search(conn, query, limit=50)]

    embed_text = " ".join([query, *expanded_terms])
    query_vec = embedding_service.embed_query(embed_text)

    vector_hits = [nid for nid, _ in embedding_service.search_notations(query_vec, top_k=50)]

    term_hits_raw = embedding_service.search_register_terms(query_vec, top_k=30)
    term_notation_ids: list[int] = []
    seen = set()
    for term, _ in term_hits_raw:
        for nid, _entry_type, _gnd in rvk_repository.get_notation_ids_for_term(conn, term):
            if nid not in seen:
                seen.add(nid)
                term_notation_ids.append(nid)

    fused = reciprocal_rank_fusion([keyword_hits, vector_hits, term_notation_ids])
    if not fused:
        return []

    ranked_ids = sorted(fused, key=lambda nid: fused[nid], reverse=True)[:MAX_RESULTS]
    notations = rvk_repository.get_notations_by_ids(conn, ranked_ids)

    results = []
    for nid in ranked_ids:
        notation = notations.get(nid)
        if notation is None:
            continue
        results.append(
            {
                "notation": notation.notation,
                "label": notation.label,
                "hierarchy_path": notation.hierarchy_path,
                "related_terms": rvk_repository.get_register_terms(conn, nid, limit=3),
                "score": fused[nid],
            }
        )
    return results
