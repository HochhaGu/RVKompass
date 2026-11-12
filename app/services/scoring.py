"""Erklärbarer Score (P5): reine Funktionen, ohne Netz und ohne Datenbank.

Jede Evidenz eines Treffers gehört zu genau einem Pfadtyp (Direkt, GND-Synonym, GND-Oberbegriff,
GND-Unterbegriff, GND-verwandt, LLM-verifiziert, Vektor, Volltext, Registerbegriff ähnlich). Je
Pfadtyp zählt die stärkste Evidenz. Der Beitrag eines Pfads ist Pfadgewicht × Evidenzstärke; der
Vektor-Beitrag ist Gewicht × Cosine zur Eingabe und gilt für jeden Kandidaten.

Formeln (Günthers Rahmen: höchstens drei Varianten, dazu „legacy“ als Bezug):
- "sum":        Summe der Pfadbeiträge + Vektor-Beitrag
- "max_plus":   stärkster Pfadbeitrag + Vektor-Beitrag + Mehrfach-Fund-Zuschlag (gedeckelt)
- "sum_capped": Summe der Pfadbeiträge, gedeckelt (proportional skaliert), + Vektor-Beitrag
- "legacy":     Cosine + kleine feste Zuschläge, gedeckelt auf 1.0 (Stand P4, unverändert)

Bei den drei neuen Formeln ist die Summe der Bestandteile exakt der Score. Der Score ist ein
Relevanzmaß, keine Wahrscheinlichkeit; die Oberfläche zeigt deshalb nur Stufen und Balken.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.models import Evidence
from app.services.query_expansion import normalize_query

FORMULAS = ("legacy", "sum", "max_plus", "sum_capped")
NEW_FORMULAS = ("sum", "max_plus", "sum_capped")

# Reihenfolge = Reihenfolge der Balkensegmente und Vorrang bei gleich starken Pfaden
PATH_TYPES = (
    "register_exact",
    "gnd_synonym",
    "gnd_narrower",
    "gnd_broader",
    "gnd_related",
    "llm_verified",
    "label_fts",
    "register_similar",
    "vector",
)
# Pfade, deren Beitrag in Summe/Maximum eingeht (Vektor wird immer als eigener Summand addiert)
NON_VECTOR_PATHS = tuple(p for p in PATH_TYPES if p != "vector")

GND_RELATION_PATHS = {
    "synonym": "gnd_synonym",
    "broader": "gnd_broader",
    "narrower": "gnd_narrower",
    "related": "gnd_related",
}

# Deutsche Bezeichnungen für die Balkensegmente, auch für die Bestandteile der Formel „legacy“
PATH_LABELS = {
    "register_exact": "Direkt (Register)",
    "gnd_synonym": "GND-Synonym",
    "gnd_broader": "GND-Oberbegriff",
    "gnd_narrower": "GND-Unterbegriff",
    "gnd_related": "GND-verwandt",
    "llm_verified": "LLM-verifiziert",
    "vector": "Vektor",
    "label_fts": "Volltext",
    "register_similar": "Registerbegriff ähnlich",
    "path_bonus": "Mehrfach-Fund",
    # Formel „legacy“ (Stand P4)
    "cosine": "Vektor",
    "keyword_bonus": "Volltext",
    "register_bonus": "Registerbegriff",
    "gnd_bonus": "GND",
    "llm_bonus": "LLM-verifiziert",
}

TIERS = ("hoch", "mittel", "niedrig")

# --- Formel „legacy“: nur als Bezug und Rückfall (Stand P4, aus ranking.py hierher verschoben) ---
KEYWORD_BONUS = 0.03
TERM_MATCH_BONUS = 0.015
GND_SYNONYM_BONUS = 0.08
GND_RELATION_BONUS = 0.02
# Bewusst 0 seit P4: ein Zuschlag nur mit Vorher/Nachher-Messung
LLM_VERIFIED_BONUS = 0.0


@dataclass(frozen=True)
class ScoringSettings:
    formula: str = "legacy"
    weights: Mapping[str, float] = field(default_factory=dict)
    path_bonus: float = 0.05
    path_bonus_cap: float = 0.15
    sum_cap: float = 1.2
    tier_high: float = 0.75
    tier_medium: float = 0.55

    def __post_init__(self) -> None:
        if self.formula not in FORMULAS:
            raise ValueError(f"Unbekannte Scoring-Formel: {self.formula!r}")
        unknown = set(self.weights) - set(PATH_TYPES)
        if unknown:
            raise ValueError(
                f"Unbekannte Pfadtypen in den Gewichten: {sorted(unknown)}"
            )
        if self.formula != "legacy":
            missing = set(PATH_TYPES) - set(self.weights)
            if missing:
                raise ValueError(f"Gewichte fehlen für: {sorted(missing)}")
        if self.tier_high < self.tier_medium:
            raise ValueError("Schwelle „hoch“ muss über der Schwelle „mittel“ liegen")

    def weight(self, path: str) -> float:
        return float(self.weights.get(path, 0.0))

    def replace(self, **changes) -> ScoringSettings:
        values = {
            "formula": self.formula,
            "weights": dict(self.weights),
            "path_bonus": self.path_bonus,
            "path_bonus_cap": self.path_bonus_cap,
            "sum_cap": self.sum_cap,
            "tier_high": self.tier_high,
            "tier_medium": self.tier_medium,
        }
        values.update(changes)
        return ScoringSettings(**values)

    def to_dict(self) -> dict:
        return {
            "formula": self.formula,
            "weights": {p: self.weight(p) for p in PATH_TYPES},
            "path_bonus": self.path_bonus,
            "path_bonus_cap": self.path_bonus_cap,
            "sum_cap": self.sum_cap,
            "tier_high": self.tier_high,
            "tier_medium": self.tier_medium,
        }

    @classmethod
    def from_config(cls, config) -> ScoringSettings:
        """Liest die SCORE_*-Werte aus einer Config-Klasse oder einem Flask-Config-Dict.

        Fehlende Werte kommen aus `app.config.Config`.
        """
        from app.config import Config

        def get(name):
            if isinstance(config, Mapping):
                if name in config:
                    return config[name]
            elif hasattr(config, name):
                return getattr(config, name)
            return getattr(Config, name)

        return cls(
            formula=get("SCORING_FORMULA"),
            weights=dict(get("SCORE_WEIGHTS")),
            path_bonus=float(get("SCORE_PATH_BONUS")),
            path_bonus_cap=float(get("SCORE_PATH_BONUS_CAP")),
            sum_cap=float(get("SCORE_SUM_CAP")),
            tier_high=float(get("SCORE_TIER_HIGH")),
            tier_medium=float(get("SCORE_TIER_MEDIUM")),
        )


def classify_evidence(evidence: Evidence, input_norm: str) -> tuple[str, float] | None:
    """Pfadtyp und Evidenzstärke (0–1) einer Evidenz; `input_norm` = normalisierte Eingabe."""
    via = evidence.via_term
    raw = max(float(evidence.raw_score), 0.0)
    if via.source == "gnd":
        path = GND_RELATION_PATHS.get(via.relation)
        if path is None or evidence.match_type not in (
            "register_exact",
            "register_gnd_id",
        ):
            return None
        return path, 1.0
    if via.source == "llm":
        if evidence.match_type in ("register_exact", "register_gnd_id"):
            return "llm_verified", 1.0
        if evidence.match_type == "vector":
            return "llm_verified", min(raw, 1.0)
        return None
    if via.source == "input":
        if evidence.match_type == "label_fts":
            return "label_fts", 1.0
        if evidence.match_type == "vector":
            return "vector", min(raw, 1.0)
        return None
    if via.source == "vector" and evidence.match_type == "register_exact":
        # Registerbegriff, per Embedding zu einem Suchbegriff gefunden
        if normalize_query(via.parent_term or "") != input_norm:
            # Suchbegriff war ein geprüfter LLM-Begriff
            return "llm_verified", min(raw, 1.0)
        if normalize_query(via.term) == input_norm:
            return "register_exact", 1.0
        return "register_similar", min(raw, 1.0)
    return None


def path_strengths(evidences: Iterable[Evidence], input_norm: str) -> dict[str, float]:
    """Stärkste Evidenz je Pfadtyp."""
    strengths: dict[str, float] = {}
    for evidence in evidences:
        classified = classify_evidence(evidence, input_norm)
        if classified is None:
            continue
        path, strength = classified
        if strength > strengths.get(path, -1.0):
            strengths[path] = strength
    return strengths


def count_path_types(strengths: Mapping[str, float]) -> int:
    """Zahl der unabhängigen Pfadtypen mit Evidenz (Vektor nur mit echter Vektor-Evidenz)."""
    return sum(1 for value in strengths.values() if value > 0)


def combine(
    formula: str,
    strengths: Mapping[str, float],
    cosine: float,
    settings: ScoringSettings,
) -> tuple[float, dict[str, float]]:
    """Score und Bestandteile für "sum", "max_plus" und "sum_capped".

    Die Summe der Bestandteile ist exakt der Score.
    """
    if formula not in NEW_FORMULAS:
        raise ValueError(f"combine kennt die Formel {formula!r} nicht")
    contributions = {
        path: settings.weight(path) * strengths[path]
        for path in NON_VECTOR_PATHS
        if strengths.get(path, 0.0) > 0
    }
    components: dict[str, float] = {}
    if formula == "sum":
        components.update(contributions)
    elif formula == "sum_capped":
        total = sum(contributions.values())
        factor = settings.sum_cap / total if total > settings.sum_cap else 1.0
        components.update({p: c * factor for p, c in contributions.items()})
    else:  # max_plus
        if contributions:
            best = max(contributions.values())
            path = next(p for p in NON_VECTOR_PATHS if contributions.get(p) == best)
            components[path] = best
        n_types = count_path_types(strengths)
        bonus = min(settings.path_bonus * max(n_types - 1, 0), settings.path_bonus_cap)
        if bonus > 0:
            components["path_bonus"] = bonus
    components["vector"] = settings.weight("vector") * max(float(cosine), 0.0)
    score = sum(components.values())
    return score, components


def legacy_components(
    cosine: float,
    in_keyword: bool,
    in_register: bool,
    gnd_relations: Iterable[str] | None,
    in_llm: bool,
) -> dict[str, float]:
    """Bestandteile der Formel „legacy“ (Stand P4). Score = min(Summe, 1.0)."""
    relations = set(gnd_relations or ())
    if not relations:
        gnd_bonus = 0.0
    elif "synonym" in relations:
        gnd_bonus = GND_SYNONYM_BONUS
    else:
        gnd_bonus = GND_RELATION_BONUS
    return {
        "cosine": cosine,
        "keyword_bonus": KEYWORD_BONUS if in_keyword else 0.0,
        "register_bonus": TERM_MATCH_BONUS if in_register else 0.0,
        "gnd_bonus": gnd_bonus,
        "llm_bonus": LLM_VERIFIED_BONUS if in_llm else 0.0,
    }


def legacy_score(components: Mapping[str, float]) -> float:
    """Wie vor P5: Summe in derselben Reihenfolge wie früher, auf 1.0 gedeckelt."""
    score = components["cosine"]
    if components["keyword_bonus"]:
        score += components["keyword_bonus"]
    if components["register_bonus"]:
        score += components["register_bonus"]
    score += components["gnd_bonus"]
    if components["llm_bonus"]:
        score += components["llm_bonus"]
    return min(score, 1.0)


def tier_for(score: float, settings: ScoringSettings) -> str:
    """Stufe „hoch“, „mittel“ oder „niedrig“ – kein Prozentwert, keine Wahrscheinlichkeit."""
    if score >= settings.tier_high:
        return "hoch"
    if score >= settings.tier_medium:
        return "mittel"
    return "niedrig"
