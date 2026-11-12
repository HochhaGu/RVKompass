"""LLM-Erweiterung, geprüft (P4): Das Sprachmodell schlägt vor, das Normvokabular entscheidet.

Ablauf je Anfrage:
1. `query_expansion.suggest_terms` holt die Rohvorschläge (Begriff, Beziehung, Begründung),
   gecacht und mit Tageslimit.
2. `verify_suggestions` prüft jeden Vorschlag: zuerst exakt bzw. normalisiert im RVK-Register,
   sonst über lobid-gnd (Vorzugsname oder Namensvariante genau gleich). Das Prüfergebnis wird
   nicht gecacht, damit es nach einer Registeraktualisierung neu berechnet wird.
3. Nur geprüfte Vorschläge werden über `verified_terms` zu Suchbegriffen
   (`ExpansionTerm(source="llm", relation="llm_verified")`). Nicht geprüfte werden nur angezeigt.

Jeder Fehler (Sprachmodell, lobid) führt zu „keine Erweiterung“ bzw. „nicht geprüft“, nie zu
einer Ausnahme.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from app.models import ExpansionTerm, LlmSuggestion
from app.services import query_expansion, rvk_repository
from app.services.expansion import gnd
from app.services.query_expansion import normalize_query

logger = logging.getLogger(__name__)


def _setting(config, key: str, default=None):
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def _capitalize_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def register_variants(term: str) -> list[str]:
    """Schreibweisen, unter denen ein Vorschlag im Register stehen kann, in Vorzugsreihenfolge:
    wie geliefert, Leerraum zusammengefasst, ohne Zusatz in spitzen Klammern, jeweils auch mit
    großem Anfangsbuchstaben, ganz klein und klein mit großem Anfangsbuchstaben."""
    raw = (term or "").strip()
    collapsed = " ".join(raw.split())
    base, _qualifier = gnd.split_qualifier(collapsed)
    variants: list[str] = []
    for form in (raw, collapsed, base):
        lower = form.lower()
        for candidate in (
            form,
            _capitalize_first(form),
            lower,
            _capitalize_first(lower),
        ):
            if candidate and candidate not in variants:
                variants.append(candidate)
    return variants


def register_match(conn, term: str) -> tuple[str, str | None] | None:
    """(Registerbegriff, GND-ID) des besten Treffers im RVK-Register, sonst None."""
    variants = register_variants(term)
    rows = rvk_repository.find_register_terms(conn, variants)
    if not rows:
        return None
    order = {v: i for i, v in enumerate(variants)}
    # beste Schreibweise zuerst; bei gleicher Schreibweise einen Eintrag mit GND-ID vorziehen
    rows.sort(key=lambda row: (order.get(row[0], len(order)), row[1] is None))
    return rows[0]


def gnd_match(source, term: str, size: int = 5) -> tuple[str, str] | None:
    """(Vorzugsname, GND-ID) eines GND-Schlagworts, dessen Name oder Variante genau passt."""
    base, _ = gnd.split_qualifier(" ".join((term or "").split()))
    normalized = normalize_query(base)
    if not normalized or source is None:
        return None
    try:
        records = source.search_subject_headings(base, size)
        for record in records:
            if gnd.matches_query(record, normalized):
                return record.preferred_name, record.gnd_id
    except Exception as exc:  # noqa: BLE001 – Ausfall der Quelle heißt: nicht geprüft
        logger.warning("GND-Prüfung für %r fehlgeschlagen: %s", term, exc)
    return None


def verify_suggestions(
    conn, raw: Iterable[dict], source=None, gnd_size: int = 5
) -> list[LlmSuggestion]:
    """Prüft jeden Rohvorschlag gegen das Register und, falls dort nicht gefunden, die GND."""
    suggestions: list[LlmSuggestion] = []
    for item in raw:
        term = item["begriff"]
        relation = item["beziehung"]
        rationale = item.get("begruendung", "")
        verified_via = matched = gnd_id = None
        try:
            found = register_match(conn, term)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Registerprüfung für %r fehlgeschlagen: %s", term, exc)
            found = None
        if found is not None:
            matched, gnd_id = found
            verified_via = "rvk_register"
        else:
            found_gnd = gnd_match(source, term, gnd_size)
            if found_gnd is not None:
                matched, gnd_id = found_gnd
                verified_via = "gnd"
        suggestions.append(
            LlmSuggestion(
                term=term,
                llm_relation=relation,
                rationale=rationale,
                verified=verified_via is not None,
                verified_via=verified_via,
                matched_term=matched,
                gnd_id=gnd_id or None,
            )
        )
    return suggestions


def verified_terms(
    suggestions: Iterable[LlmSuggestion], query: str
) -> list[ExpansionTerm]:
    """Nur die geprüften Vorschläge als Suchbegriffe, unter ihrer Schreibweise im Normvokabular."""
    return [
        ExpansionTerm(s.matched_term, "llm", "llm_verified", s.gnd_id, query)
        for s in suggestions
        if s.verified and s.matched_term
    ]


def llm_suggestions_for_query(
    conn,
    config,
    query: str,
    source=None,
    *,
    client_factory=None,
    stats: dict | None = None,
) -> list[LlmSuggestion]:
    """Einstiegspunkt für Suchroute und Evaluation; `config` ist Flask-Config oder Config-Klasse.

    Leer, wenn die Erweiterung ausgeschaltet ist oder kein Schlüssel vorliegt. Ohne mitgegebene
    `source` wird die GND nur befragt, wenn die GND-Erweiterung eingeschaltet ist; sonst prüft
    nur das Register.
    """
    if not _setting(config, "ENABLE_QUERY_EXPANSION", False):
        return []
    api_key = _setting(config, "CLAUDE_API_KEY", "")
    if not api_key:
        return []
    try:
        raw = query_expansion.suggest_terms(
            conn,
            api_key,
            query,
            model=_setting(config, "CLAUDE_MODEL", query_expansion.DEFAULT_MODEL),
            daily_limit=_setting(
                config, "CLAUDE_DAILY_LIMIT", query_expansion.DEFAULT_DAILY_LIMIT
            ),
            timeout=_setting(config, "CLAUDE_TIMEOUT_SECONDS", 10),
            max_retries=_setting(config, "CLAUDE_MAX_RETRIES", 1),
            effort=_setting(config, "CLAUDE_EFFORT", "low"),
            max_items=_setting(
                config,
                "CLAUDE_MAX_SUGGESTIONS",
                query_expansion.DEFAULT_MAX_SUGGESTIONS,
            ),
            client_factory=client_factory,
            stats=stats,
        )
        if not raw:
            return []
        if source is None and _setting(config, "ENABLE_GND_EXPANSION", False):
            source = gnd.build_gnd_source(conn, config)
        return verify_suggestions(
            conn, raw, source, gnd_size=_setting(config, "LLM_VERIFY_GND_SIZE", 5)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM-Erweiterung abgeschaltet wegen Fehler: %s", exc)
        return []


def suggestions_to_rows(suggestions: Iterable[LlmSuggestion]) -> list[dict]:
    """Zeilen für den Block „Vorschläge des Sprachmodells“ in der Oberfläche."""
    labels = {"rvk_register": "gefunden im RVK-Register", "gnd": "gefunden in GND"}
    return [
        {
            "term": s.term,
            "matched_term": s.matched_term,
            "relation": s.llm_relation,
            "rationale": s.rationale,
            "verified": s.verified,
            "verified_via": s.verified_via,
            "verified_label": labels.get(s.verified_via or "", ""),
        }
        for s in suggestions
    ]
