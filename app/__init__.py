import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

from app.config import get_config
from app.services import log_privacy

limiter = Limiter(key_func=get_remote_address, default_limits=["60 per hour"])


def _configure_logging(app: Flask) -> None:
    log_dir = Path(app.root_path).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "rvkompass.log", maxBytes=1_000_000, backupCount=3
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG if app.config["DEBUG"] else logging.INFO)
    # Keine Suchbegriffe, keine IP-Adressen in Logs (auch nicht im Container-Log über stderr)
    log_privacy.install(app.logger)


def read_rvk_version(db_path: str | None) -> str | None:
    """RVK-Stand für den Footer, nur lesend; bei fehlender Datenbank oder Fehlern None."""
    from app.services import rvk_repository

    if not db_path:
        return None
    conn = rvk_repository.get_readonly_connection(db_path)
    if conn is None:
        return None
    try:
        return rvk_repository.latest_rvk_version(conn)
    finally:
        conn.close()


def register_template_context(app: Flask) -> None:
    """Stellt allen Templates `rvk_version` bereit (Footer „RVK-Stand: …“)."""

    @app.context_processor
    def inject_rvk_version():
        return {"rvk_version": read_rvk_version(app.config.get("DATABASE_PATH"))}


def apply_proxy_fix(app: Flask) -> None:
    """Hinter Caddy: Client-Adresse aus X-Forwarded-For (nur für das Rate-Limit, nie gespeichert).

    Ohne das kämen alle Anfragen von 127.0.0.1 und teilten sich ein Rate-Limit. Nur einschalten,
    wenn der Port ausschließlich vom Reverse Proxy erreichbar ist (docker-compose: 127.0.0.1).
    """
    hops = int(app.config.get("PROXY_FIX_X_FOR", 0) or 0)
    if hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())
    apply_proxy_fix(app)

    _configure_logging(app)
    limiter.init_app(app)

    from app.services import embedding_service

    embedding_service.init_app(app)

    from app.routes.health import bp as health_bp
    from app.routes.help import bp as help_bp
    from app.routes.internal import bp as internal_bp
    from app.routes.legal import bp as legal_bp
    from app.routes.search import bp as search_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(help_bp)
    register_template_context(app)

    return app
