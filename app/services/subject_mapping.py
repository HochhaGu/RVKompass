"""GND-Sachgruppen -> RVK-Hauptgruppen aus dem Goldstandard ableiten und messen (P12, C3).

Reine Funktionen ohne Datenbank und Netz; die Daten sammelt scripts/derive_subject_mapping.py.

Die Zuordnung (Config.GND_SUBJECT_CATEGORY_TO_RVK) wirkt nur auf die Reihenfolge der
GND-Bedeutungen bei gesetztem Fachfilter (ranking.rank_senses), nicht auf die Trefferliste.
Gemessen wird deshalb an den Bedeutungen:
- „Bedeutung@1“: Die erste Bedeutung enthält eine erwartete Notation.
- „Bedeutungs-MRR“: 1 / Rang der ersten Bedeutung mit erwarteter Notation (0, wenn keine).
Gezählt werden nur Anfragen mit mindestens zwei Bedeutungen.

Der Fachfilter je Anfrage wird simuliert: die Hauptgruppen ihres Fachgebiets laut
gold_merge.FACHGEBIET_MAIN_GROUPS (eine Annahme von Claude, siehe dort).
"""

from __future__ import annotations

import string
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from app.services import gold_merge
from app.services.presentation import main_group
from app.services.ranking import rvk_groups_for_sense

# Schwellen vorab festgelegt (Phasen-Auftrag C3, Plan P12)
MIN_QUERIES = 3
MIN_SHARE = 0.8
MIN_SENSES = 2


@dataclass(frozen=True)
class SenseRecord:
    """Eine GND-Bedeutung einer Anfrage, reduziert auf das, was die Messung braucht."""

    codes: tuple[str, ...]  # Sachgruppen-Codes
    notations: frozenset[
        str
    ]  # Notationen mit Evidenz über einen Begriff dieser Bedeutung
    hit_count: int


@dataclass(frozen=True)
class QueryRecord:
    query: str
    expected: tuple[str, ...]
    fachgebiet: str
    senses: tuple[SenseRecord, ...]  # in der Reihenfolge der GND-Quelle


def sense_groups(
    codes: Iterable[str], mapping: Mapping[str, Iterable[str]]
) -> tuple[str, ...]:
    """RVK-Hauptgruppen zu Sachgruppen-Codes – dieselbe Regel wie in der Suche."""
    fake = SimpleNamespace(subject_categories=tuple((c, "") for c in codes))
    return rvk_groups_for_sense(fake, mapping)


def is_mapped(code: str, mapping: Mapping[str, Iterable[str]]) -> bool:
    return bool(sense_groups([code], mapping))


def expand_specs(specs: Iterable[str]) -> set[str]:
    """Alle Hauptgruppen (ein oder zwei Buchstaben) in den Bereichsangaben – als Fachfilter."""
    specs = list(specs)
    letters = string.ascii_uppercase
    candidates = list(letters) + [a + b for a in letters for b in letters]
    return {
        g
        for g in candidates
        if any(gold_merge.main_group_in_range(g, s) for s in specs)
    }


def filter_for(fachgebiet: str) -> set[str]:
    """Simulierter Fachfilter: Hauptgruppen des Fachgebiets (Annahme FACHGEBIET_MAIN_GROUPS)."""
    return expand_specs(gold_merge.specs_for_fachgebiet(fachgebiet))


def order_senses(
    senses: Sequence[SenseRecord],
    subject_filter: Iterable[str],
    mapping: Mapping[str, Iterable[str]],
) -> list[SenseRecord]:
    """Reihenfolge wie ranking.rank_senses: Fachfilter, dann Trefferzahl, dann GND-Reihenfolge."""
    wanted = {g.strip().upper() for g in subject_filter if g and g.strip()}
    indexed = list(enumerate(senses))
    indexed.sort(
        key=lambda item: (
            not bool(wanted & set(sense_groups(item[1].codes, mapping))),
            -item[1].hit_count,
            item[0],
        )
    )
    return [s for _, s in indexed]


def sense_rank(ordered: Sequence[SenseRecord], expected: Iterable[str]) -> int | None:
    wanted = set(expected)
    for rank, sense in enumerate(ordered, start=1):
        if sense.notations & wanted:
            return rank
    return None


def sense_metrics(
    records: Iterable[QueryRecord], mapping: Mapping[str, Iterable[str]]
) -> dict:
    """Bedeutung@1 und Bedeutungs-MRR über Anfragen mit mindestens MIN_SENSES Bedeutungen."""
    ranks = []
    for r in records:
        if len(r.senses) < MIN_SENSES:
            continue
        ordered = order_senses(r.senses, filter_for(r.fachgebiet), mapping)
        ranks.append(sense_rank(ordered, r.expected))
    n = len(ranks)
    return {
        "n": n,
        "sense@1": round(sum(1 for k in ranks if k == 1) / n, 4) if n else 0.0,
        "sense_mrr": round(sum(1 / k for k in ranks if k) / n, 4) if n else 0.0,
    }


def code_statistics(
    records: Iterable[QueryRecord], mapping: Mapping[str, Iterable[str]]
) -> dict[str, dict]:
    """Je bisher nicht zugeordnetem Code: Zahl der Anfragen, bei denen eine Bedeutung ihn
    trägt, und wie viele davon eine erwartete Notation in jeder Hauptgruppe haben."""
    stats: dict[str, dict] = {}
    for r in records:
        codes = {c for s in r.senses for c in s.codes if not is_mapped(c, mapping)}
        groups = {main_group(n) for n in r.expected} - {""}
        for code in codes:
            entry = stats.setdefault(
                code, {"queries": 0, "groups": Counter(), "sets": [], "examples": []}
            )
            entry["queries"] += 1
            entry["groups"].update(groups)
            entry["sets"].append(groups)
            if len(entry["examples"]) < 5:
                entry["examples"].append(r.query)
    return stats


def propose(stat: dict) -> tuple[str, ...] | None:
    """Vorschlag für einen Code nach den vorab festgelegten Schwellen, sonst None.

    Eine Hauptgruppe: mindestens MIN_QUERIES Anfragen und mindestens MIN_SHARE davon mit einer
    erwarteten Notation in dieser Hauptgruppe. Zwei Hauptgruppen: nur wenn erst beide zusammen
    MIN_SHARE erreichen und jede für sich mindestens MIN_QUERIES Anfragen hat.
    """
    n = stat["queries"]
    if n < MIN_QUERIES or not stat["groups"]:
        return None
    ranked = sorted(stat["groups"].items(), key=lambda kv: (-kv[1], kv[0]))
    first, count1 = ranked[0]
    if count1 >= MIN_QUERIES and count1 / n >= MIN_SHARE:
        return (first,)
    if len(ranked) < 2:
        return None
    second, count2 = ranked[1]
    if count2 < MIN_QUERIES or count1 < MIN_QUERIES:
        return None
    union = sum(1 for groups in stat["sets"] if groups & {first, second})
    if union / n >= MIN_SHARE:
        return (first, second)
    return None


def derive_mapping(
    records: Iterable[QueryRecord], mapping: Mapping[str, Iterable[str]]
) -> dict[str, dict]:
    """Vorschläge für bisher nicht zugeordnete Codes: {code: {"groups", "queries", "share", …}}.

    Bestehende Einträge bleiben unberührt (es werden nur nicht zugeordnete Codes betrachtet).
    """
    proposals = {}
    for code, stat in sorted(code_statistics(list(records), mapping).items()):
        groups = propose(stat)
        if groups is None:
            continue
        n = stat["queries"]
        support = sum(1 for s in stat["sets"] if s & set(groups))
        proposals[code] = {
            "groups": groups,
            "queries": n,
            "support": support,
            "share": round(support / n, 4),
            "per_group": {g: stat["groups"][g] for g in groups},
            "examples": stat["examples"],
        }
    return proposals


def extended_mapping(
    mapping: Mapping[str, Iterable[str]], proposals: Mapping[str, dict]
) -> dict[str, tuple[str, ...]]:
    result = {k: tuple(v) for k, v in mapping.items()}
    for code, p in proposals.items():
        if code not in result:
            result[code] = tuple(p["groups"])
    return result
