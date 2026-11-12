"""Reine Logik für scripts/build_gold_candidates.py: Kandidatenzeilen aus K10plus-SRU-Daten.

Quelle: K10plus-SRU, Datenbank opac-de-627 (https://sru.k10plus.de/opac-de-627), frei und ohne
Zugangsdaten abfragbar. Lizenz laut K10plus-Open-Data-Seite
(https://wiki.k10plus.de/display/K10PLUS/Open+Data, geprüft am 11.11.2026): Katalogdaten unter
CC0, ausgenommen Lokaldaten. Verwendet werden nur Titeldaten (PPN, Titel, RVK aus MARC 084).

Hier passiert weder Netz- noch Dateizugriff; das übernimmt das Skript.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlencode

SRU_BASE_URL = "https://sru.k10plus.de/opac-de-627"
CANDIDATE_HEADER = "query;erwartete_notationen;quelle;fachgebiet;typ;anmerkung"
MAX_EXAMPLE_PPNS = 3

NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "marc": "http://www.loc.gov/MARC21/slim",
}


@dataclass
class Record:
    ppn: str
    title: str
    rvk: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    notation: str
    count: int
    ppns: list[str]


def build_sru_url(term: str, max_records: int, start: int = 1) -> str:
    """SRU-1.1-Abfrage über alle Felder (pica.all) mit MARCXML-Ausgabe."""
    escaped = term.replace("\\", "\\\\").replace('"', '\\"')
    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "query": f'pica.all="{escaped}"',
        "recordSchema": "marcxml",
        "maximumRecords": str(max_records),
        "startRecord": str(start),
    }
    return f"{SRU_BASE_URL}?{urlencode(params)}"


def _normalize(notation: str) -> str:
    return " ".join(notation.split()).upper()


def parse_marcxml_records(xml_text: str | bytes) -> list[Record]:
    """Liest eine SRU-Antwort (MARCXML). Wirft ET.ParseError bei kaputtem XML."""
    root = ET.fromstring(xml_text)
    records = []
    for marc in root.iter(f"{{{NS['marc']}}}record"):
        ppn_el = marc.find("marc:controlfield[@tag='001']", NS)
        ppn = (ppn_el.text or "").strip() if ppn_el is not None else ""
        title = ""
        title_el = marc.find("marc:datafield[@tag='245']/marc:subfield[@code='a']", NS)
        if title_el is not None and title_el.text:
            title = title_el.text.strip()
        rvk: list[str] = []
        for df in marc.findall("marc:datafield[@tag='084']", NS):
            sources = [
                (sf.text or "").strip().lower()
                for sf in df.findall("marc:subfield[@code='2']", NS)
            ]
            if "rvk" not in sources:
                continue
            for sf in df.findall("marc:subfield[@code='a']", NS):
                if sf.text and sf.text.strip():
                    notation = _normalize(sf.text)
                    if notation not in rvk:
                        rvk.append(notation)
        records.append(Record(ppn=ppn, title=title, rvk=rvk))
    return records


def aggregate_candidates(
    records: list[Record], min_count: int, top_n: int
) -> list[Candidate]:
    """Zählt, in wie vielen Titeln jede Notation vorkommt; nur Notationen ab min_count Titeln."""
    counts: Counter[str] = Counter()
    ppns: dict[str, list[str]] = {}
    for record in records:
        for notation in record.rvk:
            counts[notation] += 1
            examples = ppns.setdefault(notation, [])
            if record.ppn and len(examples) < MAX_EXAMPLE_PPNS:
                examples.append(record.ppn)
    ranked = sorted(
        (n for n, c in counts.items() if c >= min_count), key=lambda n: (-counts[n], n)
    )
    return [Candidate(n, counts[n], ppns.get(n, [])) for n in ranked[:top_n]]


def _clean(text: str) -> str:
    return " ".join(text.replace(";", ",").split())


def candidate_rows(
    term: str, candidates: list[Candidate], total_records: int
) -> list[str]:
    """Eine Zeile je Kandidat im Goldstandard-Format; fachgebiet und typ bleiben leer."""
    rows = []
    for c in candidates:
        quelle = "K10plus-SRU (CC0) PPN " + ", ".join(c.ppns)
        anmerkung = f"Kandidat: {c.count} von {total_records} Titeln; manuell prüfen"
        rows.append(
            ";".join(
                [_clean(term), c.notation, _clean(quelle), "", "", _clean(anmerkung)]
            )
        )
    return rows
