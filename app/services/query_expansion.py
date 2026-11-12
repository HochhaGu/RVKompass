"""Begriffsvorschläge des Sprachmodells (Anthropic Claude) zu einem Suchbegriff (P4).

Das Sprachmodell schlägt nur vor, es entscheidet nicht: Ob ein Vorschlag gesucht wird, prüft
`app.services.expansion.llm` gegen RVK-Register und GND. Dieser Baustein liefert nur die
Rohvorschläge (Begriff, Beziehung, Begründung).

- Strukturierte Ausgabe über ein JSON-Schema (`output_config.format`), dazu ein robuster Leser,
  falls die Antwort trotzdem anders aussieht.
- Zwischenspeicher je normalisierter Anfrage in `expansion_cache`, Schlüssel mit Präfix „p4|“.
  Einträge aus der Zeit vor P4 (reine Begriffslisten) werden so übergangen, nicht überschrieben.
  Im Cache stehen nur die Vorschläge des Sprachmodells, nicht das Prüfergebnis: Das wird bei
  jeder Suche neu gegen das aktuelle Register berechnet.
- Tageslimit über `daily_usage`. Jeder Fehler ergibt eine leere Liste („keine Erweiterung“),
  Fehler werden nicht gespeichert. Der API-Schlüssel erscheint nie im Log.
"""

from __future__ import annotations

import inspect
import json
import logging
from functools import lru_cache

from app.models import LLM_RELATIONS
from app.services import rvk_repository

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_DAILY_LIMIT = 500
DEFAULT_MAX_SUGGESTIONS = 8
MAX_RATIONALE_WORDS = 12
MAX_TOKENS = 1024
CACHE_PREFIX = "p4|"

# Reihenfolge fest, damit Prompt und Schema stabil bleiben
RELATION_VALUES = (
    "synonym",
    "oberbegriff",
    "unterbegriff",
    "verwandt",
    "fachkontext",
)

CONTEXT_SENTENCE = "Bibliothek mit technischem Schwerpunkt, Nutzer ist fachfremd."

PROMPT_TEMPLATE = """Du hilfst bei der bibliothekarischen Sacherschließung mit der \
Regensburger Verbundklassifikation (RVK).
Kontext: {context}

Schlage zum folgenden Suchbegriff 3 bis {max_items} deutsche Fachbegriffe vor, die bei der Suche \
nach einer passenden RVK-Systemstelle helfen. Nenne die Begriffe in der Form, in der sie als \
Schlagwort in der Gemeinsamen Normdatei (GND) stehen würden: Einzahl, ohne Erklärsätze, ohne \
Artikel.

Suchbegriff: "{query}"

Gib für jeden Begriff an:
- "begriff": der Fachbegriff
- "beziehung": genau einer der Werte {relations}
- "begruendung": warum der Begriff passt, höchstens 12 Wörter

Antworte ausschließlich mit JSON in dieser Form:
{{"begriffe": [{{"begriff": "...", "beziehung": "synonym", "begruendung": "..."}}]}}"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "begriffe": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "begriff": {"type": "string"},
                    "beziehung": {"type": "string", "enum": list(RELATION_VALUES)},
                    "begruendung": {"type": "string"},
                },
                "required": ["begriff", "beziehung", "begruendung"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["begriffe"],
    "additionalProperties": False,
}


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def build_prompt(query: str, max_items: int = DEFAULT_MAX_SUGGESTIONS) -> str:
    return PROMPT_TEMPLATE.format(
        context=CONTEXT_SENTENCE,
        max_items=max_items,
        query=query.strip(),
        relations=", ".join(RELATION_VALUES),
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def parse_suggestions(
    text: str, query: str = "", max_items: int = DEFAULT_MAX_SUGGESTIONS
) -> list[dict]:
    """Liest die Vorschläge aus der Antwort des Sprachmodells.

    Nimmt `{"begriffe": [...]}` und eine bloße Liste an. Verwirft Einträge ohne Begriff oder mit
    unbekannter Beziehung, Dubletten und den Suchbegriff selbst; kürzt die Begründung auf
    12 Wörter. Kaputtes JSON löst `ValueError`, eine falsche Struktur `TypeError` aus (der
    Aufrufer wertet beides als Fehler).
    """
    data = json.loads(_strip_code_fence(text or ""))
    if isinstance(data, dict):
        data = data.get("begriffe")
    if not isinstance(data, list):
        raise TypeError("Erwartet: Liste von Begriffen")

    seen = {normalize_query(query or "")} - {""}
    suggestions: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        term = " ".join(str(item.get("begriff") or "").split())
        relation = str(item.get("beziehung") or "").strip().lower()
        if not term or relation not in LLM_RELATIONS:
            continue
        normalized = normalize_query(term)
        if normalized in seen:
            continue
        seen.add(normalized)
        words = str(item.get("begruendung") or "").split()
        suggestions.append(
            {
                "begriff": term,
                "beziehung": relation,
                "begruendung": " ".join(words[:MAX_RATIONALE_WORDS]),
            }
        )
        if len(suggestions) >= max_items:
            break
    return suggestions


def response_text(response) -> str:
    """Text aus dem ersten Block vom Typ „text“ (davor können Denk-Blöcke stehen)."""
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    raise ValueError("Antwort ohne Textblock")


@lru_cache(maxsize=1)
def sdk_supports_output_config() -> bool:
    """Ob die installierte anthropic-Bibliothek `output_config` bei messages.create kennt."""
    try:
        from anthropic.resources.messages import Messages

        return "output_config" in inspect.signature(Messages.create).parameters
    except Exception:  # noqa: BLE001
        return False


def sdk_version() -> str | None:
    try:
        import anthropic

        return str(anthropic.__version__)
    except Exception:  # noqa: BLE001
        return None


def _default_client_factory(api_key: str, timeout: float, max_retries: int):
    from anthropic import Anthropic

    return Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)


def _count(stats: dict | None, key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def suggest_terms(
    conn,
    api_key: str,
    query: str,
    *,
    model: str = DEFAULT_MODEL,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    timeout: float = 10,
    max_retries: int = 1,
    effort: str | None = "low",
    max_items: int = DEFAULT_MAX_SUGGESTIONS,
    client_factory=None,
    use_output_config: bool | None = None,
    stats: dict | None = None,
) -> list[dict]:
    """Rohvorschläge des Sprachmodells zu `query` als Liste von Dicts mit den Schlüsseln
    „begriff“, „beziehung“, „begruendung“. Leer bei Fehler, ohne Schlüssel oder über dem
    Tageslimit. `stats` (optional) zählt cache_hits, api_calls, api_errors, limit_reached.
    """
    normalized = normalize_query(query or "")
    if not normalized:
        return []
    cache_key = CACHE_PREFIX + normalized

    cached = rvk_repository.get_expansion_cache(conn, cache_key)
    if cached is not None:
        try:
            suggestions = parse_suggestions(cached, query, max_items)
            _count(stats, "cache_hits")
            return suggestions
        except (ValueError, TypeError):
            logger.warning(
                "Unlesbarer LLM-Cache-Eintrag für %r, wird neu geholt", query
            )

    if not api_key:
        return []

    call_count = rvk_repository.get_and_increment_daily_claude_calls(conn)
    if call_count > daily_limit:
        _count(stats, "limit_reached")
        logger.warning(
            "Tageslimit für Claude-Calls erreicht (%s/%s)", call_count, daily_limit
        )
        return []

    _count(stats, "api_calls")
    try:
        factory = client_factory or _default_client_factory
        client = factory(api_key, timeout, max_retries)
        kwargs = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": build_prompt(query, max_items)}],
        }
        if use_output_config is None:
            use_output_config = sdk_supports_output_config()
        if use_output_config:
            output_config: dict = {
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}
            }
            if effort:
                output_config["effort"] = effort
            kwargs["output_config"] = output_config
        response = client.messages.create(**kwargs)
        suggestions = parse_suggestions(response_text(response), query, max_items)
    except Exception as exc:  # noqa: BLE001 – jeder Fehler heißt: keine Erweiterung
        _count(stats, "api_errors")
        # Nur Fehlertyp und ggf. HTTP-Status loggen, nie Schlüssel oder Anfragedetails
        status = getattr(exc, "status_code", None)
        logger.warning(
            "Claude-Begriffsvorschläge fehlgeschlagen für %r: %s%s",
            query,
            type(exc).__name__,
            f" (HTTP {status})" if status else "",
        )
        return []

    rvk_repository.set_expansion_cache(
        conn,
        cache_key,
        json.dumps({"begriffe": suggestions}, ensure_ascii=False),
    )
    return suggestions
