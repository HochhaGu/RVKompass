from flask import Blueprint, abort, current_app, request

bp = Blueprint("internal", __name__)


@bp.post("/internal/sync")
def sync():
    token = request.args.get("token", "")
    if not current_app.config["SYNC_TOKEN"] or token != current_app.config["SYNC_TOKEN"]:
        abort(403)
    return {"status": "not_implemented"}, 501
