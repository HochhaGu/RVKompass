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


# --- Diagnose der Ränge (P11) ------------------------------------------------------------------

# Plätze, deren Score die Diagnose je Anfrage festhält (Grenzen Hit@5 und Hit@10)
DIAGNOSE_POSITIONS = (5, 6, 10, 11)


def has_norm_path(strengths: dict[str, float]) -> bool:
    """Wahr, wenn ein Fundweg außer Vektor Evidenz hat (Register, GND, Volltext, LLM …)."""
    return any(strengths.get(p, 0.0) > 0 for p in scoring.NON_VECTOR_PATHS)


def rank_diagnostics(
    qfs: Sequence[QueryFeatures], settings: scoring.ScoringSettings
) -> dict:
    """Prüft je Anfrage, wo die erste erwartete Stelle landet und warum (Prüfauftrag P11).

    Rechnet den Rang auf demselben Weg wie scripts/evaluate.py (`score_candidates`, danach
    Abschneiden auf MAX_RESULTS) und unabhängig davon über `FastScorer` (Grid-Weg). Je Anfrage:
    Rang ungekürzt und gekürzt, Rang laut FastScorer, Zahl der Kandidaten (gesamt und mit
    Fundweg außer Vektor), Scores auf den Plätzen 5, 6, 10, 11, Gleichstand an der Grenze 5/6,
    Fundwege der ersten erwarteten Stelle.
    """
    fast_ranks = FastScorer(qfs).ranks(settings) if qfs else []
    per_query = []
    for qf, fast_rank in zip(qfs, fast_ranks, strict=True):
        scored = score_candidates(qf.features, settings)
        wanted = {evaluation.normalize_notation(e) for e in qf.entry.expected}
        scores = [round(float(s), 9) for _, s, _ in scored]
        full_rank = None
        expected_feature = None
        for position, (feature, _, _) in enumerate(scored, start=1):
            if evaluation.normalize_notation(feature.notation) in wanted:
                full_rank, expected_feature = position, feature
                break
        shown_rank = full_rank if full_rank and full_rank <= MAX_RESULTS else None
        with_path = sum(1 for f in qf.features if has_norm_path(f.strengths))
        expected_paths = (
            sorted(
                p
                for p, v in expected_feature.strengths.items()
                if v > 0 and p != "vector"
            )
            if expected_feature is not None
            else []
        )
        per_query.append(
            {
                "query": qf.entry.query,
                "typ": qf.entry.typ,
                "rank": shown_rank,
                "rank_uncut": full_rank,
                "rank_fast": None if math.isinf(fast_rank) else int(fast_rank),
                "candidates": len(scored),
                "candidates_with_path": with_path,
                "scores_at": {
                    str(k): scores[k - 1] if len(scores) >= k else None
                    for k in DIAGNOSE_POSITIONS
                },
                "tie_5_6": len(scores) >= 6 and scores[4] == scores[5],
                "expected_score": scores[full_rank - 1] if full_rank else None,
                "expected_paths": expected_paths,
                "expected_vector_only": expected_feature is not None
                and not expected_paths,
            }
        )
    n = max(len(per_query), 1)
    histogram: dict[str, int] = {str(k): 0 for k in range(1, MAX_RESULTS + 1)}
    histogram["nach Platz 20 (abgeschnitten)"] = 0
    histogram["nicht unter den Kandidaten"] = 0
    for q in per_query:
        if q["rank"] is not None:
            histogram[str(q["rank"])] += 1
        elif q["rank_uncut"] is not None:
            histogram["nach Platz 20 (abgeschnitten)"] += 1
        else:
            histogram["nicht unter den Kandidaten"] += 1

    def share(pred) -> float:
        return round(sum(1 for q in per_query if pred(q)) / n, 4)

    found = [q for q in per_query if q["rank_uncut"] is not None]
    return {
        "per_query": per_query,
        "histogram": histogram,
        "hit@5": share(lambda q: q["rank"] is not None and q["rank"] <= 5),
        "hit@10": share(lambda q: q["rank"] is not None and q["rank"] <= 10),
        "hit@5_fast": share(
            lambda q: q["rank_fast"] is not None and q["rank_fast"] <= 5
        ),
        "hit@10_fast": share(
            lambda q: q["rank_fast"] is not None and q["rank_fast"] <= 10
        ),
        "rank_6_to_10": sum(
            1 for q in per_query if q["rank"] is not None and 6 <= q["rank"] <= 10
        ),
        "fast_matches_reference": all(
            q["rank_fast"] == q["rank_uncut"] for q in per_query
        ),
        "ties_at_5_6": sum(1 for q in per_query if q["tie_5_6"]),
        "expected_found": len(found),
        "expected_with_norm_path": sum(
            1 for q in found if not q["expected_vector_only"]
        ),
        "expected_vector_only": sum(1 for q in found if q["expected_vector_only"]),
        "queries_with_10_path_candidates": sum(
            1 for q in per_query if q["candidates_with_path"] >= 10
        ),
    }


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
            "hit@5": float((ranks <= 5).sum() / n),
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


# --- Fünf Stufen (P11) -------------------------------------------------------------------------

# Gesetzte Annahme (nicht gemessen): Anteil passender Treffer (Hauptgruppe einer erwarteten
# Notation), ab dem die Stufe beginnt – „sehr hoch“, „hoch“, „mittel“, „schwach“. Darunter
# „gering“. Gemessen wird nur, wo die Grenzen dann liegen und ob sie auf der Testhälfte halten.
FIVE_TIER_SHARES = (
    ("tier_very_high", 0.8),
    ("tier_high", 0.65),
    ("tier_medium", 0.5),
    ("tier_weak", 0.3),
)
# Rückfall je Grenze, wenn die Anteilsregel nicht erreichbar ist: Quintile der Scores
FIVE_TIER_QUANTILES = {
    "tier_very_high": 0.8,
    "tier_high": 0.6,
    "tier_medium": 0.4,
    "tier_weak": 0.2,
}
# Stufenwörter von unten nach oben (wie presentation.RELEVANCE_WORDS)
FIVE_TIER_WORDS = ("gering", "schwach", "mittel", "hoch", "sehr hoch")


def derive_five_tiers(rows: Sequence[tuple[float, bool, bool]]) -> dict:
    """Vier Schwellen der fünf Stufen aus (Score, passend, exakt) der Entwicklungshälfte.

    Anteilsregel, je Grenze von oben nach unten: die kleinste Score-Grenze, ab der mindestens
    der Anteil aus FIVE_TIER_SHARES passt, gesetzt nur zwischen verschiedenen Scores und streng
    unter der vorigen Grenze; jede Stufe (Band zwischen zwei Grenzen) braucht mindestens
    TIER_MIN_SUPPORT Treffer. Gilt nur, wenn alle vier Grenzen so erreichbar sind.

    Sonst – damit keine Mischung entsteht, bei der einzelne Stufen fast leer bleiben – gelten
    für alle vier Grenzen die Quintile der Scores (reine Rangordnung, nicht kalibriert).
    `method` ist „share“ oder „quantile“; `share_rule` hält fest, welche Grenzen die
    Anteilsregel erreicht hätte (für den Bericht).
    """
    if not rows:
        raise ValueError("Keine Treffer für die Stufenschwellen")
    all_scores = [float(r[0]) for r in rows]

    def band_size(low: float, high: float | None) -> int:
        return sum(1 for s in all_scores if s >= low and (high is None or s < high))

    share_rule: dict[str, float | None] = {}
    below: float | None = None
    usable = True
    for name, target in FIVE_TIER_SHARES:
        # auch nach einer unerreichbaren Grenze weiter prüfen (nur für den Bericht)
        found = _smallest_threshold(rows, target, below)
        if found is not None and band_size(found, below) < TIER_MIN_SUPPORT:
            found = None
        share_rule[name] = None if found is None else _floor4(found)
        if found is None:
            usable = False
        else:
            below = found
    if usable:
        thresholds = {name: share_rule[name] for name, _ in FIVE_TIER_SHARES}
        method = "share"
    else:
        method = "quantile"
        distinct = sorted(set(all_scores))
        thresholds = {}
        below = None
        for name, _ in FIVE_TIER_SHARES:
            q = float(np.quantile(np.array(all_scores), FIVE_TIER_QUANTILES[name]))
            # kleinster beobachteter Score ab dem Quantil, unter der vorigen Grenze
            candidates = [
                s for s in distinct if s >= q and (below is None or s < below)
            ]
            if not candidates:
                raise ValueError("Zu wenige verschiedene Scores für fünf Stufen")
            below = candidates[0]
            thresholds[name] = _floor4(below)
        values = [thresholds[name] for name, _ in FIVE_TIER_SHARES]
        if len(set(values)) < len(values):
            raise ValueError("Zu wenige verschiedene Scores für fünf Stufen")
    return {
        **thresholds,
        "method": method,
        "share_rule": share_rule,
        "shares": dict(FIVE_TIER_SHARES),
        "min_support": TIER_MIN_SUPPORT,
    }


def wilson_interval(
    successes: int, n: int, z: float = 1.96
) -> tuple[float, float] | None:
    """95-%-Wilson-Intervall für einen Anteil; ohne Fälle None."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(centre - half, 0.0), 4), round(min(centre + half, 1.0), 4)


def five_tier_level(score: float, settings: scoring.ScoringSettings) -> int:
    """Stufe 1–5 nach den vier Schwellen von `settings` (wie presentation.relevance_level)."""
    bounds = scoring.five_tier_bounds(settings)
    return 1 + sum(score >= b for b in bounds)


def five_tier_shares(
    rows: Sequence[tuple[float, bool, bool]], settings: scoring.ScoringSettings
) -> dict:
    """Je Stufe: Treffer, Anteil passend/exakt mit 95-%-Wilson-Intervall (nur Bericht).

    Dazu `ordered` (Anteil passend fällt von Stufe zu Stufe nicht an) und `separated` je
    benachbartem Stufenpaar (Intervalle überlappen nicht).
    """
    levels: dict[str, dict] = {}
    for level, word in enumerate(FIVE_TIER_WORDS, start=1):
        members = [r for r in rows if five_tier_level(r[0], settings) == level]
        n = len(members)
        good = sum(1 for r in members if r[1])
        exact = sum(1 for r in members if r[2])
        levels[word] = {
            "n": n,
            "main_group": good,
            "exact": exact,
            "share_main_group": round(good / n, 4) if n else None,
            "share_exact": round(exact / n, 4) if n else None,
            "ci_main_group": wilson_interval(good, n),
            "ci_exact": wilson_interval(exact, n),
        }
    words = list(FIVE_TIER_WORDS)
    ordered = True
    separated = {}
    for lower, upper in itertools.pairwise(words):
        lo, up = levels[lower], levels[upper]
        if lo["n"] and up["n"] and up["share_main_group"] < lo["share_main_group"]:
            ordered = False
        if lo["ci_main_group"] and up["ci_main_group"]:
            separated[f"{lower}|{upper}"] = (
                up["ci_main_group"][0] > lo["ci_main_group"][1]
            )
        else:
            separated[f"{lower}|{upper}"] = False
    return {"levels": levels, "ordered": ordered, "separated": separated}


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
