import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

from app.config import get_config

limiter = Limiter(key_func=get_remote_address, default_limits=["60 per hour"])


def _configure_logging(app: Flask) -> None:
    log_dir = Path(app.root_path).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "rvkompass.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG if app.config["DEBUG"] else logging.INFO)


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    _configure_logging(app)
    limiter.init_app(app)

    from app.services import embedding_service

    embedding_service.init_app(app)

    from app.routes.search import bp as search_bp
    from app.routes.internal import bp as internal_bp
    from app.routes.legal import bp as legal_bp

    app.register_blueprint(search_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(legal_bp)

    return app
