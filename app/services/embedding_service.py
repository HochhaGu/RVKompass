"""Lädt die vorab berechneten RVK-Embeddings einmal pro Prozess und beantwortet
Ähnlichkeitssuchen zur Laufzeit per Cosine-Similarity (Dot-Product auf normalisierten Vektoren).

Modell und Vektor-Matrizen werden NICHT pro Request neu geladen/berechnet - siehe
scripts/build_embeddings.py für die einmalige Offline-Berechnung nach jedem Sync.
"""

from __future__ import annotations

import json

import numpy as np
from sentence_transformers import SentenceTransformer

# Empirisch ermittelt (paraphrase-multilingual-MiniLM-L12-v2): thematisch passende Anfragen
# liegen meist bei Cosine-Similarity >= 0.7, Zufallsstrings/Tippfehler landen durch die
# Subword-Tokenisierung oft noch bei ~0.5-0.65. Kein scharfer Cutoff, aber filtert die
# eindeutigsten Nicht-Treffer heraus, statt bei jeder Anfrage selbstbewusst wirkende
# Top-K-Ergebnisse ohne inhaltlichen Bezug zu zeigen.
MIN_COSINE_SCORE = 0.5


class EmbeddingService:
    def __init__(
        self,
        model_name: str,
        embeddings_path: str,
        embeddings_meta_path: str,
        register_term_embeddings_path: str,
        register_term_meta_path: str,
    ):
        self.model = SentenceTransformer(model_name)

        self.notation_embeddings = np.load(embeddings_path)
        with open(embeddings_meta_path) as f:
            notation_meta = json.loads(f.read())
        self.notation_ids = notation_meta["notation_ids"]
        self._notation_index_by_id = {nid: i for i, nid in enumerate(self.notation_ids)}

        self.term_embeddings = np.load(register_term_embeddings_path)
        with open(register_term_meta_path) as f:
            term_meta = json.loads(f.read())
        self.terms = term_meta["terms"]

    def embed_query(self, text: str) -> np.ndarray:
        return self.model.encode(
            text, convert_to_numpy=True, normalize_embeddings=True
        ).astype(np.float32)

    def search_notations(
        self,
        query_vec: np.ndarray,
        top_k: int = 50,
        min_score: float = MIN_COSINE_SCORE,
    ) -> list[tuple[int, float]]:
        # np.errstate: Apples Accelerate-BLAS wirft bei großen float32-Matmuls auf macOS
        # bekannte Spurious-Warnings (divide by zero/overflow/invalid value), obwohl die
        # Ergebniswerte nachweislich weder NaN noch Inf enthalten (siehe Verifikation im Plan).
        with np.errstate(all="ignore"):
            scores = self.notation_embeddings @ query_vec
        top_indices = np.argpartition(-scores, min(top_k, len(scores) - 1))[:top_k]
        top_indices = top_indices[np.argsort(-scores[top_indices])]
        return [
            (self.notation_ids[i], float(scores[i]))
            for i in top_indices
            if scores[i] >= min_score
        ]

    def search_register_terms(
        self,
        query_vec: np.ndarray,
        top_k: int = 20,
        min_score: float = MIN_COSINE_SCORE,
    ) -> list[tuple[str, float]]:
        with np.errstate(all="ignore"):
            scores = self.term_embeddings @ query_vec
        top_indices = np.argpartition(-scores, min(top_k, len(scores) - 1))[:top_k]
        top_indices = top_indices[np.argsort(-scores[top_indices])]
        return [
            (self.terms[i], float(scores[i]))
            for i in top_indices
            if scores[i] >= min_score
        ]

    def score_for_notation(
        self, query_vec: np.ndarray, notation_id: int
    ) -> float | None:
        """Cosine-Similarity für eine einzelne Notation, unabhängig von Top-K-Cutoffs.

        Wird für Treffer benötigt, die nur über die Keyword-Suche gefunden wurden und daher
        nicht zwangsläufig unter den Top-K der Vektorsuche waren, aber trotzdem eine
        Konfidenzanzeige in der Ergebnistabelle bekommen sollen.
        """
        index = self._notation_index_by_id.get(notation_id)
        if index is None:
            return None
        with np.errstate(all="ignore"):
            return float(self.notation_embeddings[index] @ query_vec)


_instance: EmbeddingService | None = None


def init_app(app) -> None:
    global _instance
    _instance = EmbeddingService(
        model_name=app.config["EMBEDDING_MODEL_NAME"],
        embeddings_path=app.config["EMBEDDINGS_PATH"],
        embeddings_meta_path=app.config["EMBEDDINGS_META_PATH"],
        register_term_embeddings_path=app.config["REGISTER_TERM_EMBEDDINGS_PATH"],
        register_term_meta_path=app.config["REGISTER_TERM_META_PATH"],
    )


def get_service() -> EmbeddingService:
    if _instance is None:
        raise RuntimeError(
            "EmbeddingService wurde nicht initialisiert - init_app(app) aufrufen."
        )
    return _instance
