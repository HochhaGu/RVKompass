"""Optionale Begriffserweiterung eines Suchbegriffs via Anthropic Claude API.

Ergebnis wird pro normalisierter Anfrage in app.services.rvk_repository.expansion_cache
zwischengespeichert, um wiederholte API-Kosten bei gleichen/ähnlichen Anfragen zu vermeiden.
"""

from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from app.services import rvk_repository

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
DEFAULT_DAILY_LIMIT = 500

PROMPT_TEMPLATE = """Du hilfst bei der bibliothekarischen Sacherschließung mit der \
Regensburger Verbundklassifikation (RVK). Gib zu folgendem Suchbegriff verwandte Fachbegriffe, \
Synonyme und Oberbegriffe an, die bei der Suche nach einer passenden RVK-Systemstelle helfen \
könnten. Suchbegriff: "{query}"

Antworte ausschließlich mit einem JSON-Array aus 3-8 kurzen deutschen Begriffen, ohne \
weiteren Text. Beispiel: ["Begriff1", "Begriff2", "Begriff3"]"""


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def expand_query(
    conn, api_key: str, query: str, daily_limit: int = DEFAULT_DAILY_LIMIT
) -> list[str]:
    """Liefert eine Liste verwandter Begriffe für `query`, leer bei Fehlern/deaktiviertem Feature."""
    normalized = normalize_query(query)
    if not normalized:
        return []

    cached = rvk_repository.get_expansion_cache(conn, normalized)
    if cached is not None:
        return json.loads(cached)

    if not api_key:
        return []

    call_count = rvk_repository.get_and_increment_daily_claude_calls(conn)
    if call_count > daily_limit:
        logger.warning(
            "Tageslimit für Claude-Calls erreicht (%s/%s)", call_count, daily_limit
        )
        return []

    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(query=query)}],
        )
        text = response.content[0].text.strip()
        terms = json.loads(text)
        if not isinstance(terms, list):
            raise TypeError("Erwartete JSON-Liste")
        terms = [str(t) for t in terms][:8]
    except Exception:
        logger.exception(
            "Claude-Begriffserweiterung fehlgeschlagen für Query %r", query
        )
        return []

    rvk_repository.set_expansion_cache(
        conn, normalized, json.dumps(terms, ensure_ascii=False)
    )
    return terms
