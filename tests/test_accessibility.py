"""Barrierefreiheit (P6): Kontraste nach WCAG AA und Textalternativen in den Templates."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "app" / "templates"


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


def css_variables() -> dict[str, str]:
    root = re.search(r":root\s*{(.*?)}", CSS, re.DOTALL).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", root))


def test_contrast_helper_matches_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    # das frühere Grau für nicht geprüfte Vorschläge reichte nicht
    assert contrast_ratio("#888888", "#ffffff") < 4.5


# Text-/Hintergrund-Paare, die in style.css vorkommen
PAIRS = [
    ("text", "bg"),
    ("text", "surface"),
    ("text", "surface-alt"),
    ("muted", "bg"),
    ("muted", "surface"),
    ("muted", "surface-alt"),
    ("link", "bg"),
    ("link", "surface"),
    ("link", "surface-alt"),
    ("header-text", "header-bg"),
    ("footer-text", "footer-bg"),
    ("link", "footer-bg"),
    ("warn-text", "warn-bg"),
]


@pytest.mark.parametrize("foreground,background", PAIRS)
def test_color_pairs_reach_wcag_aa(foreground, background):
    variables = css_variables()
    ratio = contrast_ratio(variables[foreground], variables[background])
    assert ratio >= 4.5, f"{foreground} auf {background}: {ratio:.2f}"


def test_unverified_llm_text_uses_accessible_color():
    block = re.search(r"\.llm-unverified\s*{(.*?)}", CSS, re.DOTALL).group(1)
    assert "var(--muted)" in block
    assert "#888" not in block


def test_focus_is_visible():
    assert ":focus-visible" in CSS


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


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


def test_warning_and_llm_status_are_text():
    row = _template("_hit_row.html")
    assert "Nur über Ähnlichkeit gefunden – bitte im Register prüfen" in row
    assert 'role="note"' in row
    tree = _template("_expansion_tree.html")
    assert "{{ s.status }}" in tree


def test_skip_link_and_live_region():
    base = _template("base.html")
    assert 'class="skip-link" href="#inhalt"' in base
    assert 'id="inhalt"' in base
    assert 'aria-live="polite"' in base


def test_tables_have_caption_and_column_headers():
    group = _template("_hit_group.html")
    assert "<caption>" in group
    assert '<th scope="col">' in group
