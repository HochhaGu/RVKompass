"""GND-Erweiterung: Synonyme, Ober-, Unter- und verwandte Begriffe aus der Gemeinsamen Normdatei.

Aufbau:
- `GndSource` ist die Schnittstelle. Heute steht `LobidGndSource` (lobid-gnd live, 30-Tage-Cache)
  dahinter, ab P8 ein lokaler GND-Abzug mit lobid als Rückfallebene. Aufrufer kennen nur die
  Schnittstelle.
- `expand_gnd` macht aus den Datensätzen Bedeutungen (`GndSense`). Mehrdeutige Begriffe werden
  nicht still aufgelöst: Jeder passende Datensatz wird eine eigene Bedeutung. Kein LLM.

Feldnamen (preferredName, variantName, broaderTerm*, relatedTerm, gndSubjectCategory,
gndIdentifier, member) stammen aus dem lobid-gnd-Quellcode (conf/context.jsonld,
HomeController.java), weil die API-Doku hinter einem Bot-Schutz lag. Siehe docs/phasen/phase-3.md.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import requests

from app.models import ExpansionTerm, GndSense
from app.services import rvk_repository
from app.services.query_expansion import normalize_query

logger = logging.getLogger(__name__)

BROADER_FIELDS = (
    "broaderTermGeneral",
    "broaderTermGeneric",
    "broaderTermPartitive",
    "broaderTermInstantial",
)
SUBJECT_HEADING_FILTER = "type:SubjectHeading"
USER_AGENT = "RVKompass"
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(frozen=True)
class GndRecord:
    """Ein GND-Datensatz, reduziert auf die Felder, die die Erweiterung braucht."""

    id: str  # URI wie von lobid geliefert, z. B. https://d-nb.info/gnd/4040757-5
    gnd_id: str  # ohne Präfix, z. B. 4040757-5 (so steht sie in register_entry.gnd_id)
    preferred_name: str
    variant_names: tuple[str, ...] = ()
    broader: tuple[tuple[str, str], ...] = ()  # (gnd_id, Bezeichnung)
    related: tuple[tuple[str, str], ...] = ()  # (gnd_id, Bezeichnung)
    subject_categories: tuple[tuple[str, str], ...] = ()  # (Code, Bezeichnung)


class GndSource(Protocol):
    """Schnittstelle zur GND. Fehler führen nie zu Ausnahmen, sondern zu leeren Listen."""

    def search_subject_headings(self, query: str, size: int) -> list[GndRecord]: ...

    def find_narrower(self, record_id: str, limit: int) -> list[GndRecord]: ...


# --- reine Hilfsfunktionen -------------------------------------------------------------------


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def gnd_id_from_uri(uri: str) -> str:
    """Letzter Pfadteil einer GND-URI: "https://d-nb.info/gnd/4040757-5" -> "4040757-5"."""
    return str(uri or "").rstrip("/").rsplit("/", 1)[-1].strip()


def subject_code(uri: str) -> str:
    """Code einer GND-Sachgruppe: ".../gnd-sc#31.6" -> "31.6"; ohne "#" der ganze Wert."""
    return str(uri or "").rsplit("#", 1)[-1].strip()


def split_qualifier(name: str) -> tuple[str, str | None]:
    """Unterscheidungszusatz abtrennen: "Mutter <Technik>" -> ("Mutter", "Technik")."""
    text = (name or "").strip()
    if text.endswith(">") and "<" in text:
        base, _, rest = text.rpartition("<")
        qualifier = rest[:-1].strip()
        if base.strip() and qualifier:
            return base.strip(), qualifier
    return text, None


def _id_label_pairs(values) -> list[tuple[str, str]]:
    pairs = []
    for item in _as_list(values):
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            gnd_id = gnd_id_from_uri(item.get("id") or "")
        else:  # nur eine URI ohne Bezeichnung
            label, gnd_id = "", gnd_id_from_uri(str(item))
        if label:
            pairs.append((gnd_id, label))
    return pairs


def parse_record(data: dict) -> GndRecord | None:
    """Liest einen lobid-Datensatz robust; ohne Vorzugsnamen oder ID: None."""
    if not isinstance(data, dict):
        return None
    preferred = str(data.get("preferredName") or "").strip()
    record_id = str(data.get("id") or "").strip()
    gnd_id = str(data.get("gndIdentifier") or "").strip() or gnd_id_from_uri(record_id)
    if not preferred or not gnd_id:
        return None
    broader: list[tuple[str, str]] = []
    for field_name in BROADER_FIELDS:
        for pair in _id_label_pairs(data.get(field_name)):
            if pair not in broader:
                broader.append(pair)
    categories = []
    for item in _as_list(data.get("gndSubjectCategory")):
        if isinstance(item, dict):
            code = subject_code(item.get("id") or "")
            if code:
                categories.append((code, str(item.get("label") or "").strip()))
    variants = tuple(
        str(v).strip() for v in _as_list(data.get("variantName")) if str(v).strip()
    )
    return GndRecord(
        id=record_id or gnd_id,
        gnd_id=gnd_id,
        preferred_name=preferred,
        variant_names=variants,
        broader=tuple(broader),
        related=tuple(_id_label_pairs(data.get("relatedTerm"))),
        subject_categories=tuple(categories),
    )


def parse_search_response(data) -> list[GndRecord]:
    if not isinstance(data, dict):
        return []
    records = []
    for item in _as_list(data.get("member")):
        record = parse_record(item)
        if record is not None:
            records.append(record)
    return records


def narrower_query(record_id: str) -> str:
    """Rückwärtssuche: alle Datensätze, deren broaderTerm*-Feld diese ID enthält."""
    escaped = record_id.replace('"', "")
    return " OR ".join(f'{field}.id:"{escaped}"' for field in BROADER_FIELDS)


# --- lobid-gnd ------------------------------------------------------------------------------


class LobidGndSource:
    """GND über lobid-gnd mit Cache in der SQLite-Tabelle gnd_cache.

    Jede Ausnahme (Netz, HTTP-Status, JSON) ergibt eine leere Liste. Nach
    MAX_CONSECUTIVE_FAILURES Fehlern in Folge stellt die Instanz weitere Anfragen ein, damit ein
    Ausfall von lobid die Suche nicht mit vielen Timeouts ausbremst.
    """

    def __init__(
        self,
        conn,
        base_url: str = "https://lobid.org/gnd/search",
        timeout: float = 4,
        cache_days: int = 30,
        http_get: Callable = requests.get,
    ):
        self.conn = conn
        self.base_url = base_url
        self.timeout = timeout
        self.cache_days = cache_days
        self.http_get = http_get
        self.consecutive_failures = 0

    def _request_key(self, params: dict) -> str:
        return f"{self.base_url}?{urlencode(sorted(params.items()))}"

    def _fetch(self, params: dict):
        key = self._request_key(params)
        try:
            cached = rvk_repository.get_gnd_cache(self.conn, key, self.cache_days)
        except sqlite3.Error as exc:
            logger.warning("GND-Cache nicht lesbar: %s", exc)
            cached = None
        if cached is not None:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return None
        try:
            response = self.http_get(
                self.base_url,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Antwort ist kein JSON-Objekt")
        except Exception as exc:  # noqa: BLE001 – jeder Fehler heißt: keine Erweiterung
            self.consecutive_failures += 1
            logger.warning("lobid-gnd-Anfrage fehlgeschlagen (%s): %s", key, exc)
            return None
        self.consecutive_failures = 0
        try:
            rvk_repository.set_gnd_cache(
                self.conn, key, json.dumps(data, ensure_ascii=False)
            )
        except sqlite3.Error as exc:
            logger.warning("GND-Cache nicht beschreibbar: %s", exc)
        return data

    def search_subject_headings(self, query: str, size: int) -> list[GndRecord]:
        params = {
            "q": query,
            "filter": SUBJECT_HEADING_FILTER,
            "format": "json",
            "size": str(size),
        }
        return parse_search_response(self._fetch(params))

    def find_narrower(self, record_id: str, limit: int) -> list[GndRecord]:
        if not record_id or limit <= 0:
            return []
        params = {
            "q": narrower_query(record_id),
            "filter": SUBJECT_HEADING_FILTER,
            "format": "json",
            "size": str(limit),
        }
        return parse_search_response(self._fetch(params))[:limit]


# --- Bedeutungen ----------------------------------------------------------------------------


def matches_query(record: GndRecord, normalized_query: str) -> bool:
    """Vorzugsname (ohne Zusatz) oder eine Namensvariante entspricht genau der Anfrage."""
    names = [record.preferred_name, *record.variant_names]
    return any(
        normalize_query(split_qualifier(n)[0]) == normalized_query for n in names
    )


# Alter Name, bleibt für bestehende Aufrufer erhalten
_matches_query = matches_query


def _sense_from_record(record: GndRecord, narrower: list[GndRecord]) -> GndSense:
    name, qualifier = split_qualifier(record.preferred_name)
    parent = f"{name} <{qualifier}>" if qualifier else name
    terms: list[ExpansionTerm] = []
    seen: set[str] = set()

    def add(term: str, relation: str, gnd_id: str | None) -> None:
        normalized = normalize_query(term)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        terms.append(
            ExpansionTerm(term.strip(), "gnd", relation, gnd_id or None, parent)
        )

    add(record.preferred_name, "synonym", record.gnd_id)
    for variant in record.variant_names:
        add(variant, "synonym", record.gnd_id)
    for gnd_id, label in record.broader:
        add(label, "broader", gnd_id)
    for child in narrower:
        if child.gnd_id != record.gnd_id:
            add(child.preferred_name, "narrower", child.gnd_id)
    for gnd_id, label in record.related:
        add(label, "related", gnd_id)
    return GndSense(
        gnd_id=record.gnd_id,
        preferred_name=name,
        qualifier=qualifier,
        subject_categories=record.subject_categories,
        terms=tuple(terms),
    )


def expand_gnd(
    source: GndSource,
    query: str,
    max_senses: int = 5,
    narrower_limit: int = 10,
    search_size: int = 10,
) -> list[GndSense]:
    """Bedeutungen des Suchbegriffs laut GND, in der Reihenfolge, in der die Quelle sie liefert.

    Ein Datensatz zählt nur, wenn sein Vorzugsname (ohne Zusatz) oder eine Namensvariante genau
    der Eingabe entspricht. Unscharfe Volltexttreffer (ganze Sätze) erzeugen sonst Rauschen.
    """
    normalized = normalize_query(query or "")
    if not normalized or max_senses <= 0:
        return []
    try:
        records = source.search_subject_headings(query.strip(), search_size)
        senses: list[GndSense] = []
        seen_ids: set[str] = set()
        for record in records:
            if record.gnd_id in seen_ids or not matches_query(record, normalized):
                continue
            seen_ids.add(record.gnd_id)
            narrower = (
                source.find_narrower(record.id, narrower_limit)[:narrower_limit]
                if narrower_limit > 0
                else []
            )
            senses.append(_sense_from_record(record, narrower))
            if len(senses) >= max_senses:
                break
        return senses
    except Exception as exc:  # noqa: BLE001 – Fehler der Quelle heißt: keine Erweiterung
        logger.warning("GND-Erweiterung für %r fehlgeschlagen: %s", query, exc)
        return []


def _setting(config, key: str, default=None):
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def build_gnd_source(conn, config, http_get=None) -> GndSource:
    """Die GND-Quelle laut Konfiguration (P3: lobid-gnd)."""
    return LobidGndSource(
        conn,
        base_url=_setting(config, "GND_API_URL", "https://lobid.org/gnd/search"),
        timeout=_setting(config, "GND_TIMEOUT_SECONDS", 4),
        cache_days=_setting(config, "GND_CACHE_DAYS", 30),
        http_get=http_get or requests.get,
    )


def gnd_senses_for_query(
    conn, config, query: str, source: GndSource | None = None, http_get=None
) -> list[GndSense]:
    """Einstiegspunkt für Suchroute und Evaluation; `config` ist Flask-Config oder Config-Klasse.

    Die Evaluation gibt eine gemeinsame `source` für alle Anfragen mit, damit die Sperre nach
    mehreren Fehlern in Folge über den ganzen Lauf wirkt.
    """
    if not _setting(config, "ENABLE_GND_EXPANSION", False):
        return []
    try:
        if source is None:
            source = build_gnd_source(conn, config, http_get=http_get)
        return expand_gnd(
            source,
            query,
            max_senses=_setting(config, "GND_MAX_SENSES", 5),
            narrower_limit=_setting(config, "GND_NARROWER_LIMIT", 10),
            search_size=_setting(config, "GND_SEARCH_SIZE", 10),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GND-Erweiterung abgeschaltet wegen Fehler: %s", exc)
        return []
