"""Berechnet Embeddings für den RVK-Bestand offline (nach jedem scripts/sync_rvk.py-Lauf).

Zwei Artefakte werden erzeugt:
- data/embeddings.npy + embeddings_meta.json: ein Vektor pro Notation (Text aus
  app.models.SCHEMA_SQL/search_index: Label + Hierarchie-Pfad + Registerbegriffe).
- data/register_term_embeddings.npy + register_term_meta.json: ein Vektor pro
  EINDEUTIGEM Registerbegriff (nicht pro Zeile in register_entry - von 642k Zeilen sind
  nur ca. 40k Begriffe tatsächlich unterschiedlich, siehe Konsolen-Output des Sync-Laufs).
  Das erlaubt gezielte Treffer auf Registerbegriffs-Ebene, ohne redundante Encodes derselben
  Zeichenkette.

Beide Matrizen werden L2-normalisiert gespeichert, damit die Laufzeitsuche Cosine-Similarity
per einfacher Matrixmultiplikation (Dot-Product) berechnen kann (siehe embedding_service.py).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_config

BATCH_SIZE = 256


def load_notation_texts(conn: sqlite3.Connection) -> tuple[list[int], list[str]]:
    rows = conn.execute("SELECT notation_id, text FROM search_index ORDER BY notation_id").fetchall()
    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    return ids, texts


def load_distinct_register_terms(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT term FROM register_entry ORDER BY term").fetchall()
    return [r[0] for r in rows]


def encode_and_save(model: SentenceTransformer, texts: list[str], label: str) -> np.ndarray:
    print(f"Encoding {len(texts)} Texte ({label}) ...")
    start = time.time()
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)
    print(f"  ... fertig in {time.time() - start:.1f}s")
    return embeddings


def main():
    config = get_config()
    conn = sqlite3.connect(config.DATABASE_PATH)

    model_name = config.EMBEDDING_MODEL_NAME
    print(f"Lade Modell {model_name} ...")
    model = SentenceTransformer(model_name)
    dim = model.get_sentence_embedding_dimension()

    notation_ids, notation_texts = load_notation_texts(conn)
    notation_embeddings = encode_and_save(model, notation_texts, "Notationen")
    np.save(config.EMBEDDINGS_PATH, notation_embeddings)
    Path(config.EMBEDDINGS_META_PATH).write_text(
        json.dumps(
            {
                "model": model_name,
                "dim": dim,
                "created_at": datetime.now(UTC).isoformat(),
                "notation_ids": notation_ids,
            }
        )
    )
    print(f"Gespeichert: {config.EMBEDDINGS_PATH} ({notation_embeddings.shape})")

    terms = load_distinct_register_terms(conn)
    term_embeddings = encode_and_save(model, terms, "eindeutige Registerbegriffe")
    np.save(config.REGISTER_TERM_EMBEDDINGS_PATH, term_embeddings)
    Path(config.REGISTER_TERM_META_PATH).write_text(
        json.dumps(
            {
                "model": model_name,
                "dim": dim,
                "created_at": datetime.now(UTC).isoformat(),
                "terms": terms,
            }
        )
    )
    print(f"Gespeichert: {config.REGISTER_TERM_EMBEDDINGS_PATH} ({term_embeddings.shape})")

    conn.close()


if __name__ == "__main__":
    main()
