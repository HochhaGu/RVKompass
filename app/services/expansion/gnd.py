"""GND-Erweiterung: Synonyme, Ober-, Unter- und verwandte Begriffe aus der Gemeinsamen Normdatei.

Aufbau:
- `GndSource` ist die Schnittstelle. Seit P8 steht standardmäßig `LocalGndSource` (lokaler
  DNB-Abzug in data/gnd.sqlite3) dahinter, mit `LobidGndSource` (lobid-gnd live,
  30-Tage-Cache) als Rückfallebene. Aufrufer kennen nur die Schnittstelle.
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
from app.services.log_privacy import error_kind
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
        # Antworten aus gnd_cache (für /health und die Nutzungsstatistik)
        self.cache_hits = 0

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
                data = json.loads(cached)
            except json.JSONDecodeError:
                pass
            else:
                self.cache_hits += 1
                return data
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
            logger.warning("lobid-gnd-Anfrage fehlgeschlagen: %s", error_kind(exc))
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


# --- lokaler Abzug (P8) ---------------------------------------------------------------------


class LocalGndSource:
    """GND aus dem lokalen Abzug data/gnd.sqlite3 (siehe app/services/gnd_local.py).

    Gesucht wird exakt über den normalisierten Namen (gleiche Normalisierung wie
    `matches_query`): Vorzugsnamen vor Namensvarianten, danach nach GND-ID, höchstens `size`.

    Rückfallebene: Fehlt die Datei, ist sie leer oder unlesbar, oder tritt bei der Abfrage ein
    SQLite-Fehler auf, fragt die Quelle `fallback` (lobid-gnd). Ein leeres Ergebnis bei
    funktionierender lokaler Datenbank heißt dagegen „keine Bedeutung“ und geht nicht an lobid.
    """

    def __init__(self, db_path: str, fallback: GndSource | None = None):
        self.db_path = db_path
        self.fallback = fallback
        self._conn: sqlite3.Connection | None = None
        self._checked = False
        self.used_fallback = 0
        self._category_labels: dict[str, str] | None = None

    @property
    def cache_hits(self) -> int:
        return int(getattr(self.fallback, "cache_hits", 0) or 0)

    def _connection(self) -> sqlite3.Connection | None:
        if self._checked:
            return self._conn
        self._checked = True
        conn = rvk_repository.get_readonly_connection(self.db_path)
        if conn is None:
            logger.warning("Lokale GND %s fehlt – Rückfall auf lobid-gnd", self.db_path)
            return None
        try:
            row = conn.execute("SELECT COUNT(*) FROM gnd_subject").fetchone()
            if not row or not row[0]:
                raise sqlite3.DatabaseError("gnd_subject ist leer")
        except sqlite3.Error as exc:
            logger.warning(
                "Lokale GND %s unbrauchbar (%s) – Rückfall auf lobid-gnd",
                self.db_path,
                exc,
            )
            conn.close()
            return None
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _labels(self, conn) -> dict[str, str]:
        if self._category_labels is None:
            try:
                rows = conn.execute("SELECT code, label FROM gnd_category_label")
                self._category_labels = {r[0]: r[1] for r in rows}
            except sqlite3.Error:
                self._category_labels = {}
        return self._category_labels

    def _records(self, conn, gnd_ids: list[str]) -> list[GndRecord]:
        """GndRecord-Objekte in der Reihenfolge von `gnd_ids`, wie bei lobid aufgebaut."""
        if not gnd_ids:
            return []
        placeholders = ",".join("?" for _ in gnd_ids)
        subjects = {
            r[0]: r
            for r in conn.execute(
                "SELECT gnd_id, uri, preferred_name, categories_json FROM gnd_subject "
                f"WHERE gnd_id IN ({placeholders})",
                gnd_ids,
            )
        }
        variants: dict[str, list[str]] = {}
        for gnd_id, name in conn.execute(
            f"SELECT gnd_id, name FROM gnd_variant WHERE gnd_id IN ({placeholders}) "
            "ORDER BY gnd_id, position",
            gnd_ids,
        ):
            variants.setdefault(gnd_id, []).append(name)
        relations: dict[str, list[tuple[str, str]]] = {}
        targets: set[str] = set()
        for gnd_id, target, relation in conn.execute(
            "SELECT gnd_id, target_gnd_id, relation FROM gnd_relation "
            f"WHERE gnd_id IN ({placeholders}) ORDER BY gnd_id, relation, position",
            gnd_ids,
        ):
            relations.setdefault(gnd_id, []).append((relation, target))
            targets.add(target)
        # Bezeichnungen der Ober- und verwandten Begriffe aus gnd_subject ergänzen
        target_names: dict[str, str] = {}
        target_list = sorted(targets)
        for start in range(0, len(target_list), 500):
            batch = target_list[start : start + 500]
            marks = ",".join("?" for _ in batch)
            for gnd_id, name in conn.execute(
                "SELECT gnd_id, preferred_name FROM gnd_subject "
                f"WHERE gnd_id IN ({marks})",
                batch,
            ):
                target_names[gnd_id] = name
        labels = self._labels(conn)
        records = []
        for gnd_id in gnd_ids:
            row = subjects.get(gnd_id)
            if row is None:
                continue
            try:
                codes = json.loads(row[3] or "[]")
            except json.JSONDecodeError:
                codes = []
            broader = [
                (t, target_names[t])
                for rel, t in relations.get(gnd_id, [])
                if rel == "broader" and t in target_names
            ]
            related = [
                (t, target_names[t])
                for rel, t in relations.get(gnd_id, [])
                if rel == "related" and t in target_names
            ]
            records.append(
                GndRecord(
                    id=row[1],
                    gnd_id=gnd_id,
                    preferred_name=row[2],
                    variant_names=tuple(variants.get(gnd_id, [])),
                    broader=tuple(broader),
                    related=tuple(related),
                    subject_categories=tuple((c, labels.get(c, "")) for c in codes),
                )
            )
        return records

    def _fallback(self, method: str, *args) -> list[GndRecord]:
        if self.fallback is None:
            return []
        self.used_fallback += 1
        return getattr(self.fallback, method)(*args)

    def search_subject_headings(self, query: str, size: int) -> list[GndRecord]:
        conn = self._connection()
        if conn is None:
            return self._fallback("search_subject_headings", query, size)
        from app.services.gnd_local import name_norm

        key = name_norm(query)
        if not key or size <= 0:
            return []
        try:
            rows = conn.execute(
                "SELECT n.gnd_id, MAX(n.is_preferred), s.preferred_name FROM gnd_name n "
                "JOIN gnd_subject s ON s.gnd_id = n.gnd_id WHERE n.name_norm = ? "
                "GROUP BY n.gnd_id LIMIT 500",
                (key,),
            ).fetchall()
            # Rangfolge: Vorzugsname genau ohne Zusatz, dann Vorzugsname mit Zusatz <…>,
            # dann Namensvarianten; innerhalb gleichen Rangs nach GND-ID.
            query_norm = normalize_query(query)

            def rank(row) -> tuple[int, str]:
                if row[1] and normalize_query(row[2]) == query_norm:
                    return (0, row[0])
                return (1 if row[1] else 2, row[0])

            ordered = [r[0] for r in sorted(rows, key=rank)[:size]]
            return self._records(conn, ordered)
        except sqlite3.Error as exc:
            logger.warning("Lokale GND-Abfrage fehlgeschlagen: %s", exc)
            return self._fallback("search_subject_headings", query, size)

    def find_narrower(self, record_id: str, limit: int) -> list[GndRecord]:
        if not record_id or limit <= 0:
            return []
        conn = self._connection()
        if conn is None:
            return self._fallback("find_narrower", record_id, limit)
        try:
            rows = conn.execute(
                "SELECT DISTINCT gnd_id FROM gnd_relation "
                "WHERE target_gnd_id = ? AND relation = 'broader' ORDER BY gnd_id LIMIT ?",
                (gnd_id_from_uri(record_id), limit),
            ).fetchall()
            return self._records(conn, [r[0] for r in rows])
        except sqlite3.Error as exc:
            logger.warning("Lokale GND-Abfrage fehlgeschlagen: %s", exc)
            return self._fallback("find_narrower", record_id, limit)


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
        logger.warning(
            "GND-Erweiterung fehlgeschlagen (Begriff mit %d Zeichen): %s",
            len(query or ""),
            error_kind(exc),
        )
        return []


def _setting(config, key: str, default=None):
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def build_gnd_source(conn, config, http_get=None) -> GndSource:
    """Die GND-Quelle laut Konfiguration.

    GND_SOURCE = "local" (Standard ab P8): lokaler Abzug, lobid-gnd als Rückfallebene.
    GND_SOURCE = "lobid": nur lobid-gnd (Stand P3).
    """
    lobid = LobidGndSource(
        conn,
        base_url=_setting(config, "GND_API_URL", "https://lobid.org/gnd/search"),
        timeout=_setting(config, "GND_TIMEOUT_SECONDS", 4),
        cache_days=_setting(config, "GND_CACHE_DAYS", 30),
        http_get=http_get or requests.get,
    )
    source = str(_setting(config, "GND_SOURCE", "lobid") or "lobid").lower()
    db_path = _setting(config, "GND_DATABASE_PATH", None)
    if source == "local" and db_path:
        return LocalGndSource(db_path, fallback=lobid)
    return lobid


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
        logger.warning("GND-Erweiterung abgeschaltet wegen Fehler: %s", error_kind(exc))
        return []
