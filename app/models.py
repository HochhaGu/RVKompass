"""Gemeinsames SQLite-Schema für Ingestion (scripts/sync_rvk.py) und Laufzeitzugriff (app/services)."""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notation TEXT UNIQUE NOT NULL,
    label TEXT,
    hierarchy_path TEXT,
    parent_notation TEXT,
    level INTEGER,
    see_also TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_notation_notation ON notation(notation);

CREATE TABLE IF NOT EXISTS register_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notation_id INTEGER NOT NULL REFERENCES notation(id),
    term TEXT NOT NULL,
    entry_type TEXT,
    gnd_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_register_notation_id ON register_entry(notation_id);
CREATE INDEX IF NOT EXISTS idx_register_term ON register_entry(term);
CREATE INDEX IF NOT EXISTS idx_register_gnd_id ON register_entry(gnd_id);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    notation_id UNINDEXED,
    text
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    source_file TEXT,
    notation_count INTEGER,
    register_count INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS expansion_cache (
    query_normalized TEXT PRIMARY KEY,
    expansion_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gnd_cache (
    request_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage (
    day TEXT PRIMARY KEY,
    claude_calls INTEGER NOT NULL DEFAULT 0
);
"""

# MARC-Feldtyp -> menschenlesbarer Registerbegriff-Typ (siehe RVK-MarcXML-Doku, Felder 70X-75X)
REGISTER_ENTRY_TYPES = {
    "700": "Person",
    "710": "Koerperschaft",
    "711": "Konferenz",
    "730": "Werk",
    "750": "Sachschlagwort",
    "751": "Ort",
}


@dataclass
class Notation:
    id: int
    notation: str
    label: str | None
    hierarchy_path: str | None
    parent_notation: str | None
    level: int | None
    see_also: str | None = None
    note: str | None = None


@dataclass
class RegisterEntry:
    id: int
    notation_id: int
    term: str
    entry_type: str | None
    gnd_id: str | None = None


# Herkunft eines Suchbegriffs (P2, Evidence-Datenmodell).
EXPANSION_SOURCES = frozenset({"input", "gnd", "llm", "vector"})

# Beziehung des Suchbegriffs zur Eingabe.
# VORLÄUFIG: "llm_unverified" steht nicht in der Wertemenge des Phasen-Auftrags P2. Die heutigen
# Claude-Begriffe sind noch nicht gegen GND oder RVK-Register geprüft (das kommt in P4). Sie als
# "llm_verified" zu kennzeichnen, wäre falsch (Leitprinzip Transparenz). P4 entfernt den Wert,
# sobald die Prüfung steht.
EXPANSION_RELATIONS = frozenset(
    {
        "direct",
        "synonym",
        "broader",
        "narrower",
        "related",
        "llm_verified",
        "embedding",
        "llm_unverified",
    }
)

# Art der Übereinstimmung zwischen Suchbegriff und Notation.
MATCH_TYPES = frozenset({"register_exact", "register_gnd_id", "label_fts", "vector"})


@dataclass(frozen=True)
class ExpansionTerm:
    """Ein Suchbegriff samt Herkunft: Eingabe, GND, LLM oder Vektorsuche."""

    term: str
    source: str
    relation: str
    gnd_id: str | None = None
    parent_term: str | None = None

    def __post_init__(self) -> None:
        if self.source not in EXPANSION_SOURCES:
            raise ValueError(f"Unbekannte Begriffsquelle: {self.source!r}")
        if self.relation not in EXPANSION_RELATIONS:
            raise ValueError(f"Unbekannte Begriffsbeziehung: {self.relation!r}")


@dataclass(frozen=True)
class GndSense:
    """Eine Bedeutung eines Suchbegriffs laut GND (ein Sachschlagwort-Datensatz).

    Mehrdeutige Begriffe („Mutter“, „Feder“, „Bank“) ergeben mehrere Senses. Sie werden nicht
    still aufgelöst, sondern getrennt angezeigt und nach Fachfilter und Trefferzahl geordnet.
    """

    gnd_id: str
    preferred_name: str
    qualifier: str | None = None
    # (Sachgruppen-Code, Bezeichnung), z. B. ("31.6", "Maschinenbau")
    subject_categories: tuple[tuple[str, str], ...] = ()
    terms: tuple[ExpansionTerm, ...] = ()

    @property
    def display_name(self) -> str:
        if self.qualifier:
            return f"{self.preferred_name} <{self.qualifier}>"
        return self.preferred_name


@dataclass(frozen=True)
class Evidence:
    """Begründung für einen Treffer: über welchen Begriff und welchen Weg er gefunden wurde."""

    notation_id: int
    via_term: ExpansionTerm
    match_type: str
    raw_score: float

    def __post_init__(self) -> None:
        if self.match_type not in MATCH_TYPES:
            raise ValueError(f"Unbekannter Treffertyp: {self.match_type!r}")


@dataclass
class Hit:
    """Ein Suchtreffer. Der Score ist Relevanz, keine Wahrscheinlichkeit.

    Ein Treffer ohne Evidence darf nicht existieren (Leitprinzip Transparenz). Die Prüfung steht
    bewusst als raise statt assert, weil `python -O` Asserts abschalten würde.
    """

    notation: Notation
    evidences: list[Evidence]
    score: float
    score_components: dict[str, float]

    def __post_init__(self) -> None:
        if not self.evidences:
            raise ValueError(
                f"Treffer ohne Evidence ist nicht erlaubt: {self.notation.notation}"
            )
        for evidence in self.evidences:
            if evidence.notation_id != self.notation.id:
                raise ValueError(
                    f"Evidence für Notation-ID {evidence.notation_id} passt nicht zu "
                    f"Treffer {self.notation.notation} (ID {self.notation.id})"
                )
