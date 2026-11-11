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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from app.config import Config
from app.models import Evidence, ExpansionTerm, GndSense, Hit
from app.services import rvk_repository
from app.services.query_expansion import normalize_query

MAX_RESULTS = 20
KEYWORD_BONUS = 0.03
TERM_MATCH_BONUS = 0.015
# P3: Zuschlag für Treffer über die GND-Bedeutung selbst bzw. ein Synonym (über GND-ID oder
# exakten Registerbegriff) und für Treffer über Ober-, Unter- oder verwandte Begriffe.
# Es zählt nur der höchste zutreffende Zuschlag.
GND_SYNONYM_BONUS = 0.08
GND_RELATION_BONUS = 0.02
GND_REGISTER_LIMIT = 10

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
    # P3: Notation -> Beziehungen der GND-Begriffe, über die sie gefunden wurde
    gnd_relation_by_id: dict[int, set[str]] = field(default_factory=dict)

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


def collect_gnd_evidence(
    conn, retrieval: Retrieval, senses: Iterable[GndSense]
) -> None:
    """Ergänzt die Evidenzen um die GND-Pfade (P3), ohne Vektorsuche.

    1. GND-Begriff -> exakt gleicher Registerbegriff -> Notation (`register_exact`, 1.0)
    2. GND-Begriff -> GND-ID -> Registereintrag mit dieser GND-ID (`register_gnd_id`, 1.0)
    """
    gnd_terms = [term for sense in senses for term in sense.terms]
    if not gnd_terms:
        return

    def note(nid: int, term: ExpansionTerm, match_type: str) -> None:
        retrieval.add(Evidence(nid, term, match_type, 1.0))
        retrieval.gnd_relation_by_id.setdefault(nid, set()).add(term.relation)

    for term in gnd_terms:
        for nid, _entry_type, _gnd_id in rvk_repository.get_notation_ids_for_term(
            conn, term.term, limit=GND_REGISTER_LIMIT
        ):
            note(nid, term, "register_exact")

    ids_by_gnd = rvk_repository.get_notations_by_gnd_ids(
        conn,
        [t.gnd_id for t in gnd_terms if t.gnd_id],
        limit_per_id=GND_REGISTER_LIMIT,
    )
    for term in gnd_terms:
        for nid in ids_by_gnd.get(term.gnd_id or "", []):
            note(nid, term, "register_gnd_id")


def gnd_bonus_for(relations: set[str] | None) -> float:
    """Höchster zutreffender GND-Zuschlag; ohne GND-Evidenz 0."""
    if not relations:
        return 0.0
    if "synonym" in relations:
        return GND_SYNONYM_BONUS
    return GND_RELATION_BONUS


@dataclass
class SenseResult:
    """Eine GND-Bedeutung mit den Notationen, die über ihre Begriffe gefunden wurden."""

    sense: GndSense
    notation_ids: list[int]
    hit_count: int
    matches_filter: bool
    rvk_groups: tuple[str, ...] = ()


@dataclass
class SearchResult:
    hits: list[Hit]
    senses: list[SenseResult] = field(default_factory=list)


def rvk_groups_for_sense(
    sense: GndSense, mapping: Mapping[str, Iterable[str]]
) -> tuple[str, ...]:
    """RVK-Hauptgruppen zu den GND-Sachgruppen einer Bedeutung laut Zuordnungstabelle.

    Ein Tabellenschlüssel mit "*" am Ende gilt für alle Codes, die so beginnen.
    """
    groups: list[str] = []
    for code, _label in sense.subject_categories:
        for key, rvk_groups in mapping.items():
            matches = code.startswith(key[:-1]) if key.endswith("*") else code == key
            if matches:
                groups.extend(g for g in rvk_groups if g not in groups)
    return tuple(groups)


def rank_senses(
    senses: Iterable[GndSense],
    evidence_by_id: Mapping[int, list[Evidence]],
    subject_filter: Iterable[str] | None,
    mapping: Mapping[str, Iterable[str]],
    score_by_id: Mapping[int, float] | None = None,
) -> list[SenseResult]:
    """Rangfolge der Bedeutungen: erst Übereinstimmung mit dem Fachfilter, dann Trefferzahl,
    dann die Reihenfolge der GND-Quelle. Kein LLM.

    `notation_ids` sind die Notationen mit Evidenz über einen Begriff dieser Bedeutung, nach
    `score_by_id` absteigend (sonst in der Reihenfolge von `evidence_by_id`).
    """
    wanted = {g.strip().upper() for g in (subject_filter or []) if g and g.strip()}
    results = []
    for sense in senses:
        sense_terms = set(sense.terms)
        nids = [
            nid
            for nid, evidences in evidence_by_id.items()
            if any(e.via_term in sense_terms for e in evidences)
        ]
        if score_by_id is not None:
            nids.sort(key=lambda nid: score_by_id.get(nid, 0.0), reverse=True)
        groups = rvk_groups_for_sense(sense, mapping)
        results.append(
            SenseResult(
                sense=sense,
                notation_ids=nids,
                hit_count=len(nids),
                matches_filter=bool(wanted & set(groups)),
                rvk_groups=groups,
            )
        )
    indexed = list(enumerate(results))
    indexed.sort(
        key=lambda item: (not item[1].matches_filter, -item[1].hit_count, item[0])
    )
    return [result for _, result in indexed]


def search(conn, embedding_service, query: str, expanded_terms: list[str]) -> list[Hit]:
    """Trefferliste ohne GND-Bedeutungen (Verhalten wie vor P3)."""
    return search_result(conn, embedding_service, query, expanded_terms).hits


def _empty_result(gnd_senses, subject_filter, mapping) -> SearchResult:
    return SearchResult(
        hits=[], senses=rank_senses(gnd_senses, {}, subject_filter, mapping)
    )


def search_result(
    conn,
    embedding_service,
    query: str,
    expanded_terms: list[str],
    gnd_senses: Iterable[GndSense] = (),
    subject_filter: Iterable[str] | None = None,
    mapping: Mapping[str, Iterable[str]] | None = None,
) -> SearchResult:
    """Trefferliste samt GND-Bedeutungen (P3). Ohne Bedeutungen identisch mit `search`."""
    gnd_senses = list(gnd_senses)
    if mapping is None:
        mapping = Config.GND_SUBJECT_CATEGORY_TO_RVK
    terms = build_expansion_terms(query, expanded_terms)
    retrieval = collect_evidence(conn, embedding_service, terms)
    collect_gnd_evidence(conn, retrieval, gnd_senses)

    keyword_ids = retrieval.keyword_ids
    vector_score_by_id = retrieval.input_vector_scores
    term_notation_ids = retrieval.register_ids

    # Kandidatenmenge in derselben Form wie vor P2 (siehe Kommentar in collect_evidence).
    # Treffer nur von Erweiterungsbegriffen werden danach in-place ergänzt; ohne Erweiterung
    # ändert das nichts an der Menge oder ihrer Reihenfolge.
    all_candidate_ids = keyword_ids | set(vector_score_by_id) | term_notation_ids
    if retrieval.expansion_vector_ids:
        all_candidate_ids |= retrieval.expansion_vector_ids
    if retrieval.gnd_relation_by_id:
        all_candidate_ids |= set(retrieval.gnd_relation_by_id)
    if not all_candidate_ids:
        return _empty_result(gnd_senses, subject_filter, mapping)

    notations = rvk_repository.get_notations_by_ids(conn, list(all_candidate_ids))
    candidate_ids = {
        nid
        for nid in all_candidate_ids
        if nid in notations and is_assignable_notation(notations[nid].notation)
    }
    if not candidate_ids:
        return _empty_result(gnd_senses, subject_filter, mapping)

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
        gnd_bonus = gnd_bonus_for(retrieval.gnd_relation_by_id.get(nid))
        score += gnd_bonus
        score_by_id[nid] = min(score, 1.0)
        components_by_id[nid] = {
            "cosine": cosine,
            "keyword_bonus": keyword_bonus,
            "register_bonus": register_bonus,
            "gnd_bonus": gnd_bonus,
        }

    ranked_ids = sorted(candidate_ids, key=lambda nid: score_by_id[nid], reverse=True)[
        :MAX_RESULTS
    ]

    hits = [
        Hit(
            notation=notations[nid],
            evidences=list(retrieval.evidence_by_id[nid]),
            score=score_by_id[nid],
            score_components=components_by_id[nid],
        )
        for nid in ranked_ids
    ]
    senses = rank_senses(
        gnd_senses,
        {nid: retrieval.evidence_by_id[nid] for nid in candidate_ids},
        subject_filter,
        mapping,
        score_by_id=score_by_id,
    )
    return SearchResult(hits=hits, senses=senses)


def senses_to_rows(
    conn, senses: list[SenseResult], notations_per_sense: int = 5
) -> list[dict]:
    """Zeilen für den Block „Mehrere Bedeutungen gefunden“ bzw. „GND-Begriff“."""
    wanted = [nid for r in senses for nid in r.notation_ids[:notations_per_sense]]
    notations = rvk_repository.get_notations_by_ids(conn, list(dict.fromkeys(wanted)))
    rows = []
    for result in senses:
        sense = result.sense
        rows.append(
            {
                "name": sense.display_name,
                "gnd_id": sense.gnd_id,
                "subject_categories": [
                    label or code for code, label in sense.subject_categories
                ],
                "matches_filter": result.matches_filter,
                "hit_count": result.hit_count,
                "notations": [
                    {
                        "notation": notations[nid].notation,
                        "label": notations[nid].label,
                        "rvk_online_url": build_rvk_online_url(notations[nid].notation),
                    }
                    for nid in result.notation_ids[:notations_per_sense]
                    if nid in notations
                ],
            }
        )
    return rows


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
