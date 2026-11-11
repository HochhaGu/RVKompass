import os
from pathlib import Path
from typing import ClassVar

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    DATABASE_PATH = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "data" / "rvk.sqlite3")
    )
    EMBEDDINGS_PATH = os.environ.get(
        "EMBEDDINGS_PATH", str(BASE_DIR / "data" / "embeddings.npy")
    )
    EMBEDDINGS_META_PATH = os.environ.get(
        "EMBEDDINGS_META_PATH", str(BASE_DIR / "data" / "embeddings_meta.json")
    )
    REGISTER_TERM_EMBEDDINGS_PATH = os.environ.get(
        "REGISTER_TERM_EMBEDDINGS_PATH",
        str(BASE_DIR / "data" / "register_term_embeddings.npy"),
    )
    REGISTER_TERM_META_PATH = os.environ.get(
        "REGISTER_TERM_META_PATH", str(BASE_DIR / "data" / "register_term_meta.json")
    )
    EMBEDDING_MODEL_NAME = os.environ.get(
        "EMBEDDING_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2"
    )
    CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
    SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
    ENABLE_QUERY_EXPANSION = (
        os.environ.get("ENABLE_QUERY_EXPANSION", "true").lower() == "true"
    )
    CLAUDE_DAILY_LIMIT = int(os.environ.get("CLAUDE_DAILY_LIMIT", "500"))

    # GND-Erweiterung (P3): lobid-gnd live mit 30-Tage-Cache, ab P8 lokaler Abzug.
    ENABLE_GND_EXPANSION = (
        os.environ.get("ENABLE_GND_EXPANSION", "true").lower() == "true"
    )
    GND_API_URL = "https://lobid.org/gnd/search"
    GND_TIMEOUT_SECONDS = 4
    GND_CACHE_DAYS = 30
    GND_SEARCH_SIZE = 10
    GND_MAX_SENSES = 5
    GND_NARROWER_LIMIT = 10

    # GND-Sachgruppe (DNB-Vokabular gnd-sc) -> RVK-Hauptgruppen, für die Rangfolge der Bedeutungen
    # beim Fachfilter. Ein Schlüssel mit "*" am Ende gilt für alle Codes, die so beginnen.
    # BITTE PRÜFEN (Startbelegung P3): Die Sachgruppen-Codes sind an der DNB-Liste geprüft. Die
    # RVK-Hauptgruppen stammen aus eval/goldstandard.csv (Spalte fachgebiet -> erwartete
    # Notationen), weil die Datenbank in diesem Lauf nicht abgefragt werden konnte. Nicht
    # eindeutige Fälle (z. B. 31.8a Fertigungstechnik: im Goldstandard teils ZL, teils ZM) fehlen.
    GND_SUBJECT_CATEGORY_TO_RVK: ClassVar[dict[str, tuple[str, ...]]] = {
        # Informatik -> Goldstandard „Informatik“: ST
        "30": ("ST",),
        # Werkstoffkunde -> Goldstandard „Werkstoffe“: ZM
        "31.1d": ("ZM",),
        # Bautechnik -> Goldstandard „Bauwesen“: ZI, ZH
        "31.3b": ("ZI", "ZH"),
        # Energietechnik -> Goldstandard „Energietechnik“: überwiegend ZP
        "31.5": ("ZP",),
        # Maschinenbau -> Goldstandard „Maschinenbau“: überwiegend ZL
        "31.6": ("ZL",),
        # Betriebswirtschaftslehre (alle Untergruppen 10.11...) -> Goldstandard „Wirtschaft“: QP
        "10.11*": ("QP",),
    }
    DEBUG = False


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False


def get_config():
    env = os.environ.get("FLASK_ENV", "production")
    return DevConfig if env == "development" else ProdConfig
