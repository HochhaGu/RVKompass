"""Lädt die vorab berechneten RVK-Embeddings einmal pro Prozess und beantwortet
Ähnlichkeitssuchen zur Laufzeit per Cosine-Similarity (Dot-Product auf normalisierten Vektoren).

Modell und Vektor-Matrizen werden NICHT pro Request neu geladen/berechnet - siehe
scripts/build_embeddings.py für die einmalige Offline-Berechnung nach jedem Sync.
"""

from __future__ import annotations

import json

import numpy as np
from sentence_transformers import SentenceTransformer


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
        notation_meta = json.loads(open(embeddings_meta_path).read())
        self.notation_ids = notation_meta["notation_ids"]

        self.term_embeddings = np.load(register_term_embeddings_path)
        term_meta = json.loads(open(register_term_meta_path).read())
        self.terms = term_meta["terms"]

    def embed_query(self, text: str) -> np.ndarray:
        return self.model.encode(text, convert_to_numpy=True, normalize_embeddings=True).astype(
            np.float32
        )

    def search_notations(self, query_vec: np.ndarray, top_k: int = 50) -> list[tuple[int, float]]:
        scores = self.notation_embeddings @ query_vec
        top_indices = np.argpartition(-scores, min(top_k, len(scores) - 1))[:top_k]
        top_indices = top_indices[np.argsort(-scores[top_indices])]
        return [(self.notation_ids[i], float(scores[i])) for i in top_indices]

    def search_register_terms(self, query_vec: np.ndarray, top_k: int = 20) -> list[tuple[str, float]]:
        scores = self.term_embeddings @ query_vec
        top_indices = np.argpartition(-scores, min(top_k, len(scores) - 1))[:top_k]
        top_indices = top_indices[np.argsort(-scores[top_indices])]
        return [(self.terms[i], float(scores[i])) for i in top_indices]


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
        raise RuntimeError("EmbeddingService wurde nicht initialisiert - init_app(app) aufrufen.")
    return _instance
