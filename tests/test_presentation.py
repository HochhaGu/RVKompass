"""Aufbereitung für die Oberfläche (P6) – reine Funktionen, ohne Netz, ohne Datenbank."""

import pytest

from app.models import Evidence, ExpansionTerm, GndSense, Hit, LlmSuggestion, Notation
from app.services import evaluation, presentation, ranking, scoring

QUERY = "Wärmepumpe"
SENSE_NAME = "Wärmepumpe"
SENSE_ID = "4064903-9"

INPUT = ExpansionTerm(QUERY, "input", "direct")
SYNONYM = ExpansionTerm(SENSE_NAME, "gnd", "synonym", SENSE_ID, SENSE_NAME)
VARIANT = ExpansionTerm("Heizpumpe", "gnd", "synonym", SENSE_ID, SENSE_NAME)
BROADER = ExpansionTerm("Heiztechnik", "gnd", "broader", "4024356-4", SENSE_NAME)
NARROWER = ExpansionTerm("Erdwärmepumpe", "gnd", "narrower", "4200000-1", SENSE_NAME)
RELATED = ExpansionTerm("Kältetechnik", "gnd", "related", "4030000-1", SENSE_NAME)
LLM = ExpansionTerm("Kältemittel", "llm", "llm_verified", None, QUERY)

SENSE = GndSense(
    gnd_id=SENSE_ID,
    preferred_name=SENSE_NAME,
    subject_categories=(("31.5", "Energietechnik"),),
    terms=(SYNONYM, VARIANT, BROADER, NARROWER, RELATED),
)


def _labels(steps):
    return [(s["label"], s["term"]) for s in steps]


def _chain(term, match_type="register_exact", raw=1.0):
    return presentation.evidence_chain(
        Evidence(7, term, match_type, raw), QUERY, "ZP 4000"
    )


def test_main_group_moved_and_still_available_in_evaluation():
    assert presentation.main_group("ZN 8330") == "ZN"
    assert presentation.main_group(" zn  8330") == "ZN"
    assert presentation.main_group("1234") == ""
    assert evaluation.main_group is presentation.main_group


def test_lobid_url():
    assert presentation.lobid_url("4123456-7") == "https://lobid.org/gnd/4123456-7"
    assert presentation.lobid_url(None) is None
    assert presentation.lobid_url("") is None


def test_chain_fulltext():
    assert _labels(_chain(INPUT, "label_fts")) == [
        ("Eingabe", QUERY),
        ("Volltext-Treffer in Benennung oder Register von", "ZP 4000"),
    ]


def test_chain_vector():
    assert _labels(_chain(INPUT, "vector", 0.6)) == [
        ("Eingabe", QUERY),
        ("inhaltlich ähnlich (Vektor) zu", "ZP 4000"),
    ]


def test_chain_gnd_broader_like_in_the_task_example():
    steps = _chain(BROADER)
    assert _labels(steps) == [
        ("Eingabe", QUERY),
        ("GND-Begriff", SENSE_NAME),
        ("GND-Oberbegriff", "Heiztechnik"),
        ("Registereintrag an", "ZP 4000"),
    ]
    assert steps[2]["url"] == "https://lobid.org/gnd/4024356-4"


def test_chain_gnd_sense_link_from_mapping():
    steps = presentation.evidence_chain(
        Evidence(7, BROADER, "register_exact", 1.0),
        QUERY,
        "ZP 4000",
        {SENSE_NAME: SENSE_ID},
    )
    assert steps[1]["url"] == f"https://lobid.org/gnd/{SENSE_ID}"


@pytest.mark.parametrize(
    "term,label",
    [
        (NARROWER, "GND-Unterbegriff"),
        (RELATED, "GND-verwandt"),
        (VARIANT, "GND-Synonym"),
    ],
)
def test_chain_gnd_relations(term, label):
    labels = _labels(_chain(term, "register_gnd_id"))
    assert labels[2] == (label, term.term)
    assert labels[-1] == ("Registereintrag mit GND-Nummer an", "ZP 4000")


def test_chain_gnd_synonym_equal_to_sense_skips_middle_step():
    steps = _chain(SYNONYM)
    assert _labels(steps) == [
        ("Eingabe", QUERY),
        ("GND-Begriff", SENSE_NAME),
        ("Registereintrag an", "ZP 4000"),
    ]
    assert steps[1]["url"] == f"https://lobid.org/gnd/{SENSE_ID}"


def test_chain_llm():
    assert _labels(_chain(LLM)) == [
        ("Eingabe", QUERY),
        ("Vorschlag des Sprachmodells (im Normvokabular verifiziert)", "Kältemittel"),
        ("Registereintrag an", "ZP 4000"),
    ]
    assert _labels(_chain(LLM, "vector", 0.5))[-1] == (
        "inhaltlich ähnlich (Vektor) zu",
        "ZP 4000",
    )


def test_chain_similar_register_term_from_input():
    term = ExpansionTerm("Heizungspumpe", "vector", "embedding", None, QUERY)
    assert _labels(_chain(term, raw=0.8)) == [
        ("Eingabe", QUERY),
        ("ähnlicher Registerbegriff (Ähnlichkeit)", "Heizungspumpe"),
        ("Registereintrag an", "ZP 4000"),
    ]


def test_chain_similar_register_term_from_llm_term():
    term = ExpansionTerm("Kühlmittel", "vector", "embedding", None, "Kältemittel")
    assert _labels(_chain(term, raw=0.8)) == [
        ("Eingabe", QUERY),
        ("Vorschlag des Sprachmodells (im Normvokabular verifiziert)", "Kältemittel"),
        ("ähnlicher Registerbegriff (Ähnlichkeit)", "Kühlmittel"),
        ("Registereintrag an", "ZP 4000"),
    ]


def test_chain_register_term_equal_to_input():
    term = ExpansionTerm(QUERY, "vector", "embedding", None, QUERY)
    assert _labels(_chain(term))[1] == ("gleichlautender Registerbegriff", QUERY)


def test_is_vector_only():
    vec = Evidence(7, INPUT, "vector", 0.5)
    similar = Evidence(
        7,
        ExpansionTerm("Heizungspumpe", "vector", "embedding", None, QUERY),
        "register_exact",
        0.8,
    )
    same = Evidence(
        7,
        ExpansionTerm(QUERY, "vector", "embedding", None, QUERY),
        "register_exact",
        1.0,
    )
    register = Evidence(7, BROADER, "register_exact", 1.0)
    assert presentation.is_vector_only([vec], QUERY) is True
    assert presentation.is_vector_only([vec, similar], QUERY) is True
    assert presentation.is_vector_only([vec, register], QUERY) is False
    # gleichlautender Registerbegriff ist ein direkter Registertreffer
    assert presentation.is_vector_only([vec, same], QUERY) is False
    assert presentation.is_vector_only([], QUERY) is False


def _hit(evidences, notation="ZP 4000", nid=7, score=0.7):
    return Hit(
        Notation(nid, notation, "Wärmepumpen", None, None, None), evidences, score, {}
    )


def test_explain_hit_dedups_and_orders_by_path_type(monkeypatch):
    hit = _hit(
        [
            Evidence(7, INPUT, "vector", 0.6),
            Evidence(7, BROADER, "register_exact", 1.0),
            Evidence(7, BROADER, "register_exact", 1.0),
            Evidence(7, INPUT, "label_fts", -3.0),
            Evidence(7, SYNONYM, "register_gnd_id", 1.0),
        ]
    )
    explanation = presentation.explain_hit(hit, QUERY)
    assert [c["path_label"] for c in explanation["chains"]] == [
        "GND-Synonym",
        "GND-Oberbegriff",
        "Volltext",
        "Vektor",
    ]
    assert explanation["more"] == 0
    monkeypatch.setattr(presentation, "MAX_CHAINS", 2)
    explanation = presentation.explain_hit(hit, QUERY)
    assert len(explanation["shown"]) == 2 and explanation["more"] == 2


def _row(rank, notation, nid):
    return {"rank": rank, "notation": notation, "notation_id": nid}


def test_group_by_main_group_keeps_rank_and_order():
    rows = [_row(1, "ZN 1", 1), _row(2, "ZP 1", 2), _row(3, "ZN 2", 3)]
    groups = presentation.group_by_main_group(rows, {"ZN": "Elektrotechnik"})
    assert [(g["code"], g["label"], g["count"]) for g in groups] == [
        ("ZN", "Elektrotechnik", 2),
        ("ZP", "", 1),
    ]
    assert [r["rank"] for r in groups[0]["rows"]] == [1, 3]


def _sense(gnd_id, name):
    return GndSense(gnd_id=gnd_id, preferred_name=name, terms=())


def _sense_result(sense, ids, matches=False, hit_count=None):
    return ranking.SenseResult(
        sense=sense,
        notation_ids=ids,
        hit_count=len(ids) if hit_count is None else hit_count,
        matches_filter=matches,
    )


def test_assign_to_senses_each_row_once_and_rest_block():
    a, b = _sense("1", "Mutter <Familie>"), _sense("2", "Mutter <Technik>")
    rows = [_row(1, "ZL 1", 1), _row(2, "MS 1", 2), _row(3, "ZL 2", 3)]
    results = [_sense_result(a, [2, 1]), _sense_result(b, [1, 3])]
    blocks = presentation.assign_to_senses(rows, results)
    assert [b["name"] for b in blocks] == ["Mutter <Familie>", "Mutter <Technik>"]
    # ZL 1 gehört zu beiden Bedeutungen, kommt aber nur in die höherrangige
    assert [r["notation"] for r in blocks[0]["rows"]] == ["ZL 1", "MS 1"]
    assert [r["notation"] for r in blocks[1]["rows"]] == ["ZL 2"]
    assert blocks[0]["selected"] is True and blocks[1]["selected"] is False

    rows.append(_row(4, "AB 1", 4))
    blocks = presentation.assign_to_senses(rows, results)
    assert blocks[-1]["name"] == "Über die Eingabe gefunden"
    assert [r["notation"] for r in blocks[-1]["rows"]] == ["AB 1"]


def test_assign_to_senses_opens_matching_sense_and_lists_more():
    a, b = _sense("1", "Mutter <Familie>"), _sense("2", "Mutter <Technik>")
    rows = [_row(1, "ZL 1", 1)]
    results = [_sense_result(b, [1, 9], matches=True), _sense_result(a, [8])]
    sense_rows = [
        {
            "name": "Mutter <Technik>",
            "notations": [{"notation": "ZL 1"}, {"notation": "ZL 9"}],
        },
        {"name": "Mutter <Familie>", "notations": [{"notation": "MS 8"}]},
    ]
    blocks = presentation.assign_to_senses(rows, results, sense_rows)
    assert blocks[0]["selected"] is True and blocks[1]["selected"] is False
    assert blocks[0]["hit_count"] == 2 and blocks[0]["count"] == 1
    assert [n["notation"] for n in blocks[0]["more"]] == ["ZL 9"]
    assert [n["notation"] for n in blocks[1]["more"]] == ["MS 8"]


def test_assign_to_senses_tab_labels_and_exactly_one_selected():
    familie = GndSense(gnd_id="1", preferred_name="Mutter", qualifier="Familie")
    technik = GndSense(gnd_id="2", preferred_name="Mutter", qualifier="Technik")
    rows = [_row(1, "ZL 1", 1), _row(2, "AB 1", 2)]
    # Familie zuerst, Technik passt zum Filter -> Technik gewählt
    results = [_sense_result(familie, []), _sense_result(technik, [1], matches=True)]
    blocks = presentation.assign_to_senses(rows, results)
    assert [b["tab_label"] for b in blocks] == [
        "Mutter (Familie)",
        "Mutter (Technik)",
        "Über die Eingabe gefunden",
    ]
    assert [b["selected"] for b in blocks] == [False, True, False]
    # ohne passenden Filter: der erste Block
    results = [_sense_result(familie, []), _sense_result(technik, [1])]
    blocks = presentation.assign_to_senses(rows, results)
    assert [b["selected"] for b in blocks] == [True, False, False]
    assert presentation.sense_tab_label(GndSense("3", "Bank")) == "Bank"


def test_assign_to_senses_needs_two_senses():
    rows = [_row(1, "ZL 1", 1)]
    assert presentation.assign_to_senses(rows, []) == []
    assert (
        presentation.assign_to_senses(rows, [_sense_result(_sense("1", "X"), [1])])
        == []
    )


SUGGESTIONS = [
    LlmSuggestion(
        "Kältemittel", "verwandt", "Arbeitsstoff", True, "rvk_register", "Kältemittel"
    ),
    LlmSuggestion("Heizstab", "verwandt", "", True, "gnd", "Heizstab", "4000000-1"),
    LlmSuggestion("Wärmewunder", "fachkontext", "erfunden"),
]


def _chip_rows(chips):
    return [
        (c["term"], c["type_label"], c["origin"])
        for g in chips["groups"]
        for c in g["chips"]
    ]


def test_expansion_chips_types_keys_and_llm_status():
    chips = presentation.expansion_chips(QUERY, [SENSE], SUGGESTIONS)
    assert chips["query"] == QUERY and chips["has_expansion"] is True
    # eine Bedeutung: keine Gruppenüberschrift
    assert [g["sense_name"] for g in chips["groups"]] == [None, None]
    assert _chip_rows(chips) == [
        ("Wärmepumpe", "Synonym", "gnd"),
        ("Heizpumpe", "Synonym", "gnd"),
        ("Heiztechnik", "Oberbegriff", "gnd"),
        ("Erdwärmepumpe", "Unterbegriff", "gnd"),
        ("Kältetechnik", "verwandt", "gnd"),
        ("Kältemittel", "verwandt", "llm"),
        ("Heizstab", "verwandt", "llm"),
    ]
    first = chips["groups"][0]["chips"][0]
    assert first["key"] == f"gnd|{SENSE_ID}|synonym|wärmepumpe"
    broader = chips["groups"][0]["chips"][2]
    assert broader["url"] == "https://lobid.org/gnd/4024356-4"
    assert broader["key"] == presentation.gnd_term_key(SENSE, BROADER)
    llm_chip = chips["groups"][1]["chips"][0]
    assert llm_chip["origin_label"] == "KI, geprüft"
    assert llm_chip["status"] == "verifiziert – gefunden im RVK-Register"
    status = [(n["term"], n["status"], n["key"]) for n in chips["llm"]]
    assert status == [
        ("Kältemittel", "verifiziert – gefunden im RVK-Register", "llm|kältemittel"),
        ("Heizstab", "verifiziert – gefunden in GND", "llm|heizstab"),
        (
            "Wärmewunder",
            "nicht verifiziert – nicht im Normvokabular gefunden – nicht gesucht",
            None,
        ),
    ]
    assert [n["term"] for n in chips["unverified"]] == ["Wärmewunder"]
    assert chips["removed"] == []
    # versteckte Formularfelder: alle Schlüssel, die gesucht werden
    assert set(chips["active_keys"]) == presentation.tree_keys([SENSE], SUGGESTIONS)


def test_expansion_chips_lists_removed_terms_for_restore():
    key = presentation.gnd_term_key(SENSE, BROADER)
    chips = presentation.expansion_chips(
        QUERY, [SENSE], SUGGESTIONS, {key, "llm|heizstab"}
    )
    assert [(c["term"], c["key"]) for c in chips["removed"]] == [
        ("Heiztechnik", key),
        ("Heizstab", "llm|heizstab"),
    ]
    assert "Heiztechnik" not in [t for t, _, _ in _chip_rows(chips)]
    assert key not in chips["active_keys"]
    assert [n["checked"] for n in chips["llm"]] == [True, False, False]


def test_expansion_chips_grouped_per_sense_with_several_senses():
    other = GndSense(
        gnd_id="9",
        preferred_name="Pumpe",
        qualifier="Herz",
        terms=(ExpansionTerm("Herz", "gnd", "related", "8", "Pumpe <Herz>"),),
    )
    chips = presentation.expansion_chips(QUERY, [SENSE, other], SUGGESTIONS[:1])
    assert [g["sense_name"] for g in chips["groups"]] == [
        SENSE_NAME,
        "Pumpe <Herz>",
        "Sprachmodell, geprüft",
    ]


@pytest.mark.parametrize(
    "relation,label",
    [
        ("synonym", "Synonym"),
        ("oberbegriff", "Oberbegriff"),
        ("unterbegriff", "Unterbegriff"),
        ("verwandt", "verwandt"),
        ("fachkontext", "Fachkontext"),
    ],
)
def test_expansion_chips_llm_relation_labels(relation, label):
    suggestion = LlmSuggestion("X", relation, "", True, "gnd", "X")
    chips = presentation.expansion_chips(QUERY, [], [suggestion])
    assert chips["groups"][0]["chips"][0]["type_label"] == label


def test_expansion_chips_without_expansion():
    chips = presentation.expansion_chips(QUERY, [], [])
    assert chips["has_expansion"] is False
    assert chips["groups"] == [] and chips["active_keys"] == []


def test_tree_keys_cover_all_selectable_terms():
    keys = presentation.tree_keys([SENSE], SUGGESTIONS)
    assert len(keys) == 5 + 2
    assert "llm|wärmewunder" not in keys


def test_apply_exclusions_keeps_input_and_drops_deselected():
    llm_terms = [LLM, ExpansionTerm("Heizstab", "llm", "llm_verified", None, QUERY)]
    excluded = {presentation.gnd_term_key(SENSE, BROADER), "llm|heizstab"}
    senses, terms = presentation.apply_exclusions([SENSE], llm_terms, excluded)
    assert BROADER not in senses[0].terms
    assert len(senses[0].terms) == len(SENSE.terms) - 1
    assert terms == [LLM]
    # ohne Abwahl unverändert
    senses, terms = presentation.apply_exclusions([SENSE], llm_terms, set())
    assert senses == [SENSE] and terms == llm_terms
    # Die Eingabe hat keinen Schlüssel und lässt sich deshalb nicht abwählen
    assert all(not k.startswith("input") for k in presentation.tree_keys([SENSE], []))


def test_parse_main_groups_and_options():
    assert presentation.parse_main_groups(["zl", " ZM, zl", "", None]) == ["ZL", "ZM"]
    options = presentation.main_group_options(
        [("AA", "Allgemeines"), ("ZL", "Maschinenbau"), ("ZN", "Elektrotechnik")],
        ["ZN", "QP"],
    )
    assert [(g["code"], g["label"]) for g in options["selected"]] == [
        ("ZN", "Elektrotechnik"),
        ("QP", ""),
    ]
    assert [
        (l["letter"], [g["code"] for g in l["groups"]]) for l in options["letters"]
    ] == [
        ("A", ["AA"]),
        ("Z", ["ZL"]),
    ]
    # vor der ersten Suche: keine Zahlen, nichts abgeschwächt
    assert options["has_counts"] is False
    assert all(
        g["count"] is None and g["has_hits"]
        for g in options["selected"] + options["letters"][0]["groups"]
    )


def test_main_group_options_with_counts():
    options = presentation.main_group_options(
        [("AA", "Allgemeines"), ("ZL", "Maschinenbau")], ["ZL"], {"ZL": 3, "MS": 2}
    )
    assert options["has_counts"] is True
    zl = options["selected"][0]
    assert (zl["code"], zl["count"], zl["has_hits"], zl["checked"]) == (
        "ZL",
        3,
        True,
        True,
    )
    aa = options["letters"][0]["groups"][0]
    assert (aa["code"], aa["count"], aa["has_hits"]) == ("AA", 0, False)


# --- P10: Verweise als Links ---------------------------------------------------------------


def test_link_notations_single_notation():
    html = str(presentation.link_notations("siehe ST 301 und mehr"))
    assert html == (
        'siehe <a class="notation-link" href="/?q=ST%20301" data-query="ST 301">'
        "→ ST 301 anzeigen</a> und mehr"
    )


@pytest.mark.parametrize("dash", ["-", "–"])
def test_link_notations_range_is_one_link(dash):
    html = str(presentation.link_notations(f"vgl. ST 300 {dash} ST 310."))
    assert html.count("<a ") == 1
    assert 'data-query="ST 300 - ST 310"' in html
    assert 'href="/?q=ST%20300%20-%20ST%20310"' in html
    assert "→ ST 300 - ST 310 anzeigen</a>." in html
    assert html.startswith("vgl. <a ")


def test_link_notations_several_and_decimal():
    html = str(presentation.link_notations("ZN 8330, ZL 4600 und AB 12.5"))
    assert html.count('class="notation-link"') == 3
    assert 'data-query="AB 12.5"' in html
    assert ", <a " in html and " und <a " in html


def test_link_notations_escapes_text():
    html = str(presentation.link_notations('<script>alert("x")</script> & ZL 4600'))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert 'data-query="ZL 4600"' in html


def test_link_notations_leaves_plain_text_and_empty():
    assert (
        str(presentation.link_notations("Hier nur Normteile")) == "Hier nur Normteile"
    )
    assert str(presentation.link_notations("kleine st 301")) == "kleine st 301"
    assert str(presentation.link_notations(None)) == ""
    assert str(presentation.link_notations("")) == ""


def test_parse_notation_query():
    assert presentation.parse_notation_query("ZL 4620") == "ZL 4620"
    assert presentation.parse_notation_query("  zl   4620 ") == "ZL 4620"
    assert presentation.parse_notation_query("ST 300–ST 310") == "ST 300 - ST 310"
    assert presentation.parse_notation_query("ST 300 - ST 310") == "ST 300 - ST 310"
    assert presentation.parse_notation_query("Getriebe") is None
    assert presentation.parse_notation_query("ZL 4620 Muttern") is None
    assert presentation.parse_notation_query("") is None
    assert presentation.parse_notation_query(None) is None


# --- P10: fünf Relevanzstufen ----------------------------------------------------------------

TIERS = scoring.ScoringSettings(tier_high=0.75, tier_medium=0.55)


@pytest.mark.parametrize(
    "score,level,word",
    [
        (1.2, 5, "sehr hoch"),
        (0.95, 5, "sehr hoch"),
        (0.9499, 4, "hoch"),
        (0.75, 4, "hoch"),
        (0.7499, 3, "mittel"),
        (0.55, 3, "mittel"),
        (0.5499, 2, "schwach"),
        (0.35, 2, "schwach"),
        (0.3499, 1, "gering"),
        (0.0, 1, "gering"),
    ],
)
def test_relevance_level_boundaries(score, level, word):
    assert presentation.relevance_level(score, TIERS) == (level, word)


def test_relevance_level_keeps_calibrated_tiers():
    # Stufen 3 und 4 beginnen genau an den Schwellen von scoring.tier_for
    for score in (0.2, 0.5, 0.55, 0.6, 0.75, 0.8, 1.0):
        level, _ = presentation.relevance_level(score, TIERS)
        tier = scoring.tier_for(score, TIERS)
        assert (level >= 4) == (tier == "hoch")
        assert (level == 3) == (tier == "mittel")


def test_relevance_words_never_percent():
    assert presentation.RELEVANCE_WORDS == (
        "gering",
        "schwach",
        "mittel",
        "hoch",
        "sehr hoch",
    )
    assert all("%" not in w for w in presentation.RELEVANCE_WORDS)


# --- P10: keine Vorschläge, eingegebene Notation -----------------------------------------------


def test_empty_state_filter_cause_with_reset_count():
    state = presentation.empty_state("Getriebe", ["ZL", "ZM"], {"MS": 3, "AB": 2}, 1)
    assert state["cause"] == "filter"
    assert state["reset_count"] == 5
    assert state["selected"] == ["ZL", "ZM"]
    assert state["removed_count"] == 1


def test_empty_state_nothing_found():
    assert presentation.empty_state("X", [], {"MS": 3})["cause"] == "nothing"
    state = presentation.empty_state("X", ["ZL"], {})
    assert state["cause"] == "nothing" and state["reset_count"] == 0
    assert presentation.empty_state("X", ["ZL"], None)["cause"] == "nothing"


def test_entered_notation_card():
    notation = Notation(1, "ZL 4620", "Muttern", "Technik", None, None, "ZL 4600", "N")
    card = presentation.entered_notation(notation, "https://example.invalid/x")
    assert card["notation"] == "ZL 4620" and card["label"] == "Muttern"
    assert card["origin"] == "Direkt: Notation eingegeben"
    assert "→ ZL 4600 anzeigen" in str(card["see_also_html"])
    assert card["has_note"] is True


# --- P11: fünf gemessene Stufen, Zahlen „20+“, Facette nach Fachgebieten, Quelle am Chip -----

FIVE = scoring.ScoringSettings(
    formula="legacy",
    tier_very_high=0.83,
    tier_high=0.58,
    tier_medium=0.52,
    tier_weak=0.43,
)


@pytest.mark.parametrize(
    "score,level,word",
    [
        (1.3, 5, "sehr hoch"),
        (0.83, 5, "sehr hoch"),
        (0.8299, 4, "hoch"),
        (0.58, 4, "hoch"),
        (0.5799, 3, "mittel"),
        (0.52, 3, "mittel"),
        (0.5199, 2, "schwach"),
        (0.43, 2, "schwach"),
        (0.4299, 1, "gering"),
    ],
)
def test_relevance_level_with_four_measured_thresholds(score, level, word):
    assert presentation.relevance_level(score, FIVE) == (level, word)


def test_relevance_level_profile_from_config_is_used():
    from app.config import Config

    settings = scoring.ScoringSettings.from_config(Config)
    tiers = Config.SCORE_PROFILES["max_plus"]["tiers"]
    assert presentation.relevance_level(tiers["tier_very_high"], settings)[0] == 5
    assert presentation.relevance_level(tiers["tier_weak"], settings)[0] == 2
    below = round(tiers["tier_weak"] - 0.0001, 4)
    assert presentation.relevance_level(below, settings)[1] == "gering"


def test_format_count():
    assert presentation.format_count(21, 20) == "20+"
    assert presentation.format_count(20, 20) == "20"
    assert presentation.format_count(3, 20) == "3"
    assert presentation.format_count(0, 20) == "0"
    assert presentation.format_count(500, None) == "500"
    assert presentation.format_count(6, 5) == "5+"


TOP_LEVEL = [
    ("A", "Allgemeines"),
    ("CA - CK", "Philosophie"),
    ("CL - CZ", "Psychologie"),
    ("LD,LH - LO", "Kunstgeschichte"),
    ("SA - SP", "Mathematik"),
    ("SQ - SU", "Informatik"),
    ("X - Y", "Medizin"),
    ("ZG - ZS", "Technik"),
    ("ZX - ZY", "Sport"),
]


def test_letter_titles_join_subjects_and_ranges():
    titles = presentation.letter_titles(TOP_LEVEL)
    assert titles["A"] == "Allgemeines"
    assert titles["C"] == "Philosophie · Psychologie"
    assert titles["L"] == "Kunstgeschichte"
    assert titles["S"] == "Mathematik · Informatik"
    # Bereich über zwei Buchstaben gilt für beide
    assert titles["X"] == titles["Y"] == "Medizin"
    assert titles["Z"] == "Technik · Sport"
    assert "B" not in titles
    # ohne Dubletten
    doubled = presentation.letter_titles(
        [("SA - SK", "Mathematik"), ("SL", "Mathematik")]
    )
    assert doubled["S"] == "Mathematik"


def test_letter_titles_database_first_config_as_fallback():
    titles = presentation.letter_titles(
        [("ZG - ZS", "Technik")],
        [("A", "Allgemeines"), ("ZG - ZS", "Technik alt"), ("ZX - ZY", "Sport")],
    )
    # Datenbank hat Vorrang je Buchstabe; die Konfiguration füllt nur Lücken
    assert titles["Z"] == "Technik"
    assert titles["A"] == "Allgemeines"
    assert presentation.letter_titles([], []) == {}


def test_fallback_in_config_covers_every_main_group_letter():
    from app.config import Config

    titles = presentation.letter_titles([], Config.MAIN_GROUP_TOP_LEVEL_FALLBACK)
    # Anfangsbuchstaben aller RVK-Hauptgruppen laut Quelle (RVK-API v3)
    assert set(titles) >= set("ABCDEFGHIKLMNPQRSTUVWXYZ")
    assert titles["S"] == "Mathematik · Informatik"


def test_main_group_options_letters_with_heading_sum_and_open():
    groups = [
        ("AA", "Allgemeines"),
        ("SK", "Analysis"),
        ("ST", "Informatik"),
        ("ZL", "Maschinenbau"),
        ("ZM", "Werkstoffe"),
    ]
    titles = presentation.letter_titles(TOP_LEVEL)
    before = presentation.main_group_options(groups, [], titles=titles, max_count=20)
    s = next(letter for letter in before["letters"] if letter["letter"] == "S")
    assert s["heading"] == "S – Mathematik · Informatik"
    assert s["count_sum"] is None and s["count_sum_text"] is None
    assert not any(letter["open"] for letter in before["letters"])

    after = presentation.main_group_options(
        groups,
        ["ZL"],
        {"SK": 15, "ST": 9, "ZL": 4, "ZM": 1},
        titles=titles,
        max_count=20,
    )
    by_letter = {letter["letter"]: letter for letter in after["letters"]}
    assert by_letter["S"]["count_sum"] == 24
    assert by_letter["S"]["count_sum_text"] == "20+"
    assert [g["count_text"] for g in by_letter["S"]["groups"]] == ["15", "9"]
    # Buchstabe mit gewählter Hauptgruppe offen, die übrigen eingeklappt
    assert by_letter["Z"]["open"] is True and by_letter["S"]["open"] is False
    assert [g["code"] for g in by_letter["Z"]["groups"]] == ["ZM"]
    assert by_letter["Z"]["count_sum"] == 1
    # ohne Titel nur der Buchstabe
    assert by_letter["A"]["heading"] == "A – Allgemeines"
    plain = presentation.main_group_options(groups, [], titles={})
    assert plain["letters"][0]["heading"] == "A"


def test_empty_state_reset_count_text_capped():
    state = presentation.empty_state("X", ["ZL"], {"MS": 30, "AB": 2}, max_count=20)
    assert state["reset_count"] == 32 and state["reset_count_text"] == "20+"
    small = presentation.empty_state("X", ["ZL"], {"MS": 3}, max_count=20)
    assert small["reset_count_text"] == "3"


def test_expansion_chips_carry_source_label_also_when_removed():
    key = presentation.gnd_term_key(SENSE, BROADER)
    chips = presentation.expansion_chips(
        QUERY, [SENSE], SUGGESTIONS, {key, "llm|heizstab"}
    )
    labels = {c["term"]: c["source_label"] for g in chips["groups"] for c in g["chips"]}
    assert labels["Wärmepumpe"] == "GND · Synonym"
    assert labels["Erdwärmepumpe"] == "GND · Unterbegriff"
    assert labels["Kältetechnik"] == "GND · verwandt"
    assert labels["Kältemittel"] == "KI, geprüft im RVK-Register"
    removed = {c["term"]: c for c in chips["removed"]}
    assert removed["Heiztechnik"]["source_label"] == "GND · Oberbegriff"
    assert removed["Heiztechnik"]["url"] == "https://lobid.org/gnd/4024356-4"
    assert removed["Heizstab"]["source_label"] == "KI, geprüft in GND"
    assert presentation.llm_source_label(None) == "KI, geprüft"


def test_expansion_chips_without_llm_have_no_ai_chips_or_block():
    # Sprachmodell aus (Standard): keine Vorschläge -> keine KI-Chips, kein LLM-Bereich
    chips = presentation.expansion_chips(QUERY, [SENSE], [])
    assert chips["llm"] == [] and chips["unverified"] == []
    assert all(c["origin"] == "gnd" for g in chips["groups"] for c in g["chips"])
    assert all(
        not c["source_label"].startswith("KI")
        for g in chips["groups"]
        for c in g["chips"]
    )
    other = GndSense(
        gnd_id="9", preferred_name="Pumpe", qualifier="Herz", terms=(RELATED,)
    )
    named = presentation.expansion_chips(QUERY, [SENSE, other], [])
    assert "Sprachmodell, geprüft" not in [g["sense_name"] for g in named["groups"]]
