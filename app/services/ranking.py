"""Sucht pro Suchbegriff (Multi-Query) und fasst die Funde zu einer nach Relevanz sortierten
Trefferliste zusammen. Jeder Treffer (`Hit`) trägt seine Evidenzen: über welchen Begriff und
welchen Weg (Volltext, Vektor, Registerbegriff) er gefunden wurde.

Suchbegriffe sind die Eingabe und jeder Erweiterungsbegriff einzeln. Früher wurden sie zu einem
Text und einem Vektor verschmolzen; dann ließ sich kein Treffer einem Begriff zuordnen, und die
Erweiterung verwässerte die eigentliche Anfrage.

Ablauf (P5): `build_candidates` sammelt die Evidenzen und berechnet je Kandidat die Merkmale
(Cosine zur Eingabe, stärkste Evidenz je Pfadtyp, Bestandteile der alten Formel).
`score_candidates` bildet daraus mit der eingestellten Formel (app/services/scoring.py) Score und
Bestandteile und sortiert. Erweiterungsbegriffe und Registerwege erweitern die Kandidatenmenge;
wie stark sie zählen, bestimmen die Pfadgewichte in app/config.py.

Der Score ist ein Relevanzmaß, keine Wahrscheinlichkeit. Sortierung und Anzeige (Stufe und
Balken) beruhen auf derselben Zahl, damit die Reihenfolge nie der Anzeige widerspricht (eine
frühere Reciprocal-Rank-Fusion hatte genau dieses Problem).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from app.config import Config
from app.models import Evidence, ExpansionTerm, GndSense, Hit
from app.services import rvk_repository, scoring
from app.services.query_expansion import normalize_query

MAX_RESULTS = 20
# Zuschläge der Formel „legacy“ (Stand P4), seit P5 in app/services/scoring.py
KEYWORD_BONUS = scoring.KEYWORD_BONUS
TERM_MATCH_BONUS = scoring.TERM_MATCH_BONUS
GND_SYNONYM_BONUS = scoring.GND_SYNONYM_BONUS
GND_RELATION_BONUS = scoring.GND_RELATION_BONUS
LLM_VERIFIED_BONUS = scoring.LLM_VERIFIED_BONUS
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


def is_verified_llm_term(term) -> bool:
    return (
        isinstance(term, ExpansionTerm)
        and term.source == "llm"
        and term.relation == "llm_verified"
    )


def build_expansion_terms(
    query: str, llm_terms: Iterable[ExpansionTerm]
) -> list[ExpansionTerm]:
    """Suchbegriffe: zuerst die Eingabe, dann jeder geprüfte LLM-Begriff einzeln (P4).

    Gesucht werden nur `ExpansionTerm(source="llm", relation="llm_verified")`; alles andere
    (nicht geprüfte Vorschläge, bloße Texte) wird zur Sicherheit verworfen. Leere und (nach
    Normalisierung) doppelte Begriffe werden übersprungen.
    """
    terms = [ExpansionTerm(query, "input", "direct")]
    seen = {normalize_query(query)}
    for term in llm_terms or ():
        if not is_verified_llm_term(term):
            continue
        normalized = normalize_query(term.term or "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
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
    # P4: Notationen, die über einen geprüften LLM-Begriff exakt im Register gefunden wurden
    llm_ids: set[int] = field(default_factory=set)

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


def collect_llm_evidence(
    conn, retrieval: Retrieval, terms: Iterable[ExpansionTerm]
) -> None:
    """Ergänzt die Evidenzen um die exakten Wege der geprüften LLM-Begriffe (P4).

    1. LLM-Begriff -> exakt gleicher Registerbegriff -> Notation (`register_exact`, 1.0)
    2. LLM-Begriff -> GND-ID -> Registereintrag mit dieser GND-ID (`register_gnd_id`, 1.0)
    Kein Zuschlag in P4 (LLM_VERIFIED_BONUS = 0); die Funde erweitern nur die Kandidatenmenge.
    """
    llm_terms = [t for t in terms if is_verified_llm_term(t)]
    if not llm_terms:
        return

    def note(nid: int, term: ExpansionTerm, match_type: str) -> None:
        retrieval.add(Evidence(nid, term, match_type, 1.0))
        retrieval.llm_ids.add(nid)

    for term in llm_terms:
        for nid, _entry_type, _gnd_id in rvk_repository.get_notation_ids_for_term(
            conn, term.term, limit=GND_REGISTER_LIMIT
        ):
            note(nid, term, "register_exact")

    ids_by_gnd = rvk_repository.get_notations_by_gnd_ids(
        conn,
        [t.gnd_id for t in llm_terms if t.gnd_id],
        limit_per_id=GND_REGISTER_LIMIT,
    )
    for term in llm_terms:
        for nid in ids_by_gnd.get(term.gnd_id or "", []):
            note(nid, term, "register_gnd_id")


def gnd_bonus_for(relations: set[str] | None) -> float:
    """Höchster zutreffende GND-Zuschlag der Formel „legacy“; ohne GND-Evidenz 0."""
    return scoring.legacy_components(0.0, False, False, relations, False)["gnd_bonus"]


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


def search(
    conn, embedding_service, query: str, expanded_terms: list[ExpansionTerm]
) -> list[Hit]:
    """Trefferliste ohne GND-Bedeutungen (Verhalten wie vor P3).

    `expanded_terms` sind geprüfte LLM-Begriffe (siehe `build_expansion_terms`).
    """
    return search_result(conn, embedding_service, query, expanded_terms).hits


def _empty_result(gnd_senses, subject_filter, mapping) -> SearchResult:
    return SearchResult(
        hits=[], senses=rank_senses(gnd_senses, {}, subject_filter, mapping)
    )


@dataclass
class CandidateFeatures:
    """Alles, was der Score eines Kandidaten braucht – ohne Datenbank und ohne Modell.

    Damit lässt sich jede Formel offline neu bewerten (Abstimmung in scripts/tune_scoring.py),
    mit derselben Rangfolge wie in der Suche selbst.
    """

    nid: int
    notation: str
    cosine: float
    strengths: dict[str, float]
    legacy: dict[str, float]


@dataclass
class Candidates:
    retrieval: Retrieval
    notations: dict
    candidate_ids: set[int]
    # in der Iterationsreihenfolge von candidate_ids (bestimmt Gleichstände bei „legacy“)
    features: list[CandidateFeatures]


def build_candidates(
    conn,
    embedding_service,
    query: str,
    expanded_terms: list[ExpansionTerm],
    gnd_senses: Iterable[GndSense] = (),
) -> Candidates | None:
    """Sammelt die Evidenzen aller Suchwege und berechnet die Merkmale je Kandidat.

    Ohne zuweisbare Kandidaten: None.
    """
    terms = build_expansion_terms(query, expanded_terms)
    retrieval = collect_evidence(conn, embedding_service, terms)
    collect_gnd_evidence(conn, retrieval, gnd_senses)
    collect_llm_evidence(conn, retrieval, terms[1:])

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
    if retrieval.llm_ids:
        all_candidate_ids |= retrieval.llm_ids
    if not all_candidate_ids:
        return None

    notations = rvk_repository.get_notations_by_ids(conn, list(all_candidate_ids))
    candidate_ids = {
        nid
        for nid in all_candidate_ids
        if nid in notations and is_assignable_notation(notations[nid].notation)
    }
    if not candidate_ids:
        return None

    input_norm = normalize_query(query)
    features = []
    for nid in candidate_ids:
        cosine = vector_score_by_id.get(nid)
        if cosine is None:
            cosine = (
                embedding_service.score_for_notation(retrieval.query_vec, nid) or 0.0
            )
        features.append(
            CandidateFeatures(
                nid=nid,
                notation=notations[nid].notation,
                cosine=cosine,
                strengths=scoring.path_strengths(
                    retrieval.evidence_by_id[nid], input_norm
                ),
                legacy=scoring.legacy_components(
                    cosine,
                    nid in keyword_ids,
                    nid in term_notation_ids,
                    retrieval.gnd_relation_by_id.get(nid),
                    nid in retrieval.llm_ids,
                ),
            )
        )
    return Candidates(retrieval, notations, candidate_ids, features)


def score_candidates(
    features: list[CandidateFeatures], settings: scoring.ScoringSettings
) -> list[tuple[CandidateFeatures, float, dict[str, float]]]:
    """Score und Bestandteile je Kandidat, absteigend sortiert (alle Kandidaten).

    „legacy“: stabile Sortierung nur nach dem Score wie vor P5 (bitgleich). Neue Formeln: nach
    (Score, Cosine, Notation), damit die Rangfolge nicht von der Mengenreihenfolge abhängt.
    """
    scored = []
    for feature in features:
        if settings.formula == "legacy":
            components = dict(feature.legacy)
            score = scoring.legacy_score(components)
        else:
            score, components = scoring.combine(
                settings.formula, feature.strengths, feature.cosine, settings
            )
        scored.append((feature, score, components))
    if settings.formula == "legacy":
        scored.sort(key=lambda item: item[1], reverse=True)
    else:
        # Score auf 9 Stellen gerundet, damit Rundungsreste der Summenreihenfolge keine
        # Gleichstände auflösen (gleiche Regel wie scoring_tuning.FastScorer)
        scored.sort(
            key=lambda item: (-round(item[1], 9), -item[0].cosine, item[0].notation)
        )
    return scored


def search_result(
    conn,
    embedding_service,
    query: str,
    expanded_terms: list[ExpansionTerm],
    gnd_senses: Iterable[GndSense] = (),
    subject_filter: Iterable[str] | None = None,
    mapping: Mapping[str, Iterable[str]] | None = None,
    scoring_settings: scoring.ScoringSettings | None = None,
) -> SearchResult:
    """Trefferliste samt GND-Bedeutungen (P3) und geprüften LLM-Begriffen (P4).

    Ohne Bedeutungen identisch mit `search`. `scoring_settings` (P5) wählt Formel und Gewichte;
    Standard ist die Einstellung aus `Config`.
    """
    gnd_senses = list(gnd_senses)
    if mapping is None:
        mapping = Config.GND_SUBJECT_CATEGORY_TO_RVK
    if scoring_settings is None:
        scoring_settings = scoring.ScoringSettings.from_config(Config)
    candidates = build_candidates(
        conn, embedding_service, query, expanded_terms, gnd_senses
    )
    if candidates is None:
        return _empty_result(gnd_senses, subject_filter, mapping)

    # Der Score ist zugleich Sortierschlüssel und Grundlage der Anzeige (Stufe und Balken) – beide
    # müssen übereinstimmen, sonst widerspricht die Reihenfolge der Anzeige (Problem der früheren
    # RRF-Lösung). Er ist ein Relevanzmaß, keine Wahrscheinlichkeit.
    scored = score_candidates(candidates.features, scoring_settings)
    retrieval = candidates.retrieval
    hits = [
        Hit(
            notation=candidates.notations[feature.nid],
            evidences=list(retrieval.evidence_by_id[feature.nid]),
            score=score,
            score_components=components,
        )
        for feature, score, components in scored[:MAX_RESULTS]
    ]
    score_by_id = {feature.nid: score for feature, score, _ in scored}
    senses = rank_senses(
        gnd_senses,
        {nid: retrieval.evidence_by_id[nid] for nid in candidates.candidate_ids},
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


# Länge des Balkens beim ersten Vorschlag (CSS-Einheit em); alle anderen relativ dazu
SCORE_BAR_EM = 8.0


def score_bar(
    components: Mapping[str, float], max_score: float
) -> list[dict[str, object]]:
    """Balkensegmente je Bestandteil: Schlüssel, deutsches Label, Wert und Breite in em.

    Die Breite ist relativ zum höchsten Score der Liste und nur Gestaltung, kein angezeigter Text.
    Negative oder leere Bestandteile erscheinen nicht.
    """
    segments = []
    for key, value in components.items():
        if value <= 0:
            continue
        width = SCORE_BAR_EM * value / max_score if max_score > 0 else 0.0
        segments.append(
            {
                "key": key,
                "label": scoring.PATH_LABELS.get(key, key),
                "value": round(value, 2),
                "width": round(min(width, SCORE_BAR_EM), 2),
            }
        )
    return segments


def hits_to_rows(
    conn, hits: list[Hit], scoring_settings: scoring.ScoringSettings | None = None
) -> list[dict]:
    """Zeilen für die Ergebnistabelle (_results_table.html).

    Seit P5 statt Prozentwert: Stufe („hoch / mittel / niedrig“), Relevanzwert (nur im Tooltip)
    und die Bestandteile als Balkensegmente.
    """
    if scoring_settings is None:
        scoring_settings = scoring.ScoringSettings.from_config(Config)
    max_score = max((hit.score for hit in hits), default=0.0)
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
                "tier": scoring.tier_for(hit.score, scoring_settings),
                "score": round(hit.score, 2),
                "components": score_bar(hit.score_components, max_score),
                "rvk_online_url": build_rvk_online_url(notation.notation),
            }
        )
    return rows
