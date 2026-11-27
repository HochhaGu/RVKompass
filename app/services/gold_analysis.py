"""Schwachstellen des Goldstandard-Laufs (P12, D): reine Funktionen ohne Datenbank und Netz.

Eingaben sind die Einzelwerte aus einem Evaluationsbericht (`per_query` von scripts/evaluate.py)
und aus der Diagnose (`diagnose.full.per_query` von scripts/tune_scoring.py --diagnose).
Nur Analyse – nichts hier ändert Suche oder Scoring.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from app.services import evaluation
from app.services.presentation import main_group

# Ursachen in fester Prüfreihenfolge (die erste zutreffende gilt)
CAUSES = (
    ("not_candidate", "richtige Stelle nicht unter den Kandidaten"),
    ("no_gnd_sense", "keine GND-Bedeutung und kein Treffer über das RVK-Register"),
    ("wrong_sense", "GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung)"),
    (
        "vector_only",
        "nur über inhaltliche Ähnlichkeit (Vektor) gefunden, zu weit hinten",
    ),
    ("cut_after_20", "gefunden, aber hinter Platz 20 abgeschnitten"),
    ("behind_10", "gefunden, aber hinter Platz 10"),
    ("top10_not_first", "unter den ersten 10, aber nicht auf Platz 1"),
    ("ok", "auf Platz 1"),
)
CAUSE_LABELS = dict(CAUSES)

REGISTER_PATHS = ("register_exact", "register_similar")
MIN_GROUP_SIZE = 5
WORST_PER_SUBSET = 15


def classify_cause(per_query: Mapping, diag: Mapping | None) -> str:
    """Ursache für eine Anfrage, nach der Reihenfolge in CAUSES.

    Ohne Diagnose-Eintrag gilt die Anfrage als „nicht unter den Kandidaten“, wenn sie keinen
    Rang hat, sonst wird nur nach dem Rang eingeordnet.
    """
    rank = per_query.get("rank")
    diag = diag or {}
    rank_uncut = diag.get("rank_uncut", rank)
    paths = set(diag.get("expected_paths") or [])
    if rank == 1:
        return "ok"
    if rank_uncut is None:
        return "not_candidate"
    senses = per_query.get("gnd_senses") or []
    if not senses and not paths & set(REGISTER_PATHS):
        return "no_gnd_sense"
    expected_groups = {main_group(n) for n in per_query.get("expected") or []} - {""}
    top_groups = {main_group(n) for n in (per_query.get("top3") or [])[:3]}
    if (
        senses
        and not any(p.startswith("gnd_") for p in paths)
        and not (top_groups & expected_groups)
    ):
        return "wrong_sense"
    if diag.get("expected_vector_only") and (rank is None or rank > 10):
        return "vector_only"
    if rank is None:
        return "cut_after_20"
    if rank > 10:
        return "behind_10"
    return "top10_not_first"


def is_homonym(per_query: Mapping) -> bool:
    return per_query.get("typ") == "Mehrdeutig"


def annotate(
    per_query: Sequence[Mapping],
    diagnostics: Mapping[str, Mapping],
    labels: Sequence[str] | None = None,
) -> list[dict]:
    """Einzelwerte mit Ursache, Homonym-Kennzeichen und Teilmenge."""
    rows = []
    for i, q in enumerate(per_query):
        diag = diagnostics.get(q["query"])
        rows.append(
            {
                **q,
                "subset": labels[i] if labels else "alle",
                "cause": classify_cause(q, diag),
                "homonym": is_homonym(q),
                "rank_uncut": (diag or {}).get("rank_uncut", q.get("rank")),
                "expected_paths": list((diag or {}).get("expected_paths") or []),
            }
        )
    return rows


def worst_by_subset(rows: Sequence[dict], n: int = WORST_PER_SUBSET) -> dict[str, list]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["subset"], []).append(row)
    return {k: evaluation.worst_queries(v, n) for k, v in groups.items()}


def weakest_groups(
    rows: Sequence[dict], field_name: str, min_size: int = MIN_GROUP_SIZE, n: int = 8
) -> list[dict]:
    """Schwächste Gruppen (Fachgebiet oder Typ) nach MRR, nur mit mindestens `min_size`."""
    result = []
    for name, metrics in evaluation.aggregate_by(list(rows), field_name).items():
        if metrics["n"] >= min_size:
            result.append({"name": name, **metrics})
    result.sort(key=lambda m: (m["mrr"], m["hit@10"], m["name"]))
    return result[:n]


def cause_counts(rows: Iterable[dict], only_misses: bool = True) -> dict[str, int]:
    """Häufigkeit der Ursachen; Standard: nur Anfragen ohne Treffer unter den ersten 10."""
    counts = Counter(
        r["cause"]
        for r in rows
        if not only_misses or not (r.get("rank") and r["rank"] <= 10)
    )
    return {key: counts[key] for key, _ in CAUSES if counts.get(key)}


def proposals_by_impact(counts: Mapping[str, int]) -> list[dict]:
    """Ursachen nach Zahl der betroffenen Anfragen (größte Wirkung zuerst)."""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {"cause": key, "label": CAUSE_LABELS[key], "queries": count}
        for key, count in ordered
        if key not in ("ok", "top10_not_first")
    ]
