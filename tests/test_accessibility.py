"""Barrierefreiheit und Design-Regeln (P6, P10): Tokens aus docs/design.md, Kontraste nach
WCAG AA, Fokus, Touch-Ziele, keine externen Ressourcen, Textalternativen in den Templates."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "app" / "templates"

# docs/design.md Abschnitt 2 – exakt diese Werte
DESIGN_TOKENS = {
    "navy-900": "#13294B",
    "navy-700": "#1C3A6B",
    "navy-100": "#E8EDF5",
    "gold-500": "#C9A24B",
    "gold-50": "#FAF4E6",
    "gold-700": "#7A5A12",
    "gold-300": "#F1D58F",
    "text": "#1A2233",
    "muted": "#4A5568",
    "border": "#D9DFE8",
    "bg": "#F6F7FA",
    "seg-off": "#E3E7EE",
}


def _relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Kontrastverhältnis nach WCAG 2.x, z. B. Schwarz auf Weiß = 21."""
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _root_block() -> str:
    return re.search(r":root\s*{(.*?)}", CSS, re.DOTALL).group(1)


def css_variables() -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", _root_block()))


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _rule(selector: str) -> str:
    """Inhalt der ersten Regel, deren Selektorliste `selector` enthält."""
    for match in re.finditer(r"([^{}]+){([^{}]*)}", _without_comments(CSS)):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    raise AssertionError(f"Keine Regel für {selector}")


def test_contrast_helper_matches_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    # das frühere Grau für nicht geprüfte Vorschläge reichte nicht
    assert contrast_ratio("#888888", "#ffffff") < 4.5


def test_all_design_tokens_have_exact_values():
    variables = css_variables()
    for name, value in DESIGN_TOKENS.items():
        assert variables.get(name, "").upper() == value, name


def test_only_documented_extra_tokens():
    # P10: nur „surface“ (weiße Flächen) und „on-dark“ (Text auf Navy) kommen dazu
    extra = set(css_variables()) - set(DESIGN_TOKENS)
    assert extra == {"surface", "on-dark"}


NAMED_COLORS = (
    "white",
    "black",
    "red",
    "blue",
    "green",
    "gray",
    "grey",
    "orange",
    "yellow",
    "silver",
    "navy",
    "gold",
    "purple",
    "maroon",
)


def test_no_color_values_outside_root():
    css = _without_comments(CSS)
    rest = re.sub(r":root\s*{.*?}", "", css, count=1, flags=re.DOTALL)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rest)
    assert not re.search(r"\b(rgba?|hsla?|hwb|lab|lch|oklch)\(", rest)
    without_vars = re.sub(r"var\(--[\w-]+\)", "", rest)
    without_strings = re.sub(r'"[^"]*"', "", without_vars)
    for name in NAMED_COLORS:
        assert not re.search(rf":[^;{{}}]*\b{name}\b", without_strings), name


# Text-/Hintergrund-Paare, die im Stylesheet vorkommen
PAIRS = [
    ("text", "bg"),
    ("text", "surface"),
    ("text", "gold-50"),
    ("muted", "bg"),
    ("muted", "surface"),
    ("muted", "gold-50"),
    ("muted", "seg-off"),
    ("muted", "navy-100"),
    ("navy-700", "surface"),
    ("navy-700", "bg"),
    ("navy-700", "gold-50"),
    ("navy-700", "navy-100"),
    ("navy-900", "surface"),
    ("navy-900", "bg"),
    ("navy-900", "navy-100"),
    ("navy-900", "gold-50"),
    ("gold-700", "gold-50"),
    ("gold-700", "surface"),
    ("on-dark", "navy-700"),
    ("on-dark", "navy-900"),
    ("gold-300", "navy-900"),
]


@pytest.mark.parametrize("foreground,background", PAIRS)
def test_color_pairs_reach_wcag_aa(foreground, background):
    variables = css_variables()
    ratio = contrast_ratio(variables[foreground], variables[background])
    assert ratio >= 4.5, f"{foreground} auf {background}: {ratio:.2f}"


def test_gold_500_is_never_text_color():
    # Gold nur als Fläche (Linie, Segment, Chip-Rand), als Text gold-700
    assert not re.search(r"(?<![-\w])color:\s*var\(--gold-500\)", CSS)
    assert "color: var(--gold-700)" in _rule(".chip-type")


def test_focus_ring_is_3px_gold_with_contrast_ring():
    rule = _rule(":focus-visible")
    assert "outline: 3px solid var(--gold-500)" in rule
    # Ergänzung für WCAG 1.4.11, ersetzt den Goldrahmen nicht
    assert "box-shadow:" in rule and "var(--navy-900)" in rule


@pytest.mark.parametrize(
    "selector",
    [
        ".btn-primary",
        ".chip-remove",
        ".chip-restore",
        ".action",
        ".facet-item",
        ".facet-toggle",
        ".main-nav a",
        '.sense-tabs [role="tab"]',
        ".why summary",
    ],
)
def test_touch_targets_at_least_44px(selector):
    assert "min-height: 44px" in _rule(selector)


def test_chip_x_visible_32px_with_44px_click_area():
    assert "width: 32px" in _rule(".chip-x") and "height: 32px" in _rule(".chip-x")
    remove = _rule(".chip-remove")
    assert "min-width: 44px" in remove and "min-height: 44px" in remove


def test_muted_facet_entries_use_color_not_opacity():
    rule = _rule(".facet-item.is-empty")
    assert "color: var(--muted)" in rule and "opacity" not in rule
    inactive = _rule(".facet-fieldset.is-inactive .facet-item")
    assert "color: var(--muted)" in inactive and "opacity" not in inactive
    assert "background: var(--seg-off)" in _rule(
        ".facet-fieldset.is-inactive .facet-scroll"
    )
    # opacity kommt im ganzen Stylesheet nur für die Ladeanzeige vor
    assert _without_comments(CSS).count("opacity") == 3


def test_unverified_llm_text_uses_accessible_color():
    assert "var(--muted)" in _rule(".llm-unverified")


def test_fonts_are_local_with_swap():
    faces = re.findall(r"@font-face\s*{(.*?)}", CSS, re.DOTALL)
    assert len(faces) == 3
    for face in faces:
        urls = re.findall(r'url\("([^"]+)"\)', face)
        assert urls and all(u.startswith("/static/fonts/") for u in urls)
        assert (ROOT / "app" / urls[0].lstrip("/")).is_file()
        assert "font-display: swap" in face


def test_no_external_resources_in_css_and_head():
    assert not re.search(r"https?://", _without_comments(CSS))
    assert "@import" not in CSS
    head = _template("base.html").split("</head>", 1)[0]
    assert not re.search(r'(href|src)="https?://', head)


def test_breakpoint_860px():
    assert "@media (max-width: 860px)" in CSS


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_favicon_links():
    base = _template("base.html")
    assert "img/favicon.svg" in base and 'type="image/svg+xml"' in base
    assert "filename='favicon.ico'" in base
    assert 'rel="apple-touch-icon"' in base and "img/apple-touch-icon.png" in base


def test_logo_and_claim_in_header():
    base = _template("base.html")
    assert "img/logo-rvkompass.svg" in base and 'alt="RVKompass"' in base
    assert 'height="44"' in base
    assert "Klassifizieren mit Richtung" in base


def test_exactly_one_link_to_ghochhauser_at():
    base = _template("base.html")
    assert len(re.findall(r'href="https?://(www\.)?ghochhauser\.at', base)) == 1
    assert "Ein Projekt von ghochhauser.at" in base
    assert 'href="mailto:rvkompass@ghochhauser.at"' in base


def test_search_field_has_label():
    html = _template("search.html")
    assert '<label for="q"' in html and 'id="q"' in html


def test_every_button_has_text_or_label():
    for path in TEMPLATES.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.DOTALL):
            attrs, text = match.groups()
            visible = re.sub(r"<[^>]+>|{[{%#].*?[%}#]}", "", text).strip()
            assert visible or "aria-label" in attrs, f"{path.name}: {match.group(0)}"


def test_chip_remove_has_aria_label():
    chips = _template("_chips.html")
    assert 'aria-label="{{ c.term }} entfernen"' in chips


def test_warning_and_llm_status_are_text():
    card = _template("_hit_card.html")
    assert "Nur über Ähnlichkeit gefunden – bitte im Register prüfen" in card
    assert 'role="note"' in card
    chips = _template("_chips.html")
    assert "{{ s.status }}" in chips


def test_relevance_is_also_text():
    card = _template("_hit_card.html")
    assert 'role="img"' in card
    assert 'aria-label="Relevanz {{ r.level_word }}, Stufe {{ r.level }} von 5"' in card
    assert '<span class="relevance-word">{{ r.level_word }}</span>' in card


def test_tabs_roles():
    table = _template("_results_table.html")
    assert 'role="tablist"' in table and 'role="tab"' in table
    assert "aria-controls=" in table and "aria-selected=" in table
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert '"tabpanel"' in js and "ArrowRight" in js and "ArrowLeft" in js


def test_skip_link_and_live_region():
    base = _template("base.html")
    assert 'class="skip-link" href="#inhalt"' in base
    assert 'id="inhalt"' in base
    assert 'aria-live="polite"' in base


def test_hit_groups_have_heading_and_ordered_list_of_articles():
    group = _template("_hit_list.html")
    assert 'class="hit-group-title"' in group
    assert '<ol class="hits">' in group
    assert '{% include "_hit_card.html" %}' in group
    assert _template("_hit_card.html").startswith('<article class="hit-card"')
