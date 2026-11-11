"""Sucht pro Suchbegriff (Multi-Query) und fasst die Funde zu einer nach Relevanz sortierten
Trefferliste zusammen. Jeder Treffer (`Hit`) trägt seine Evidenzen: über welchen Begriff und
welchen Weg (Volltext, Vektor, Registerbegriff) er gefunden wurde.

Suchbegriffe sind die Eingabe und jeder Erweiterungsbegriff einzeln. Früher wurden sie zu einem
Text und einem Vektor verschmolzen; dann ließ sich kein Treffer einem Begriff zuordnen, und die
Erweiterung verwässerte die eigentliche Anfrage.

Grundlage der Sortierung ist die Cosine-Similarity zur Eingabeanfrage. Volltext- und
Registerbegriff-Treffer sowie Erweiterungsbegriffe erweitern nur die Kandidatenmenge (Recall);
Volltext- und Registertreffer geben zusätzlich einen kleinen additiven Bonus. Der Score ist ein
Relevanzmaß, keine Wahrscheinlichkeit. Sortierung und angezeigter Wert beruhen auf derselben Zahl,
damit die Reihenfolge nie der Anzeige widerspricht (eine frühere Reciprocal-Rank-Fusion hatte
genau dieses Problem).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from app.models import Evidence, ExpansionTerm, Hit
from app.services import rvk_repository
from app.services.query_expansion import normalize_query

MAX_RESULTS = 20
KEYWORD_BONUS = 0.03
TERM_MATCH_BONUS = 0.015

# Parameter je Suchbegriff (unverändert gegenüber der Suche vor P2)
KEYWORD_LIMIT = 50
NOTATION_TOP_K = 50
REGISTER_TOP_K = 30

RVK_ONLINE_BASE_URL = (
    "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online"
)


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


def build_expansion_terms(query: str, expanded_terms: list[str]) -> list[ExpansionTerm]:
    """Suchbegriffe: zuerst die Eingabe, dann jeder Erweiterungsbegriff einzeln.

    Leere und (nach Normalisierung) doppelte Begriffe werden übersprungen. Die Normalisierung
    dient nur der Dublettenerkennung; gesucht wird mit dem Begriff, wie er geliefert wurde.
    """
    terms = [ExpansionTerm(query, "input", "direct")]
    seen = {normalize_query(query)}
    for raw in expanded_terms:
        normalized = normalize_query(raw or "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        # VORLÄUFIG: Die heutigen Claude-Begriffe sind ungeprüft, daher "llm_unverified"
        # statt "llm_verified" (siehe app/models.py). P4 stellt auf geprüfte Begriffe um.
        terms.append(ExpansionTerm(raw.strip(), "llm", "llm_unverified"))
    return terms


@dataclass
class Retrieval:
    """Rohfunde aller Suchbegriffe, bevor gefiltert und sortiert wird."""

    query_vec: object
    # Cosine-Werte der Vektorsuche für die Eingabeanfrage (Grundlage des Scores)
    input_vector_scores: dict[int, float]
    keyword_ids: set[int]
    register_ids: set[int]
    # Notationen, die nur die Vektorsuche eines Erweiterungsbegriffs geliefert hat
    expansion_vector_ids: set[int]
    evidence_by_id: dict[int, list[Evidence]] = field(default_factory=dict)

    def add(self, evidence: Evidence) -> None:
        bucket = self.evidence_by_id.setdefault(evidence.notation_id, [])
        if evidence not in bucket:
            bucket.append(evidence)


def collect_evidence(conn, embedding_service, terms: list[ExpansionTerm]) -> Retrieval:
    """Führt die Suche für jeden Begriff einzeln aus und sammelt die Evidenzen je Notation.

    `terms[0]` muss die Eingabeanfrage sein. Die Volltextsuche läuft (wie vor P2) nur auf der
    Eingabe; Vektor- und Registersuche laufen für jeden Begriff.
    """
    input_term = terms[0]
    keyword_results = rvk_repository.keyword_search(
        conn, input_term.term, limit=KEYWORD_LIMIT
    )
    # Mengen genau wie vor P2 aufbauen: Ihre Iterationsreihenfolge bestimmt, wie Gleichstände
    # in der stabilen Sortierung aufgelöst werden.
    keyword_ids = {nid for nid, _ in keyword_results}

    query_vec = None
    input_vector_scores: dict[int, float] = {}
    register_ids: set[int] = set()
    expansion_vector_ids: set[int] = set()
    retrieval = Retrieval(
        query_vec=None,
        input_vector_scores=input_vector_scores,
        keyword_ids=keyword_ids,
        register_ids=register_ids,
        expansion_vector_ids=expansion_vector_ids,
    )

    for nid, bm25 in keyword_results:
        retrieval.add(Evidence(nid, input_term, "label_fts", float(bm25)))

    for index, term in enumerate(terms):
        vec = embedding_service.embed_query(term.term)
        vector_hits = embedding_service.search_notations(vec, top_k=NOTATION_TOP_K)
        if index == 0:
            query_vec = vec
            input_vector_scores.update(vector_hits)
        for nid, cosine in vector_hits:
            if index > 0 and nid not in input_vector_scores:
                expansion_vector_ids.add(nid)
            retrieval.add(Evidence(nid, term, "vector", float(cosine)))

        for register_term, cosine in embedding_service.search_register_terms(
            vec, top_k=REGISTER_TOP_K
        ):
            for nid, _entry_type, gnd_id in rvk_repository.get_notation_ids_for_term(
                conn, register_term
            ):
                register_ids.add(nid)
                # ÜBERGANGSZUORDNUNG: Der Registerbegriff wurde per Embedding-Ähnlichkeit zum
                # Suchbegriff gefunden, nicht über eine exakte Übereinstimmung. "register_exact"
                # trifft nur die zweite Hälfte der Kette zu (Registerbegriff -> Notation ist exakt
                # im RVK-Register hinterlegt). Die Embedding-Hälfte wird über via_term sichtbar
                # (source="vector", relation="embedding", parent_term=Suchbegriff, raw_score =
                # Cosine). Eine eigene Kennung für diesen Weg kann mit P3/P5 folgen.
                via = ExpansionTerm(
                    register_term,
                    "vector",
                    "embedding",
                    gnd_id=gnd_id or None,
                    parent_term=term.term,
                )
                retrieval.add(Evidence(nid, via, "register_exact", float(cosine)))

    retrieval.query_vec = query_vec
    return retrieval


def search(conn, embedding_service, query: str, expanded_terms: list[str]) -> list[Hit]:
    terms = build_expansion_terms(query, expanded_terms)
    retrieval = collect_evidence(conn, embedding_service, terms)

    keyword_ids = retrieval.keyword_ids
    vector_score_by_id = retrieval.input_vector_scores
    term_notation_ids = retrieval.register_ids

    # Kandidatenmenge in derselben Form wie vor P2 (siehe Kommentar in collect_evidence).
    # Treffer nur von Erweiterungsbegriffen werden danach in-place ergänzt; ohne Erweiterung
    # ändert das nichts an der Menge oder ihrer Reihenfolge.
    all_candidate_ids = keyword_ids | set(vector_score_by_id) | term_notation_ids
    if retrieval.expansion_vector_ids:
        all_candidate_ids |= retrieval.expansion_vector_ids
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

    # Der Score ist zugleich der angezeigte Wert - beide müssen identisch sein, sonst kann die
    # Reihenfolge wieder von der Anzeige abweichen (genau das Problem der früheren RRF-Lösung).
    # Er ist ein Relevanzmaß, keine Wahrscheinlichkeit, und wird auf 1.0 gedeckelt.
    components_by_id: dict[int, dict[str, float]] = {}
    score_by_id: dict[int, float] = {}
    for nid in candidate_ids:
        cosine = vector_score_by_id.get(nid)
        if cosine is None:
            cosine = (
                embedding_service.score_for_notation(retrieval.query_vec, nid) or 0.0
            )

        score = cosine
        keyword_bonus = 0.0
        register_bonus = 0.0
        if nid in keyword_ids:
            keyword_bonus = KEYWORD_BONUS
            score += KEYWORD_BONUS
        if nid in term_notation_ids:
            register_bonus = TERM_MATCH_BONUS
            score += TERM_MATCH_BONUS
        score_by_id[nid] = min(score, 1.0)
        components_by_id[nid] = {
            "cosine": cosine,
            "keyword_bonus": keyword_bonus,
            "register_bonus": register_bonus,
        }

    ranked_ids = sorted(candidate_ids, key=lambda nid: score_by_id[nid], reverse=True)[
        :MAX_RESULTS
    ]

    return [
        Hit(
            notation=notations[nid],
            evidences=list(retrieval.evidence_by_id[nid]),
            score=score_by_id[nid],
            score_components=components_by_id[nid],
        )
        for nid in ranked_ids
    ]


def hits_to_rows(conn, hits: list[Hit]) -> list[dict]:
    """Zeilen für die Ergebnistabelle (_results_table.html), unverändert gegenüber vor P2."""
    rows = []
    for hit in hits:
        notation = hit.notation
        rows.append(
            {
                "notation": notation.notation,
                "label": notation.label,
                "hierarchy_path": notation.hierarchy_path,
                "related_terms": rvk_repository.get_register_terms(
                    conn, notation.id, limit=3
                ),
                "confidence_pct": round(hit.score * 100),
                "rvk_online_url": build_rvk_online_url(notation.notation),
            }
        )
    return rows
