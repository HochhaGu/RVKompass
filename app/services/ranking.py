"""Kombiniert Keyword- und Vektor-Treffer zu einer einzigen, nach Relevanz sortierten Trefferliste.

Grundlage der Sortierung ist die Cosine-Similarity zur Anfrage; Keyword- und Registerbegriff-
Treffer erweitern nur die Kandidatenmenge (Recall) und geben einen kleinen additiven Bonus für
exakte lexikalische Übereinstimmung. Der resultierende Score ist zugleich der angezeigte
Konfidenzwert - eine frühere Reciprocal-Rank-Fusion über getrennte Ranglisten führte dazu, dass
die Reihenfolge nicht mit der angezeigten Konfidenz übereinstimmte (ein 45%-Treffer konnte einen
80%-Treffer überholen, wenn er zusätzlich in den anderen Listen gut platziert war).
"""

from __future__ import annotations

from urllib.parse import quote

from app.services import rvk_repository

MAX_RESULTS = 20
KEYWORD_BONUS = 0.03
TERM_MATCH_BONUS = 0.015

RVK_ONLINE_BASE_URL = "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online"


def build_rvk_online_url(notation: str) -> str:
    """Deep-Link zu einer einzelnen Notation in RVK-Online (verifiziert, z.B. #notation/ZG%209287)."""
    return f"{RVK_ONLINE_BASE_URL}?view=rvkonline#notation/{quote(notation)}"


def is_assignable_notation(notation: str) -> bool:
    """Nur konkrete, einer Ressource zuweisbare Notationen sind als Ergebnis relevant.

    Ausgeschlossen: reine Buchstaben-Notationen ohne Zahl (z.B. "A", "AA", "ZG" - das sind
    Oberkategorien/Hierarchie-Ebenen, keine vergebbaren Signaturen) und Notationsbereiche
    (z.B. "AI 2200 - AI 2300" - eine Spanne, keine eindeutige Notation).
    """
    return " - " not in notation and any(char.isdigit() for char in notation)


def search(conn, embedding_service, query: str, expanded_terms: list[str]) -> list[dict]:
    keyword_ids = {nid for nid, _ in rvk_repository.keyword_search(conn, query, limit=50)}

    embed_text = " ".join([query, *expanded_terms])
    query_vec = embedding_service.embed_query(embed_text)

    vector_score_by_id = dict(embedding_service.search_notations(query_vec, top_k=50))

    term_notation_ids: set[int] = set()
    for term, _ in embedding_service.search_register_terms(query_vec, top_k=30):
        for nid, _entry_type, _gnd in rvk_repository.get_notation_ids_for_term(conn, term):
            term_notation_ids.add(nid)

    all_candidate_ids = keyword_ids | set(vector_score_by_id) | term_notation_ids
    if not all_candidate_ids:
        return []

    notations = rvk_repository.get_notations_by_ids(conn, list(all_candidate_ids))
    candidate_ids = {
        nid
        for nid in all_candidate_ids
        if nid in notations and is_assignable_notation(notations[nid].notation)
    }
    if not candidate_ids:
        return []

    # sort_score ist zugleich der angezeigte Konfidenzwert - beide müssen identisch sein,
    # sonst kann die Reihenfolge wieder von der sichtbaren Prozentzahl abweichen (genau das
    # Problem der vorherigen RRF-Lösung). Der kleine Keyword-/Registerbonus wird daher in
    # die Anzeige übernommen statt separat gehalten, und auf 100% gedeckelt.
    sort_score_by_id: dict[int, float] = {}
    for nid in candidate_ids:
        confidence = vector_score_by_id.get(nid)
        if confidence is None:
            confidence = embedding_service.score_for_notation(query_vec, nid) or 0.0

        sort_score = confidence
        if nid in keyword_ids:
            sort_score += KEYWORD_BONUS
        if nid in term_notation_ids:
            sort_score += TERM_MATCH_BONUS
        sort_score_by_id[nid] = min(sort_score, 1.0)

    ranked_ids = sorted(candidate_ids, key=lambda nid: sort_score_by_id[nid], reverse=True)[
        :MAX_RESULTS
    ]

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
                "confidence_pct": round(sort_score_by_id[nid] * 100),
                "rvk_online_url": build_rvk_online_url(notation.notation),
            }
        )
    return results
