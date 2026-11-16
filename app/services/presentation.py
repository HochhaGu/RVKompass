"""Aufbereitung der Suchergebnisse für die Oberfläche (P6): reine Funktionen, ohne Netz.

Hier entsteht, was die Oberfläche zur Erklärung eines Vorschlags zeigt:
- die Erklärungskette je Evidenz („Warum?“): Eingabe → GND-Begriff → … → Registereintrag an <Notation>,
- die Chip-Zeile der Sucherweiterung mit abwählbaren und wiederherstellbaren Begriffen (P10),
- die Gruppierung nach RVK-Hauptgruppe und nach GND-Bedeutung (Tabs, P10),
- Relevanzstufen, Verweis-Links und der Zustand ohne Vorschläge (P10).

Die Templates zeigen nur an; alle Entscheidungen fallen hier und sind ohne Datenbank testbar.
Nichts hier ändert die Rangfolge der Vorschläge.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote

from markupsafe import Markup, escape

from app.config import parse_main_groups_env
from app.models import (
    Evidence,
    ExpansionTerm,
    GndSense,
    Hit,
    LlmSuggestion,
    Notation,
)
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


def sense_tab_label(sense: GndSense) -> str:
    """Beschriftung eines Bedeutungs-Tabs: „Mutter (Technik)“ statt „Mutter <Technik>“."""
    if sense.qualifier:
        return f"{sense.preferred_name} ({sense.qualifier})"
    return sense.display_name


def assign_to_senses(
    rows: list[dict],
    sense_results: list,
    sense_rows: list[dict] | None = None,
    labels: Mapping[str, str] | None = None,
) -> list[dict]:
    """Ergebnisblöcke je GND-Bedeutung (nur bei mehr als einer Bedeutung, sonst leer).

    Jeder Vorschlag kommt in genau einen Block: den der höchstrangigen Bedeutung, über deren
    Begriffe er gefunden wurde. Der Rest kommt in „Über die Eingabe gefunden“. Gewählt
    (`selected`, P10: der sichtbare Tab) ist genau ein Block: die erste Bedeutung, die zum
    Fachfilter passt; passt keine, die erste in der Rangfolge.
    `sense_rows` (aus `ranking.senses_to_rows`, gleiche Reihenfolge) liefert Anzeigename,
    Sachgruppen und die ersten Stellen je Bedeutung.
    """
    if len(sense_results) <= 1:
        return []
    sense_rows = sense_rows or [{} for _ in sense_results]
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
                "tab_label": sense_tab_label(sense),
                "hit_count": result.hit_count,
                "matches_filter": result.matches_filter,
                "ids": set(result.notation_ids),
                "candidates": info.get("notations", []),
                "rows": [],
            }
        )
    # P10: genau ein Tab gewählt – die erste Bedeutung, die zum Fachfilter passt, sonst die erste
    chosen = next((b for b in blocks if b["matches_filter"]), blocks[0])
    for block in blocks:
        block["selected"] = block is chosen
    rest = {
        "id": "sense-block-rest",
        "kind": "rest",
        "name": "Über die Eingabe gefunden",
        "tab_label": "Über die Eingabe gefunden",
        "selected": False,
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


# --- Sucherweiterung als Chips und Abwahl ------------------------------------------------------

# P10: Typ eines Chips (Einzahl, wie in docs/design.md Abschnitt 5)
RELATION_LABELS = {
    "synonym": "Synonym",
    "broader": "Oberbegriff",
    "narrower": "Unterbegriff",
    "related": "verwandt",
}

# Beziehung, die das Sprachmodell zu seinem Vorschlag angibt
LLM_RELATION_LABELS = {
    "synonym": "Synonym",
    "oberbegriff": "Oberbegriff",
    "unterbegriff": "Unterbegriff",
    "verwandt": "verwandt",
    "fachkontext": "Fachkontext",
}

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


def _llm_node(suggestion: LlmSuggestion, excluded: set[str]) -> dict:
    """Ein LLM-Vorschlag mit Prüfstatus als Text (geprüft wird gesucht, sonst nur angezeigt)."""
    node = {
        "term": suggestion.term,
        "matched_term": suggestion.matched_term,
        "relation": suggestion.llm_relation,
        "rationale": suggestion.rationale,
        "verified": suggestion.verified,
        "key": None,
        "checked": False,
    }
    if suggestion.verified and suggestion.matched_term:
        node["key"] = llm_term_key(suggestion.matched_term)
        node["checked"] = node["key"] not in excluded
        node["status"] = LLM_VERIFIED_LABELS.get(
            suggestion.verified_via or "", "verifiziert"
        )
    else:
        node["status"] = LLM_UNVERIFIED_LABEL
    return node


def expansion_chips(
    query: str,
    senses: Iterable[GndSense],
    suggestions: Iterable[LlmSuggestion],
    excluded: Iterable[str] = (),
) -> dict:
    """Chip-Zeile unter dem Suchfeld (P10): je Erweiterungsbegriff eine abwählbare Pille.

    `senses` und `suggestions` sind die ungefilterten Erweiterungen, `excluded` die abgewählten
    Schlüssel. Liefert:
    - `groups`: `[{"sense_name", "chips"}]`, bei mehreren Bedeutungen je Bedeutung benannt,
    - `removed`: abgewählte Begriffe (einzeln wiederherstellbar),
    - `llm`: alle LLM-Vorschläge mit Prüfstatus, `unverified`: die nicht gesuchten davon,
    - `active_keys`: Schlüssel der gesuchten Begriffe (versteckte Formularfelder).
    Jeder Chip trägt `key`, `term`, `type_label`, `origin` (gnd/llm), `origin_label` und `url`.
    """
    excluded = set(excluded)
    senses = list(senses)
    suggestions = list(suggestions)
    named = len(senses) > 1
    groups: list[dict] = []
    removed: list[dict] = []
    active_keys: list[str] = []

    def place(chip: dict, bucket: list[dict]) -> None:
        if chip["key"] in excluded:
            removed.append(chip)
        else:
            bucket.append(chip)
            active_keys.append(chip["key"])

    for sense in senses:
        chips: list[dict] = []
        for term in sense.terms:
            chip = {
                "key": gnd_term_key(sense, term),
                "term": term.term,
                "type_label": RELATION_LABELS.get(term.relation, "verwandt"),
                "origin": "gnd",
                "origin_label": "GND",
                "url": lobid_url(term.gnd_id),
                "sense_name": sense.display_name,
            }
            place(chip, chips)
        if chips:
            groups.append(
                {"sense_name": sense.display_name if named else None, "chips": chips}
            )

    llm_nodes = [_llm_node(s, excluded) for s in suggestions]
    llm_chips: list[dict] = []
    for node in llm_nodes:
        if not node["key"]:
            continue
        chip = {
            "key": node["key"],
            "term": node["matched_term"],
            "type_label": LLM_RELATION_LABELS.get(node["relation"], "verwandt"),
            "origin": "llm",
            "origin_label": "KI, geprüft",
            "url": None,
            "sense_name": None,
            "status": node["status"],
            "rationale": node["rationale"],
        }
        place(chip, llm_chips)
    if llm_chips:
        groups.append(
            {
                "sense_name": "Sprachmodell, geprüft" if named else None,
                "chips": llm_chips,
            }
        )
    return {
        "query": query,
        "groups": groups,
        "removed": removed,
        "llm": llm_nodes,
        "unverified": [n for n in llm_nodes if not n["key"]],
        "active_keys": active_keys,
        "has_expansion": bool(groups or removed or llm_nodes),
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
    groups: Iterable[tuple[str, str]],
    selected: Iterable[str],
    counts: Mapping[str, int] | None = None,
) -> dict:
    """Facette „Hauptgruppen“: gewählte zuerst, dann nach Anfangsbuchstaben.

    Gewählte Codes, die in `groups` fehlen (z. B. Voreinstellung aus .env), erscheinen trotzdem.
    `counts` (P10) sind die Fundstellen je Hauptgruppe der letzten Suche, ungefiltert; vor der
    ersten Suche `None` – dann tragen die Einträge keine Zahl (`count` None).
    """
    groups = list(groups)
    selected = list(selected)
    labels = dict(groups)

    def entry(code: str, label: str, checked: bool) -> dict:
        count = None if counts is None else int(counts.get(code, 0))
        return {
            "code": code,
            "label": label,
            "checked": checked,
            "count": count,
            "has_hits": count is None or count > 0,
        }

    chosen = [entry(code, labels.get(code, ""), True) for code in selected]
    letters: dict[str, list[dict]] = {}
    for code, label in groups:
        if code in selected:
            continue
        letters.setdefault(code[:1], []).append(entry(code, label, False))
    return {
        "selected": chosen,
        "letters": [
            {"letter": letter, "groups": items}
            for letter, items in sorted(letters.items())
        ],
        "has_counts": counts is not None,
    }


def parse_main_groups(values: Iterable[str]) -> list[str]:
    """Hauptgruppen aus Formularwerten („ZL“, „zl, zm“) in Großbuchstaben, ohne Dubletten."""
    return list(parse_main_groups_env(",".join(str(v or "") for v in values)))


# --- P10: Verweise, Relevanzstufen, Zustände ohne Treffer ------------------------------------

_NOTATION = r"[A-Z]{1,3} \d{1,6}(?:\.\d+)?"
# Einzelne Notation („ST 301“) oder Bereich („ST 300 - ST 310“, auch mit Gedankenstrich)
NOTATION_REF_RE = re.compile(
    rf"\b(?P<first>{_NOTATION})(?:\s*[-–]\s*(?P<last>{_NOTATION}))?\b"
)
_NOTATION_QUERY_RE = re.compile(
    rf"(?P<first>{_NOTATION})(?:\s*[-–]\s*(?P<last>{_NOTATION}))?"
)


def _notation_code(match: re.Match) -> str:
    """Schreibweise wie in der RVK: Bereiche mit „ - “ (Bindestrich, Leerzeichen)."""
    if match.group("last"):
        return f"{match.group('first')} - {match.group('last')}"
    return match.group("first")


def link_notations(text: str | None) -> Markup:
    """RVK-Bemerkung oder Verweis mit Notationen als Such-Links („→ ST 301 anzeigen“).

    Der Text wird vollständig escaped; nur die erkannten Notationen werden zu Links auf
    `/?q=<Notation>` (ohne JavaScript) mit `data-query` (mit JavaScript: Suche im Formular).
    Ein Bereich wird ein Link. Der übrige Text bleibt unverändert. Leer/None: leeres Markup.
    """
    if not text:
        return Markup("")
    parts: list[str] = []
    position = 0
    for match in NOTATION_REF_RE.finditer(text):
        parts.append(str(escape(text[position : match.start()])))
        code = _notation_code(match)
        parts.append(
            str(
                Markup(
                    '<a class="notation-link" href="/?q={href}" data-query="{code}">'
                    "→ {code} anzeigen</a>"
                ).format(href=quote(code, safe=""), code=code)
            )
        )
        position = match.end()
    parts.append(str(escape(text[position:])))
    return Markup("".join(parts))


def parse_notation_query(query: str | None) -> str | None:
    """Ist die Eingabe genau eine Notation oder ein Bereich? Dann deren RVK-Schreibweise.

    „zl  4620“ -> "ZL 4620", „ST 300–ST 310“ -> "ST 300 - ST 310"; sonst None.
    """
    normalized = " ".join(str(query or "").split()).upper()
    match = _NOTATION_QUERY_RE.fullmatch(normalized)
    return _notation_code(match) if match else None


RELEVANCE_WORDS = ("gering", "schwach", "mittel", "hoch", "sehr hoch")


def relevance_level(score: float, settings: scoring.ScoringSettings) -> tuple[int, str]:
    """Fünf Relevanzstufen für die Treffer-Karte: (Stufe 1–5, Stufenwort).

    „mittel“ und „hoch“ beginnen an den kalibrierten Schwellen `tier_medium`/`tier_high`.
    Die äußeren Stufen (NICHT kalibriert) liegen je einen Abstand d = tier_high − tier_medium
    darüber („sehr hoch“) bzw. darunter („schwach“); noch tiefer ist „gering“.
    Ein Relevanzmaß, keine Wahrscheinlichkeit; die Rangfolge ändert sich dadurch nicht.
    """
    high, medium = settings.tier_high, settings.tier_medium
    step = high - medium
    bounds = (round(medium - step, 9), medium, high, round(high + step, 9))
    level = 1 + sum(score >= bound for bound in bounds)
    return level, RELEVANCE_WORDS[level - 1]


def empty_state(
    query: str,
    selected: Iterable[str],
    counts: Mapping[str, int] | None,
    removed_count: int = 0,
) -> dict:
    """Zustand „keine Vorschläge“: Ursache und Auswege (docs/design.md Abschnitt 7).

    Ursache `filter`: Es gäbe Fundstellen, aber keine in den gewählten Hauptgruppen; dann nennt
    `reset_count` die Zahl, die ein Zurücksetzen der Auswahl zeigen würde. Sonst `nothing`.
    """
    selected = list(selected)
    total = sum((counts or {}).values())
    cause = "filter" if selected and total > 0 else "nothing"
    return {
        "query": query,
        "cause": cause,
        "selected": selected,
        "reset_count": total,
        "removed_count": int(removed_count),
    }


def entered_notation(notation: Notation, rvk_online_url: str) -> dict:
    """Karte „Eingegebene Notation“: die eingetippte Stelle selbst, getrennt von den Vorschlägen.

    Herkunft ist die Eingabe (Direkt); die Rangfolge der Vorschläge bleibt davon unberührt.
    """
    return {
        "notation": notation.notation,
        "label": notation.label,
        "hierarchy_path": notation.hierarchy_path,
        "note_html": link_notations(notation.note),
        "see_also_html": link_notations(notation.see_also),
        "has_note": bool(notation.note),
        "rvk_online_url": rvk_online_url,
        "origin": "Direkt: Notation eingegeben",
    }
