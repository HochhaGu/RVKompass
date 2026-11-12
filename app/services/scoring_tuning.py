"""Abstimmung der Scoring-Gewichte (P5): reine Funktionen mit numpy, ohne Netz und Datenbank.

Ablauf (scripts/tune_scoring.py):
1. Goldstandard deterministisch und nach Anfragetyp geschichtet in Entwicklungs- und Testhälfte
   teilen (`split_gold`).
2. Merkmale je Anfrage einmal berechnen (`ranking.build_candidates`), danach jede Gewichtung
   offline bewerten (`FastScorer` für das Grid, `rank_with` als Referenz mit derselben Rangfolge
   wie die Suche).
3. Grid-Search nur auf der Entwicklungshälfte, Ziel MRR (`grid_search`).
4. Entscheidung nach Günthers Regel (`accept`), Stufenschwellen aus der Entwicklungshälfte
   (`derive_tier_thresholds`).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from app.services import evaluation, scoring
from app.services.ranking import MAX_RESULTS, CandidateFeatures, score_candidates

# Grid je Pfadgewicht (register_exact 1.0 und llm_verified 0.5 bleiben fest)
WEIGHT_GRID = {
    "gnd_synonym": (0.8, 0.9, 1.0),
    "gnd_narrower": (0.6, 0.75, 0.9),
    "gnd_broader": (0.55, 0.7, 0.85),
    "gnd_related": (0.45, 0.6, 0.75),
    "vector": (0.2, 0.4, 0.7, 1.0, 1.5, 2.0),
    "label_fts": (0.0, 0.2, 0.4),
    "register_similar": (0.2, 0.4, 0.6),
}
PATH_BONUS_GRID = (0.0, 0.025, 0.05, 0.1)
SUM_CAP_GRID = (0.9, 1.2, 1.5)

# Startgewichte laut Phasen-Auftrag P5 (dazu label_fts und register_similar, siehe Bericht).
# Fest hier, damit ein erneuter Lauf nicht von bereits eingetragenen Werten aus startet.
START_SETTINGS = scoring.ScoringSettings(
    formula="sum",
    weights={
        "register_exact": 1.0,
        "gnd_synonym": 0.9,
        "gnd_narrower": 0.75,
        "gnd_broader": 0.7,
        "gnd_related": 0.6,
        "llm_verified": 0.5,
        "vector": 0.4,
        "label_fts": 0.2,
        "register_similar": 0.4,
    },
    path_bonus=0.05,
    path_bonus_cap=0.15,
    sum_cap=1.2,
)

# Anteil „passender“ Treffer (Hauptgruppe einer erwarteten Notation), ab dem eine Stufe beginnt
TIER_HIGH_SHARE = 0.8
TIER_MEDIUM_SHARE = 0.5
# Mindestzahl Treffer oberhalb einer Schwelle, damit sie nicht an Einzelfällen hängt
TIER_MIN_SUPPORT = 10
TIER_TOP_K = 10


@dataclass
class QueryFeatures:
    entry: evaluation.GoldEntry
    features: list[CandidateFeatures]


# --- Teilung ---------------------------------------------------------------------------------


def split_indices(
    entries: Sequence[evaluation.GoldEntry],
) -> tuple[list[int], list[int]]:
    """Positionen für Entwicklung und Test: je Anfragetyp in Dateireihenfolge abwechselnd
    (gerade Position im Typ -> Entwicklung, ungerade -> Test)."""
    seen: dict[str, int] = {}
    dev, test = [], []
    for index, entry in enumerate(entries):
        position = seen.get(entry.typ, 0)
        seen[entry.typ] = position + 1
        (dev if position % 2 == 0 else test).append(index)
    return dev, test


def split_gold(entries: Sequence) -> tuple[list, list]:
    dev_idx, test_idx = split_indices(entries)
    return [entries[i] for i in dev_idx], [entries[i] for i in test_idx]


# --- Referenz-Bewertung (dieselbe Rangfolge wie die Suche) -------------------------------------


def rank_with(qf: QueryFeatures, settings: scoring.ScoringSettings) -> list[str]:
    return [f.notation for f, _, _ in score_candidates(qf.features, settings)][
        :MAX_RESULTS
    ]


def per_query_for(
    qfs: Iterable[QueryFeatures], settings: scoring.ScoringSettings
) -> list[dict]:
    return [
        evaluation.evaluate_query(qf.entry, rank_with(qf, settings), 0.0, [])
        for qf in qfs
    ]


def metrics_for(
    qfs: Sequence[QueryFeatures], settings: scoring.ScoringSettings
) -> dict:
    """Kennzahlen wie in scripts/evaluate.py (ohne Antwortzeit)."""
    metrics = evaluation.aggregate(per_query_for(qfs, settings))
    metrics.pop("mean_response_ms", None)
    return metrics


# --- Grid ------------------------------------------------------------------------------------


def weight_grid(variant: str) -> list[dict]:
    """Alle Kombinationen für eine Formel: {"weights": {...}, "path_bonus"?, "sum_cap"?}."""
    if variant not in scoring.NEW_FORMULAS:
        raise ValueError(f"Kein Grid für Formel {variant!r}")
    keys = list(WEIGHT_GRID)
    extra: tuple = (None,)
    if variant == "max_plus":
        extra = PATH_BONUS_GRID
    elif variant == "sum_capped":
        extra = SUM_CAP_GRID
    grid = []
    for values in itertools.product(*(WEIGHT_GRID[k] for k in keys)):
        for value in extra:
            combo: dict = {"weights": dict(zip(keys, values, strict=True))}
            if variant == "max_plus":
                combo["path_bonus"] = value
            elif variant == "sum_capped":
                combo["sum_cap"] = value
            grid.append(combo)
    return grid


def settings_for(
    base: scoring.ScoringSettings, variant: str, combo: dict
) -> scoring.ScoringSettings:
    weights = dict(base.weights)
    weights.update(combo["weights"])
    changes: dict = {"formula": variant, "weights": weights}
    if "path_bonus" in combo:
        changes["path_bonus"] = combo["path_bonus"]
    if "sum_cap" in combo:
        changes["sum_cap"] = combo["sum_cap"]
    return base.replace(**changes)


def distance_to_start(
    base: scoring.ScoringSettings, settings: scoring.ScoringSettings
) -> float:
    """Abstand zu den Startgewichten (Gleichstand -> startnah, Schutz vor Overfitting)."""
    total = sum(abs(settings.weight(k) - base.weight(k)) for k in WEIGHT_GRID)
    total += abs(settings.path_bonus - base.path_bonus)
    total += abs(settings.sum_cap - base.sum_cap)
    return total


class FastScorer:
    """Vektorisierte Bewertung vieler Gewichtungen auf festen Merkmalen.

    Liefert denselben Rang des ersten erwarteten Treffers wie `rank_with`: Sortierung nach
    (Score, Cosine, Notation), Score auf 9 Stellen gerundet.
    """

    def __init__(self, qfs: Sequence[QueryFeatures]):
        self.n_queries = len(qfs)
        strengths, cosines, raw_cosines, n_types = [], [], [], []
        exp_index, exp_query, pair_exp, pair_other = [], [], [], []
        tiebreak = []
        offset = 0
        for q, qf in enumerate(qfs):
            feats = qf.features
            order = sorted(
                range(len(feats)), key=lambda i: (-feats[i].cosine, feats[i].notation)
            )
            local_tb = [0] * len(feats)
            for pos, i in enumerate(order):
                local_tb[i] = pos
            tiebreak.extend(local_tb)
            wanted = {evaluation.normalize_notation(e) for e in qf.entry.expected}
            for i, f in enumerate(feats):
                strengths.append(
                    [f.strengths.get(p, 0.0) for p in scoring.NON_VECTOR_PATHS]
                )
                cosines.append(max(float(f.cosine), 0.0))
                raw_cosines.append(float(f.cosine))
                n_types.append(scoring.count_path_types(f.strengths))
                if evaluation.normalize_notation(f.notation) in wanted:
                    e = len(exp_index)
                    exp_index.append(offset + i)
                    exp_query.append(q)
                    for j in range(len(feats)):
                        if j != i:
                            pair_exp.append(e)
                            pair_other.append(offset + j)
            offset += len(feats)
        self.paths = scoring.NON_VECTOR_PATHS
        self.S = np.array(strengths, dtype=float).reshape(-1, len(self.paths))
        self.cos = np.array(cosines, dtype=float)
        self.n_types = np.array(n_types, dtype=float)
        self.tb = np.array(tiebreak, dtype=int)
        self.exp_index = np.array(exp_index, dtype=int)
        self.exp_query = np.array(exp_query, dtype=int)
        self.pair_exp = np.array(pair_exp, dtype=int)
        self.pair_other = np.array(pair_other, dtype=int)

    def scores(self, settings: scoring.ScoringSettings) -> np.ndarray:
        w = np.array([settings.weight(p) for p in self.paths], dtype=float)
        contributions = self.S * w
        vector = settings.weight("vector") * self.cos
        if settings.formula == "sum":
            score = contributions.sum(axis=1) + vector
        elif settings.formula == "sum_capped":
            score = np.minimum(contributions.sum(axis=1), settings.sum_cap) + vector
        elif settings.formula == "max_plus":
            best = contributions.max(axis=1) if len(self.paths) else 0.0
            bonus = np.minimum(
                settings.path_bonus * np.maximum(self.n_types - 1, 0),
                settings.path_bonus_cap,
            )
            score = best + vector + bonus
        else:
            raise ValueError(f"FastScorer kennt die Formel {settings.formula!r} nicht")
        return np.round(score, 9)

    def ranks(self, settings: scoring.ScoringSettings) -> np.ndarray:
        """Rang des ersten erwarteten Treffers je Anfrage (inf = nicht in den Kandidaten)."""
        ranks = np.full(self.n_queries, np.inf)
        if len(self.exp_index) == 0:
            return ranks
        sc = self.scores(settings)
        se = sc[self.exp_index][self.pair_exp]
        so = sc[self.pair_other]
        te = self.tb[self.exp_index][self.pair_exp]
        to = self.tb[self.pair_other]
        beats = (so > se) | ((so == se) & (to < te))
        exp_ranks = 1.0 + np.bincount(
            self.pair_exp, weights=beats.astype(float), minlength=len(self.exp_index)
        )
        np.minimum.at(ranks, self.exp_query, exp_ranks)
        return ranks

    def metrics(self, settings: scoring.ScoringSettings) -> dict:
        ranks = self.ranks(settings)
        valid = ranks <= MAX_RESULTS
        rr = np.where(valid, 1.0 / np.where(valid, ranks, 1.0), 0.0)
        n = max(self.n_queries, 1)
        return {
            "mrr": float(rr.sum() / n),
            "hit@10": float((ranks <= 10).sum() / n),
            "hit@1": float((ranks <= 1).sum() / n),
        }


def grid_search(
    qfs: Sequence[QueryFeatures], variant: str, base: scoring.ScoringSettings
) -> dict:
    """Beste Gewichtung einer Formel auf `qfs` (Entwicklungshälfte): höchstes MRR, dann
    höheres Hit@10, dann kleinster Abstand zu den Startgewichten."""
    scorer = FastScorer(qfs)
    best = None
    best_key = None
    combos = weight_grid(variant)
    for combo in combos:
        settings = settings_for(base, variant, combo)
        m = scorer.metrics(settings)
        key = (
            round(m["mrr"], 9),
            round(m["hit@10"], 9),
            -round(distance_to_start(base, settings), 9),
        )
        if best_key is None or key > best_key:
            best_key = key
            best = (settings, m)
    settings, m = best
    return {
        "variant": variant,
        "settings": settings,
        "dev_fast": m,
        "combos": len(combos),
    }


# --- Entscheidung ----------------------------------------------------------------------------


def accept(
    variants: Sequence[dict],
    legacy_test: dict,
    p4_reference: dict,
    gate_hit10: float | None,
    gate_tolerance: float = 0.02,
) -> dict:
    """Entscheidungsregel P5 (Günther Punkte 3 und 4).

    `variants`: je Formel {"variant", "dev", "test", "full"} (Kennzahlen). Geprüft wird in der
    Reihenfolge des MRR auf der Entwicklungshälfte. Eine Variante wird übernommen, wenn auf der
    Testhälfte Hit@10 und MRR mindestens die P4-Werte erreichen, mindestens so gut sind wie
    „legacy“ auf derselben Hälfte und auf dem vollen Goldstandard das Gate (Hit@10 höchstens
    `gate_tolerance` unter dem letzten Bericht) hält. Sonst Option (b): „legacy“.
    """
    ordered = sorted(variants, key=lambda v: -v["dev"]["mrr"])
    checks = []
    chosen = None
    for v in ordered:
        test, full = v["test"], v["full"]
        conditions = {
            "test_hit10_ge_p4": test["hit@10"] >= p4_reference["hit@10"],
            "test_mrr_ge_p4": test["mrr"] >= p4_reference["mrr"],
            "test_hit10_ge_legacy": test["hit@10"] >= legacy_test["hit@10"],
            "test_mrr_ge_legacy": test["mrr"] >= legacy_test["mrr"],
            "full_hit10_gate": gate_hit10 is None
            or full["hit@10"] >= round(gate_hit10 - gate_tolerance, 4),
        }
        ok = all(conditions.values())
        beats_legacy = (
            conditions["test_hit10_ge_legacy"] and conditions["test_mrr_ge_legacy"]
        )
        checks.append(
            {
                "variant": v["variant"],
                "accepted": ok,
                "conditions": conditions,
                "beats_legacy_on_test_only": beats_legacy and not ok,
            }
        )
        if ok and chosen is None:
            chosen = v["variant"]
    if chosen:
        reason = f"Formel „{chosen}“ erfüllt alle Bedingungen und wird übernommen."
    else:
        reason = (
            "Keine Formel erfüllt alle Bedingungen – Option (b): bisherige Rangfolge "
            "(„legacy“) bleibt, neu ist nur die Stufenanzeige."
        )
    return {
        "chosen": chosen or "legacy",
        "option": "c" if chosen else "b",
        "checks": checks,
        "reason": reason,
    }


# --- Stufen ----------------------------------------------------------------------------------


def top_hits(
    qfs: Sequence[QueryFeatures], settings: scoring.ScoringSettings, k: int = TIER_TOP_K
) -> list[tuple[float, bool, bool]]:
    """(Score, passend, exakt) für die ersten k Treffer je Anfrage.

    „passend“: Hauptgruppe einer erwarteten Notation; „exakt“: eine erwartete Notation.
    """
    rows = []
    for qf in qfs:
        groups = {evaluation.main_group(e) for e in qf.entry.expected} - {""}
        wanted = {evaluation.normalize_notation(e) for e in qf.entry.expected}
        for f, score, _ in score_candidates(qf.features, settings)[:k]:
            rows.append(
                (
                    float(score),
                    evaluation.main_group(f.notation) in groups,
                    evaluation.normalize_notation(f.notation) in wanted,
                )
            )
    return rows


def _floor4(value: float) -> float:
    return math.floor(value * 10000) / 10000


def _smallest_threshold(rows, share: float, below: float | None) -> float | None:
    """Kleinste Schwelle t (aus den beobachteten Scores), bei der unter allen Treffern mit
    Score >= t mindestens `share` passen und es mindestens TIER_MIN_SUPPORT Treffer gibt."""
    found = None
    ordered = sorted(rows, key=lambda r: -r[0])
    good = 0
    for count, (score, ok, _) in enumerate(ordered, start=1):
        good += ok
        # Schwelle nur zwischen verschiedenen Scores setzen
        if count < len(ordered) and ordered[count][0] == score:
            continue
        if below is not None and score >= below:
            continue
        if count >= TIER_MIN_SUPPORT and good / count >= share:
            found = score
    return found


def derive_tier_thresholds(rows: Sequence[tuple[float, bool, bool]]) -> dict:
    """Schwellen „hoch“ und „mittel“ aus (Score, passend, exakt) der Entwicklungshälfte.

    t_hoch: kleinste Schwelle mit mindestens 80 % passenden Treffern; t_mittel: kleinste mit
    mindestens 50 %. Rückfall, wenn nicht erreichbar: Drittel-Quantile der Scores.
    """
    if not rows:
        raise ValueError("Keine Treffer für die Stufenschwellen")
    high = _smallest_threshold(rows, TIER_HIGH_SHARE, None)
    medium = (
        _smallest_threshold(rows, TIER_MEDIUM_SHARE, high) if high is not None else None
    )
    if high is not None and medium is not None:
        return {
            "tier_high": _floor4(high),
            "tier_medium": _floor4(medium),
            "method": "share",
        }
    scores = np.array([r[0] for r in rows], dtype=float)
    q_high = float(np.quantile(scores, 2 / 3))
    q_medium = float(np.quantile(scores, 1 / 3))
    if high is not None and high > q_medium:
        return {
            "tier_high": _floor4(high),
            "tier_medium": _floor4(q_medium),
            "method": "share_high_quantile_medium",
        }
    return {
        "tier_high": _floor4(q_high),
        "tier_medium": _floor4(q_medium),
        "method": "quantile",
    }


def tier_shares(
    rows: Sequence[tuple[float, bool, bool]], settings: scoring.ScoringSettings
) -> dict[str, dict]:
    """Je Stufe: Zahl der Treffer, Anteil passender und Anteil exakter Treffer (nur Bericht)."""
    result = {}
    for tier in scoring.TIERS:
        members = [r for r in rows if scoring.tier_for(r[0], settings) == tier]
        n = len(members)
        result[tier] = {
            "n": n,
            "share_main_group": round(sum(r[1] for r in members) / n, 4) if n else None,
            "share_exact": round(sum(r[2] for r in members) / n, 4) if n else None,
        }
    return result
