import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "data" / "rvk.sqlite3"))
    EMBEDDINGS_PATH = os.environ.get("EMBEDDINGS_PATH", str(BASE_DIR / "data" / "embeddings.npy"))
    EMBEDDINGS_META_PATH = os.environ.get(
        "EMBEDDINGS_META_PATH", str(BASE_DIR / "data" / "embeddings_meta.json")
    )
    REGISTER_TERM_EMBEDDINGS_PATH = os.environ.get(
        "REGISTER_TERM_EMBEDDINGS_PATH", str(BASE_DIR / "data" / "register_term_embeddings.npy")
    )
    REGISTER_TERM_META_PATH = os.environ.get(
        "REGISTER_TERM_META_PATH", str(BASE_DIR / "data" / "register_term_meta.json")
    )
    EMBEDDING_MODEL_NAME = os.environ.get(
        "EMBEDDING_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2"
    )
    CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
    SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
    ENABLE_QUERY_EXPANSION = os.environ.get("ENABLE_QUERY_EXPANSION", "true").lower() == "true"
    CLAUDE_DAILY_LIMIT = int(os.environ.get("CLAUDE_DAILY_LIMIT", "500"))
    DEBUG = False


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False


def get_config():
    env = os.environ.get("FLASK_ENV", "production")
    return DevConfig if env == "development" else ProdConfig
