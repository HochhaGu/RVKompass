from flask import Blueprint, current_app, render_template, request

from app import limiter
from app.services import (
    embedding_service,
    presentation,
    ranking,
    rvk_repository,
    usage_stats,
)
from app.services.expansion import gnd, llm

bp = Blueprint("search", __name__)


def selected_main_groups(form, config) -> list[str]:
    """Gewählte RVK-Hauptgruppen: Feld `hg` (mehrfach) und altes Feld `fach` (kommagetrennt).

    Das Startformular schickt `hg_form=1` mit; dann gilt genau seine Auswahl (auch „keine“).
    Fehlt das Feld und ist nichts gewählt, gilt die Voreinstellung DEFAULT_MAIN_GROUPS.
    """
    groups = presentation.parse_main_groups([*form.getlist("hg"), form.get("fach", "")])
    if not groups and not form.get("hg_form"):
        groups = list(config.get("DEFAULT_MAIN_GROUPS", ()) or ())
    return groups


@bp.get("/")
def index():
    selected = list(current_app.config.get("DEFAULT_MAIN_GROUPS", ()) or ())
    groups: list[tuple[str, str]] = []
    conn = rvk_repository.get_readonly_connection(current_app.config["DATABASE_PATH"])
    if conn is not None:
        try:
            groups = rvk_repository.list_main_groups(conn)
        finally:
            conn.close()
    return render_template(
        "search.html",
        main_group_options=presentation.main_group_options(groups, selected),
        selected_groups=selected,
    )


@bp.post("/search")
@limiter.limit("20 per minute")
def search():
    query = request.form.get("q", "").strip()
    config = current_app.config
    selected = selected_main_groups(request.form, config)
    if not query:
        return render_template(
            "_results_table.html", results=[], query=query, selected_groups=selected
        )

    conn = rvk_repository.get_connection(config["DATABASE_PATH"])
    source = None
    llm_stats: dict = {}
    try:
        # Eine GND-Quelle für GND-Erweiterung und Prüfung der LLM-Vorschläge zusammen
        source = gnd.build_gnd_source(conn, config)
        all_senses = gnd.gnd_senses_for_query(conn, config, query, source=source)

        # P4: Das Sprachmodell schlägt vor; gesucht wird nur, was im Register oder in der GND steht
        llm_source = source if config.get("ENABLE_GND_EXPANSION") else None
        suggestions = llm.llm_suggestions_for_query(
            conn, config, query, source=llm_source, stats=llm_stats
        )
        all_llm_terms = llm.verified_terms(suggestions, query)

        # P6: Abwahl im Erweiterungsbaum. Der Baum schickt baum=1 und die Schlüssel der
        # angehakten Begriffe; abgewählt ist, was im Baum steht, aber nicht mitkommt.
        excluded: set[str] = set()
        if request.form.get("baum") == "1":
            excluded = presentation.tree_keys(all_senses, suggestions) - set(
                request.form.getlist("begriff")
            )
        gnd_senses, llm_terms = presentation.apply_exclusions(
            all_senses, all_llm_terms, excluded
        )

        service = embedding_service.get_service()
        result = ranking.search_result(
            conn,
            service,
            query,
            llm_terms,
            gnd_senses=gnd_senses,
            subject_filter=selected,
            main_group_filter=selected,
        )
        results = ranking.hits_to_rows(
            conn, result.hits, query=query, senses=all_senses
        )
        senses = ranking.senses_to_rows(conn, result.senses)
        labels = rvk_repository.get_main_group_labels(
            conn, {row["main_group"] for row in results}
        )
    finally:
        close_source = getattr(source, "close", None)
        if callable(close_source):
            close_source()
        conn.close()

    # P8: anonyme Tageszähler – keine IP, kein Suchtext (app/services/usage_stats.py)
    usage_path = config.get("USAGE_DATABASE_PATH")
    if usage_path:
        usage_stats.record_search(usage_path, len(results))
        usage_stats.record_cache_hits(
            usage_path,
            gnd=int(getattr(source, "cache_hits", 0) or 0),
            llm=int(llm_stats.get("cache_hits", 0) or 0),
        )

    blocks = presentation.assign_to_senses(results, result.senses, senses, labels)
    return render_template(
        "_results_table.html",
        results=results,
        query=query,
        tree=presentation.expansion_tree(query, all_senses, suggestions, excluded),
        senses=senses,
        blocks=blocks,
        groups=[] if blocks else presentation.group_by_main_group(results, labels),
        filtered_out=getattr(result, "filtered_out", 0),
        selected_groups=selected,
    )
