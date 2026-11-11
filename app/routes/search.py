from flask import Blueprint, current_app, render_template, request

from app import limiter
from app.services import embedding_service, ranking, rvk_repository
from app.services.expansion import gnd, llm

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
        config = current_app.config
        # Eine GND-Quelle für GND-Erweiterung und Prüfung der LLM-Vorschläge zusammen
        source = gnd.build_gnd_source(conn, config)
        gnd_senses = gnd.gnd_senses_for_query(conn, config, query, source=source)

        # P4: Das Sprachmodell schlägt vor; gesucht wird nur, was im Register oder in der GND steht
        llm_source = source if config.get("ENABLE_GND_EXPANSION") else None
        suggestions = llm.llm_suggestions_for_query(
            conn, config, query, source=llm_source
        )
        llm_terms = llm.verified_terms(suggestions, query)

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
            llm_terms,
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
        llm_suggestions=llm.suggestions_to_rows(suggestions),
        senses=senses,
    )
