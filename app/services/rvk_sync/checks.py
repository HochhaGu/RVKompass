"""Prüfungen vor dem Tausch eines neuen RVK-Stands (P8). Reine Funktionen.

1. Anzahl: Die Notationsanzahl weicht um weniger als SYNC_MAX_COUNT_DEVIATION ab.
2. Stichprobe: Alle Hauptgruppen des alten Stands und SYNC_SAMPLE_NOTATIONS gibt es auch neu.
3. Goldstandard: Notationen, die im alten Stand vorhanden und im neuen gelöscht sind, werden als
   „gelöscht in <Version>“ markiert und fallen in beiden Messläufen aus der Wertung. Notationen,
   die schon im alten Stand fehlten, werden getrennt als „fehlte schon vorher“ ausgewiesen; sie
   bleiben in der Messbasis (in beiden Läufen gleich unerreichbar) und stehen in der Mail.
   „RVK-Historie“ ist hier der Vergleich der beiden Gesamtabzüge plus `notation_change`.
4. Evaluation: Hit@10 sinkt um höchstens SYNC_MAX_HIT10_DROP (vorher und nachher mit derselben
   bereinigten Gold-Liste).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.services.evaluation import GoldEntry
from app.services.rvk_sync.diff import DiffResult

GOLD_DELETED = (
    "deleted"  # im alten Stand vorhanden, im neuen gelöscht -> aus der Wertung
)
GOLD_MISSING_BEFORE = "missing_before"  # fehlte schon im alten Stand und fehlt weiter
GOLD_NEW = "new"  # fehlte im alten Stand, ist im neuen vorhanden

GOLD_STATUS_LABELS = {
    GOLD_DELETED: "gelöscht in {version}",
    GOLD_MISSING_BEFORE: "fehlte schon vorher (bleibt in der Messbasis)",
    GOLD_NEW: "neu in {version}",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    message: str


@dataclass
class CheckReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str) -> CheckResult:
        result = CheckResult(name, passed, message)
        self.results.append(result)
        return result

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


@dataclass(frozen=True)
class GoldMark:
    query: str
    notation: str
    status: str
    version: str = ""

    @property
    def label(self) -> str:
        return GOLD_STATUS_LABELS[self.status].format(version=self.version or "?")


def check_count(old: int | None, new: int, max_deviation: float) -> tuple[bool, str]:
    """|neu − alt| / alt < max_deviation; ohne alten Stand reicht neu > 0."""
    if new <= 0:
        return False, "Der neue Stand enthält keine Datensätze."
    if not old:
        return True, f"Kein bisheriger Stand zum Vergleich; neu: {new}."
    deviation = abs(new - old) / old
    ok = deviation < max_deviation
    relation = "unter" if ok else "nicht unter"
    return (
        ok,
        f"alt {old}, neu {new}, Abweichung {deviation:.4f} ({relation} {max_deviation:.4f})",
    )


def main_groups(conn: sqlite3.Connection, table: str = "notation") -> list[str]:
    """Hauptgruppen (Zeilen aus genau zwei Großbuchstaben) einer Notationstabelle."""
    return [
        r[0]
        for r in conn.execute(
            f"SELECT notation FROM {table} WHERE notation GLOB '[A-Z][A-Z]' ORDER BY notation"
        )
    ]


def existing_notations(
    conn: sqlite3.Connection, notations: Iterable[str], table: str
) -> set[str]:
    wanted = list(dict.fromkeys(n for n in notations if n))
    found: set[str] = set()
    for start in range(0, len(wanted), 500):
        batch = wanted[start : start + 500]
        marks = ",".join("?" for _ in batch)
        found.update(
            r[0]
            for r in conn.execute(
                f"SELECT notation FROM {table} WHERE notation IN ({marks})", batch
            )
        )
    return found


def check_sample(
    conn: sqlite3.Connection, sample: Iterable[str], table: str = "notation_new"
) -> tuple[bool, str, list[str]]:
    """Alle Notationen der Stichprobe kommen im neuen Stand vor."""
    wanted = list(dict.fromkeys(s for s in sample if s))
    if not wanted:
        return False, "Leere Stichprobe – nichts zu prüfen.", []
    found = existing_notations(conn, wanted, table)
    missing = [n for n in wanted if n not in found]
    if missing:
        shown = ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
        return False, f"{len(missing)} von {len(wanted)} fehlen: {shown}", missing
    return True, f"alle {len(wanted)} vorhanden", []


def mark_gold(
    entries: Iterable[GoldEntry],
    old_present: set[str],
    new_present: set[str],
    version: str,
    diff: DiffResult | None = None,
) -> list[GoldMark]:
    """Markiert Gold-Notationen nach ihrem Stand im alten und neuen Abzug.

    `old_present`/`new_present`: Gold-Notationen, die im alten bzw. neuen Stand vorkommen.
    `diff.deleted` zählt zusätzlich als gelöscht (falls die Mengen unvollständig sind).
    """
    deleted_in_diff = set(diff.deleted) if diff else set()
    marks: list[GoldMark] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        for notation in entry.expected:
            key = (entry.query, notation)
            if key in seen:
                continue
            seen.add(key)
            in_old = notation in old_present
            in_new = notation in new_present and notation not in deleted_in_diff
            if in_old and not in_new:
                status = GOLD_DELETED
            elif not in_old and not in_new:
                status = GOLD_MISSING_BEFORE
            elif not in_old and in_new:
                status = GOLD_NEW
            else:
                continue
            marks.append(GoldMark(entry.query, notation, status, version))
    return marks


def excluded_by_marks(marks: Iterable[GoldMark]) -> set[str]:
    """Notationen, die aus der Wertung fallen: nur „gelöscht“."""
    return {m.notation for m in marks if m.status == GOLD_DELETED}


def check_eval(
    before: dict | None, after: dict | None, max_drop: float
) -> tuple[bool, str]:
    """Hit@10 nachher ≥ Hit@10 vorher − max_drop (beide Läufe mit derselben Gold-Liste)."""
    if not before or not after:
        return False, "Evaluation nicht vollständig gelaufen."
    old, new = float(before["hit@10"]), float(after["hit@10"])
    drop = round(old - new, 6)
    ok = drop <= max_drop + 1e-9
    return ok, (
        f"Hit@10 vorher {old:.4f}, nachher {new:.4f}, Veränderung {new - old:+.4f} "
        f"(höchstens −{max_drop:.4f} erlaubt)"
    )
