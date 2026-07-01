from flask import Blueprint, current_app, render_template, request

from app import limiter
from app.services import embedding_service, query_expansion, ranking, rvk_repository

bp = Blueprint("search", __name__)


@bp.get("/")
def index():
    return render_template("search.html")


@bp.post("/search")
@limiter.limit("20 per minute")
def search():
    query = request.form.get("q", "").strip()
    if not query:
        return render_template("_results_table.html", results=[], query=query)

    conn = rvk_repository.get_connection(current_app.config["DATABASE_PATH"])
    try:
        expanded_terms = []
        if current_app.config["ENABLE_QUERY_EXPANSION"]:
            expanded_terms = query_expansion.expand_query(
                conn,
                current_app.config["CLAUDE_API_KEY"],
                query,
                daily_limit=current_app.config["CLAUDE_DAILY_LIMIT"],
            )

        service = embedding_service.get_service()
        results = ranking.search(conn, service, query, expanded_terms)
    finally:
        conn.close()

    return render_template(
        "_results_table.html", results=results, query=query, expanded_terms=expanded_terms
    )
