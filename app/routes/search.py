from flask import Blueprint, current_app, render_template, request

from app import limiter
from app.services import embedding_service, query_expansion, ranking, rvk_repository
from app.services.expansion import gnd

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

        gnd_senses = gnd.gnd_senses_for_query(conn, current_app.config, query)
        # Fachfilter als RVK-Hauptgruppen, kommagetrennt (z. B. "ZL,ZM"); Auswahlfeld folgt in P5.
        subject_filter = [
            part.strip().upper()
            for part in request.form.get("fach", "").split(",")
            if part.strip()
        ]

        service = embedding_service.get_service()
        result = ranking.search_result(
            conn,
            service,
            query,
            expanded_terms,
            gnd_senses=gnd_senses,
            subject_filter=subject_filter,
        )
        results = ranking.hits_to_rows(conn, result.hits)
        senses = ranking.senses_to_rows(conn, result.senses)
    finally:
        conn.close()

    return render_template(
        "_results_table.html",
        results=results,
        query=query,
        expanded_terms=expanded_terms,
        senses=senses,
    )
