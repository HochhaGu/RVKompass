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

    Die Facette schickt `hg_form=1` mit; dann gilt genau ihre Auswahl (auch „keine“).
    Fehlt das Feld und ist nichts gewählt, gilt die Voreinstellung DEFAULT_MAIN_GROUPS.
    """
    groups = presentation.parse_main_groups([*form.getlist("hg"), form.get("fach", "")])
    if not groups and not form.get("hg_form"):
        groups = list(config.get("DEFAULT_MAIN_GROUPS", ()) or ())
    return groups


def excluded_keys(form, query: str, all_keys: set[str]) -> set[str]:
    """Abgewählte Erweiterungsbegriffe (P6/P10).

    Die Chip-Zeile schickt `baum=1`, `chips_q` (die Suche, zu der sie gehört) und je gesuchtem
    Begriff `begriff=<Schlüssel>`; abgewählt ist, was zur Erweiterung gehört, aber nicht
    mitkommt. Dazu kommen `entfernen` (×-Knopf) und, wieder hinzugefügt, `zurueck`.
    Gehört die Chip-Zeile zu einer anderen Suche, gilt nichts als abgewählt.
    """
    if form.get("baum") != "1":
        return set()
    chips_query = form.get("chips_q")
    if chips_query is not None and chips_query.strip() != query:
        return set()
    excluded = all_keys - set(form.getlist("begriff"))
    excluded |= set(form.getlist("entfernen")) & all_keys
    excluded -= set(form.getlist("zurueck"))
    return excluded


def _main_groups(db_path: str) -> list[tuple[str, str]]:
    conn = rvk_repository.get_readonly_connection(db_path)
    if conn is None:
        return []
    try:
        return rvk_repository.list_main_groups(conn)
    finally:
        conn.close()


@bp.get("/")
def index():
    selected = list(current_app.config.get("DEFAULT_MAIN_GROUPS", ()) or ())
    groups = _main_groups(current_app.config["DATABASE_PATH"])
    # P10: /?q=… (Verweis-Link ohne JavaScript, neuer Tab) füllt das Feld und sucht sofort
    initial_query = request.args.get("q", "").strip()
    return render_template(
        "search.html",
        main_group_options=presentation.main_group_options(groups, selected),
        selected_groups=selected,
        initial_query=initial_query,
    )


@bp.post("/search")
@limiter.limit("20 per minute")
def search():
    form = request.form
    # Der ×-Knopf eines Chips schickt nur die Chip-Zeile mit (Suchbegriff in `chips_q`)
    query = (form.get("q") if "q" in form else form.get("chips_q", "")).strip()
    config = current_app.config
    selected = selected_main_groups(form, config)
    if not query:
        groups = _main_groups(config["DATABASE_PATH"])
        return render_template(
            "_results_table.html",
            results=[],
            query=query,
            selected_groups=selected,
            facet_options=presentation.main_group_options(groups, selected),
        )

    conn = rvk_repository.get_connection(config["DATABASE_PATH"])
    source = None
    llm_stats: dict = {}
    entered = None
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

        # P6/P10: Abwahl über die Chip-Zeile
        excluded = excluded_keys(
            form, query, presentation.tree_keys(all_senses, suggestions)
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
        groups = rvk_repository.list_main_groups(conn)

        # P10: Eingabe ist selbst eine Notation („ZL 4620“) – Karte getrennt von den Vorschlägen
        code = presentation.parse_notation_query(query)
        notation = rvk_repository.get_notation_by_code(conn, code) if code else None
        if notation is not None:
            entered = presentation.entered_notation(
                notation, ranking.build_rvk_online_url(notation.notation)
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
            gnd_lookups=int(getattr(source, "lookups", 0) or 0),
            llm_lookups=int(llm_stats.get("cache_hits", 0) or 0)
            + int(llm_stats.get("api_calls", 0) or 0),
        )

    counts = dict(getattr(result, "main_group_counts", {}) or {})
    chips = presentation.expansion_chips(query, all_senses, suggestions, excluded)
    blocks = presentation.assign_to_senses(results, result.senses, senses, labels)
    return render_template(
        "_results_table.html",
        results=results,
        query=query,
        chips=chips,
        senses=senses,
        blocks=blocks,
        groups=[] if blocks else presentation.group_by_main_group(results, labels),
        filtered_out=getattr(result, "filtered_out", 0),
        selected_groups=selected,
        facet_options=presentation.main_group_options(groups, selected, counts),
        empty=presentation.empty_state(query, selected, counts, len(chips["removed"])),
        entered=entered,
    )
