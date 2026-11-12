"""Aufbereitung für die Oberfläche (P6) – reine Funktionen, ohne Netz, ohne Datenbank."""

import pytest

from app.models import Evidence, ExpansionTerm, GndSense, Hit, LlmSuggestion, Notation
from app.services import evaluation, presentation, ranking

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
    assert blocks[0]["open"] is True and blocks[1]["open"] is False

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
    assert blocks[0]["open"] is True and blocks[1]["open"] is False
    assert blocks[0]["hit_count"] == 2 and blocks[0]["count"] == 1
    assert [n["notation"] for n in blocks[0]["more"]] == ["ZL 9"]
    assert [n["notation"] for n in blocks[1]["more"]] == ["MS 8"]


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


def test_expansion_tree_structure_keys_and_llm_status():
    tree = presentation.expansion_tree(QUERY, [SENSE], SUGGESTIONS)
    assert tree["query"] == QUERY and tree["has_expansion"] is True
    sense = tree["senses"][0]
    assert sense["url"] == f"https://lobid.org/gnd/{SENSE_ID}"
    assert sense["subject_categories"] == ["Energietechnik"]
    assert sense["own"]["key"] == f"gnd|{SENSE_ID}|synonym|wärmepumpe"
    assert [(g["label"], [t["term"] for t in g["terms"]]) for g in sense["groups"]] == [
        ("Synonyme", ["Heizpumpe"]),
        ("Oberbegriffe", ["Heiztechnik"]),
        ("Unterbegriffe", ["Erdwärmepumpe"]),
        ("verwandte Begriffe", ["Kältetechnik"]),
    ]
    broader = sense["groups"][1]["terms"][0]
    assert broader["url"] == "https://lobid.org/gnd/4024356-4" and broader["checked"]
    status = [(n["term"], n["status"], n["key"]) for n in tree["llm"]]
    assert status == [
        ("Kältemittel", "verifiziert – gefunden im RVK-Register", "llm|kältemittel"),
        ("Heizstab", "verifiziert – gefunden in GND", "llm|heizstab"),
        (
            "Wärmewunder",
            "nicht verifiziert – nicht im Normvokabular gefunden – nicht gesucht",
            None,
        ),
    ]


def test_expansion_tree_marks_excluded_terms():
    key = presentation.gnd_term_key(SENSE, BROADER)
    tree = presentation.expansion_tree(
        QUERY, [SENSE], SUGGESTIONS, {key, "llm|heizstab"}
    )
    assert tree["senses"][0]["groups"][1]["terms"][0]["checked"] is False
    assert [n["checked"] for n in tree["llm"]] == [True, False, False]


def test_expansion_tree_without_expansion():
    assert presentation.expansion_tree(QUERY, [], [])["has_expansion"] is False


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
