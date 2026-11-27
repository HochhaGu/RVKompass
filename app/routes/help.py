from flask import Blueprint, current_app, render_template

from app.config import Config
from app.services import scoring

bp = Blueprint("help", __name__)


@bp.get("/hilfe")
def hilfe():
    # P11: Sätze über das Sprachmodell nur, wenn die LLM-Erweiterung eingeschaltet ist
    return render_template(
        "hilfe.html",
        path_labels=scoring.PATH_LABELS,
        llm_enabled=bool(current_app.config.get("ENABLE_QUERY_EXPANSION")),
        max_results=current_app.config.get("MAX_RESULTS", Config.MAX_RESULTS),
    )
