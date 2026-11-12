from flask import Blueprint, render_template

from app.services import scoring

bp = Blueprint("help", __name__)


@bp.get("/hilfe")
def hilfe():
    return render_template("hilfe.html", path_labels=scoring.PATH_LABELS)
