"""Aufbereitung der Suchergebnisse für die Oberfläche (P6): reine Funktionen, ohne Netz.

Hier entsteht, was die Oberfläche zur Erklärung eines Vorschlags zeigt:
- die Erklärungskette je Evidenz („Warum?“): Eingabe → GND-Begriff → … → Registereintrag an <Notation>,
- der Erweiterungsbaum („Ihre Suche wurde erweitert“) mit abwählbaren Begriffen,
- die Gruppierung nach RVK-Hauptgruppe und nach GND-Bedeutung.

Die Templates zeigen nur an; alle Entscheidungen fallen hier und sind ohne Datenbank testbar.
Nichts hier ändert die Rangfolge der Vorschläge.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote

from app.config import parse_main_groups_env
from app.models import Evidence, ExpansionTerm, GndSense, Hit, LlmSuggestion
from app.services import scoring
from app.services.query_expansion import normalize_query

LOBID_GND_BASE_URL = "https://lobid.org/gnd/"

# Höchstens so viele Ketten je Vorschlag sichtbar, der Rest als „+N weitere“
MAX_CHAINS = 5

_MAIN_GROUP_RE = re.compile(r"^[A-Z]+")


def main_group(notation: str) -> str:
    """Hauptgruppe = führende Buchstaben der Notation: "ZN 8330" -> "ZN"."""
    normalized = " ".join(str(notation).split()).upper()
    match = _MAIN_GROUP_RE.match(normalized)
    return match.group(0) if match else ""


def lobid_url(gnd_id: str | None) -> str | None:
    """Link auf den GND-Datensatz bei lobid-gnd, z. B. https://lobid.org/gnd/4123456-7."""
    if not gnd_id:
        return None
    return LOBID_GND_BASE_URL + quote(str(gnd_id).strip())


# --- Erklärungskette je Evidenz ------------------------------------------------------------


def _step(label: str, term: str | None = None, url: str | None = None) -> dict:
    return {"label": label, "term": term, "url": url}


def _register_step(match_type: str, notation: str) -> dict:
    if match_type == "register_gnd_id":
        return _step("Registereintrag mit GND-Nummer an", notation)
    return _step("Registereintrag an", notation)


def evidence_chain(
    evidence: Evidence,
    query: str,
    notation: str,
    sense_ids: Mapping[str, str] | None = None,
) -> list[dict]:
    """Schritte der Kette, über die `evidence` zur Notation führt.

    Jeder Schritt ist `{"label", "term", "url"}`; die Oberfläche setzt Pfeile dazwischen.
    `sense_ids` ordnet den Namen einer GND-Bedeutung ihrer GND-Nummer zu (für den lobid-Link).
    """
    via = evidence.via_term
    match_type = evidence.match_type
    steps = [_step("Eingabe", query)]
    input_norm = normalize_query(query)

    if via.source == "input":
        if match_type == "label_fts":
            steps.append(
                _step("Volltext-Treffer in Benennung oder Register von", notation)
            )
        else:
            steps.append(_step("inhaltlich ähnlich (Vektor) zu", notation))
        return steps

    if via.source == "gnd":
        sense_name = via.parent_term or via.term
        sense_id = (sense_ids or {}).get(sense_name)
        if sense_id is None and via.relation == "synonym":
            sense_id = via.gnd_id
        steps.append(_step("GND-Begriff", sense_name, lobid_url(sense_id)))
        path = scoring.GND_RELATION_PATHS.get(via.relation)
        if via.relation != "synonym" or normalize_query(via.term) != normalize_query(
            sense_name
        ):
            label = scoring.PATH_LABELS.get(path or "", "GND-Begriff")
            steps.append(_step(label, via.term, lobid_url(via.gnd_id)))
        steps.append(_register_step(match_type, notation))
        return steps

    if via.source == "llm":
        steps.append(
            _step(
                "Vorschlag des Sprachmodells (im Normvokabular verifiziert)",
                via.term,
                lobid_url(via.gnd_id),
            )
        )
        if match_type == "vector":
            steps.append(_step("inhaltlich ähnlich (Vektor) zu", notation))
        else:
            steps.append(_register_step(match_type, notation))
        return steps

    if via.source == "vector":
        parent = via.parent_term or query
        if normalize_query(parent) != input_norm:
            steps.append(
                _step(
                    "Vorschlag des Sprachmodells (im Normvokabular verifiziert)", parent
                )
            )
        if normalize_query(via.term) == normalize_query(parent):
            steps.append(_step("gleichlautender Registerbegriff", via.term))
        else:
            steps.append(_step("ähnlicher Registerbegriff (Ähnlichkeit)", via.term))
        steps.append(_register_step(match_type, notation))
        return steps

    steps.append(_step("gefunden an", notation))
    return steps


def is_similarity_only(evidence: Evidence, input_norm: str) -> bool:
    """Wahr, wenn die Evidenz nur auf inhaltlicher Ähnlichkeit (Embedding) beruht.

    Ein über Ähnlichkeit gefundener Registerbegriff, der genau der Eingabe entspricht, zählt als
    direkter Registertreffer (wie in `scoring.classify_evidence`).
    """
    if evidence.match_type == "vector":
        return True
    if evidence.via_term.source == "vector":
        classified = scoring.classify_evidence(evidence, input_norm)
        return classified is None or classified[0] != "register_exact"
    return False


def is_vector_only(evidences: Iterable[Evidence], query: str = "") -> bool:
    """Wahr, wenn jede Evidenz nur über Ähnlichkeit kommt (Warnhinweis am Vorschlag)."""
    evidences = list(evidences)
    input_norm = normalize_query(query)
    return bool(evidences) and all(is_similarity_only(e, input_norm) for e in evidences)


def explain_hit(
    hit: Hit, query: str, sense_ids: Mapping[str, str] | None = None
) -> dict:
    """Alle Erklärungsketten eines Vorschlags, ohne Dubletten, nach Fundweg geordnet.

    Liefert `{"chains", "shown", "more"}`; jede Kette trägt Pfadtyp, deutschen Namen und Schritte.
    """
    input_norm = normalize_query(query)
    order = {path: i for i, path in enumerate(scoring.PATH_TYPES)}
    seen: set[tuple] = set()
    chains = []
    for position, evidence in enumerate(hit.evidences):
        classified = scoring.classify_evidence(evidence, input_norm)
        path, strength = classified if classified else ("", 0.0)
        steps = evidence_chain(evidence, query, hit.notation.notation, sense_ids)
        key = tuple((s["label"], s["term"]) for s in steps)
        if key in seen:
            continue
        seen.add(key)
        chains.append(
            {
                "path": path,
                "path_label": scoring.PATH_LABELS.get(path, "sonstiger Fundweg"),
                "steps": steps,
                "_sort": (order.get(path, len(order)), -strength, position),
            }
        )
    chains.sort(key=lambda c: c.pop("_sort"))
    return {
        "chains": chains,
        "shown": chains[:MAX_CHAINS],
        "more": max(len(chains) - MAX_CHAINS, 0),
    }


# --- Gruppierung -------------------------------------------------------------------------------


def group_by_main_group(
    rows: Iterable[dict], labels: Mapping[str, str] | None = None
) -> list[dict]:
    """Vorschläge je RVK-Hauptgruppe, Gruppen nach dem besten Rang geordnet.

    Die Zeilen behalten ihren Rang über alle Vorschläge (Feld `rank`).
    """
    labels = labels or {}
    groups: dict[str, dict] = {}
    for row in rows:
        code = row.get("main_group") or main_group(row["notation"])
        group = groups.get(code)
        if group is None:
            group = groups[code] = {
                "code": code,
                "label": labels.get(code) or "",
                "count": 0,
                "rows": [],
            }
        group["rows"].append(row)
        group["count"] += 1
    return list(groups.values())


def assign_to_senses(
    rows: list[dict],
    sense_results: list,
    sense_rows: list[dict] | None = None,
    labels: Mapping[str, str] | None = None,
) -> list[dict]:
    """Ergebnisblöcke je GND-Bedeutung (nur bei mehr als einer Bedeutung, sonst leer).

    Jeder Vorschlag kommt in genau einen Block: den der höchstrangigen Bedeutung, über deren
    Begriffe er gefunden wurde. Der Rest kommt in „Über die Eingabe gefunden“. Aufgeklappt ist,
    was zum Fachfilter passt; passt keine Bedeutung, die erste in der Rangfolge.
    `sense_rows` (aus `ranking.senses_to_rows`, gleiche Reihenfolge) liefert Anzeigename,
    Sachgruppen und die ersten Stellen je Bedeutung.
    """
    if len(sense_results) <= 1:
        return []
    sense_rows = sense_rows or [{} for _ in sense_results]
    any_match = any(r.matches_filter for r in sense_results)
    blocks = []
    for index, (result, info) in enumerate(zip(sense_results, sense_rows, strict=True)):
        sense = result.sense
        blocks.append(
            {
                "id": f"sense-block-{index + 1}",
                "kind": "sense",
                "name": info.get("name") or sense.display_name,
                "gnd_id": sense.gnd_id,
                "url": lobid_url(sense.gnd_id),
                "subject_categories": info.get("subject_categories")
                or [label or code for code, label in sense.subject_categories],
                "hit_count": result.hit_count,
                "matches_filter": result.matches_filter,
                "open": result.matches_filter if any_match else index == 0,
                "ids": set(result.notation_ids),
                "candidates": info.get("notations", []),
                "rows": [],
            }
        )
    rest = {
        "id": "sense-block-rest",
        "kind": "rest",
        "name": "Über die Eingabe gefunden",
        "open": True,
        "rows": [],
        "candidates": [],
    }
    for row in rows:
        target = next(
            (b for b in blocks if row.get("notation_id") in b["ids"]),
            rest,
        )
        target["rows"].append(row)
    shown = {row["notation"] for row in rows}
    result_blocks = []
    for block in [*blocks, rest]:
        if block["kind"] == "rest" and not block["rows"]:
            continue
        block.pop("ids", None)
        block["count"] = len(block["rows"])
        block["groups"] = group_by_main_group(block["rows"], labels)
        # Stellen über diese Bedeutung, die nicht unter den angezeigten Vorschlägen sind
        block["more"] = [n for n in block["candidates"] if n["notation"] not in shown]
        result_blocks.append(block)
    return result_blocks


# --- Erweiterungsbaum und Abwahl ---------------------------------------------------------------

RELATION_GROUPS = (
    ("synonym", "Synonyme"),
    ("broader", "Oberbegriffe"),
    ("narrower", "Unterbegriffe"),
    ("related", "verwandte Begriffe"),
)

LLM_VERIFIED_LABELS = {
    "rvk_register": "verifiziert – gefunden im RVK-Register",
    "gnd": "verifiziert – gefunden in GND",
}
LLM_UNVERIFIED_LABEL = (
    "nicht verifiziert – nicht im Normvokabular gefunden – nicht gesucht"
)


def gnd_term_key(sense: GndSense, term: ExpansionTerm) -> str:
    """Stabiler Schlüssel eines GND-Suchbegriffs (Formularwert im Erweiterungsbaum)."""
    return f"gnd|{sense.gnd_id}|{term.relation}|{normalize_query(term.term)}"


def llm_term_key(term: str) -> str:
    """Stabiler Schlüssel eines geprüften LLM-Begriffs (Schreibweise im Normvokabular)."""
    return f"llm|{normalize_query(term)}"


def tree_keys(
    senses: Iterable[GndSense], suggestions: Iterable[LlmSuggestion]
) -> set[str]:
    """Alle abwählbaren Schlüssel; die Eingabe selbst ist nicht abwählbar."""
    keys = {gnd_term_key(s, t) for s in senses for t in s.terms}
    keys |= {
        llm_term_key(s.matched_term)
        for s in suggestions
        if s.verified and s.matched_term
    }
    return keys


def expansion_tree(
    query: str,
    senses: Iterable[GndSense],
    suggestions: Iterable[LlmSuggestion],
    excluded: Iterable[str] = (),
) -> dict:
    """Baum „Ihre Suche wurde erweitert“: Eingabe → GND-Begriffe → Beziehungen, dazu LLM-Vorschläge.

    `senses` und `suggestions` sind die ungefilterten Erweiterungen; `excluded` die abgewählten
    Schlüssel (deren Häkchen fehlt).
    """
    excluded = set(excluded)
    sense_nodes = []
    for sense in senses:
        own = None
        groups: dict[str, list[dict]] = {rel: [] for rel, _ in RELATION_GROUPS}
        for term in sense.terms:
            key = gnd_term_key(sense, term)
            node = {
                "key": key,
                "term": term.term,
                "url": lobid_url(term.gnd_id) if term.relation != "synonym" else None,
                "checked": key not in excluded,
            }
            if (
                own is None
                and term.relation == "synonym"
                and normalize_query(term.term) == normalize_query(sense.display_name)
            ):
                own = node
                continue
            groups.setdefault(term.relation, []).append(node)
        sense_nodes.append(
            {
                "name": sense.display_name,
                "gnd_id": sense.gnd_id,
                "url": lobid_url(sense.gnd_id),
                "subject_categories": [
                    label or code for code, label in sense.subject_categories
                ],
                "own": own,
                "groups": [
                    {"relation": rel, "label": label, "terms": groups[rel]}
                    for rel, label in RELATION_GROUPS
                    if groups.get(rel)
                ],
            }
        )
    llm_nodes = []
    for s in suggestions:
        node = {
            "term": s.term,
            "matched_term": s.matched_term,
            "relation": s.llm_relation,
            "rationale": s.rationale,
            "verified": s.verified,
            "key": None,
            "checked": False,
        }
        if s.verified and s.matched_term:
            node["key"] = llm_term_key(s.matched_term)
            node["checked"] = node["key"] not in excluded
            node["status"] = LLM_VERIFIED_LABELS.get(
                s.verified_via or "", "verifiziert"
            )
        else:
            node["status"] = LLM_UNVERIFIED_LABEL
        llm_nodes.append(node)
    return {
        "query": query,
        "senses": sense_nodes,
        "llm": llm_nodes,
        "has_expansion": bool(sense_nodes or llm_nodes),
    }


def apply_exclusions(
    senses: Iterable[GndSense],
    llm_terms: Iterable[ExpansionTerm],
    excluded: Iterable[str],
) -> tuple[list[GndSense], list[ExpansionTerm]]:
    """Bedeutungen und LLM-Begriffe ohne die abgewählten Begriffe (die Eingabe bleibt immer)."""
    excluded = set(excluded)
    filtered_senses = [
        dataclasses.replace(
            sense,
            terms=tuple(
                t for t in sense.terms if gnd_term_key(sense, t) not in excluded
            ),
        )
        for sense in senses
    ]
    filtered_llm = [t for t in llm_terms if llm_term_key(t.term) not in excluded]
    return filtered_senses, filtered_llm


def main_group_options(
    groups: Iterable[tuple[str, str]], selected: Iterable[str]
) -> dict:
    """Auswahl „Hauptgruppen einschränken“: gewählte zuerst, dann nach Anfangsbuchstaben.

    Gewählte Codes, die in `groups` fehlen (z. B. Voreinstellung aus .env), erscheinen trotzdem.
    """
    selected = list(selected)
    labels = dict(groups)
    chosen = [
        {"code": code, "label": labels.get(code, ""), "checked": True}
        for code in selected
    ]
    letters: dict[str, list[dict]] = {}
    for code, label in groups:
        if code in selected:
            continue
        letters.setdefault(code[:1], []).append(
            {"code": code, "label": label, "checked": False}
        )
    return {
        "selected": chosen,
        "letters": [
            {"letter": letter, "groups": items}
            for letter, items in sorted(letters.items())
        ],
    }


def parse_main_groups(values: Iterable[str]) -> list[str]:
    """Hauptgruppen aus Formularwerten („ZL“, „zl, zm“) in Großbuchstaben, ohne Dubletten."""
    return list(parse_main_groups_env(",".join(str(v or "") for v in values)))
