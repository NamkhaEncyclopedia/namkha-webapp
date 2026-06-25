"""Pure render helpers, exercised with synthetic inputs and asserted exactly.

Colors are compared against `app.constants`.
"""

import namkha_calculator as nc
import pytest
from lxml import etree

from app import constants
from app.calculation_render import (
    SVG_NS,
    _band_colors,
    _diamond_geometry,
    _element_color,
    _layout,
    _sanitize_svg,
    _set_fill,
    _set_label,
    _utc_offset,
)

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


# --- _layout: gender side-swap ----------------------------------------------------


def test_layout_male():
    positions, labels = _layout(nc.Gender.MALE)
    assert positions["left"] == nc.Aspect.CAPACITY
    assert positions["right"] == nc.Aspect.FORTUNE
    assert positions["left_mewa"] == nc.Aspect.MEWA_CAPACITY
    assert positions["right_mewa"] == nc.Aspect.MEWA_FORTUNE
    assert labels["label_left_aspect"] == "Capacity"
    assert labels["label_right_aspect"] == "Fortune"
    assert labels["label_left_mewa"] == "Capacity Mewa"
    assert labels["label_right_mewa"] == "Fortune Mewa"
    # Fixed mappings are gender-independent.
    assert positions["life"] == nc.Aspect.LIFE
    assert labels["label_life"] == "Life"
    assert labels["label_life_mewa"] == "Life Mewa"


def test_layout_female_mirrors_sides():
    positions, labels = _layout(nc.Gender.FEMALE)
    assert positions["left"] == nc.Aspect.FORTUNE
    assert positions["right"] == nc.Aspect.CAPACITY
    assert positions["left_mewa"] == nc.Aspect.MEWA_FORTUNE
    assert positions["right_mewa"] == nc.Aspect.MEWA_CAPACITY
    assert labels["label_left_aspect"] == "Fortune"
    assert labels["label_right_aspect"] == "Capacity"


# --- _element_color: deep-water override ------------------------------------------


@pytest.mark.parametrize(
    "element, mewa, expected",
    [
        (nc.Element.WATER, None, constants.ELEMENT_COLORS[nc.Element.WATER]),
        (nc.Element.WATER, 2, constants.MEWA_TWO_COLOR),
        (nc.Element.WATER, 6, constants.ELEMENT_COLORS[nc.Element.WATER]),
        (nc.Element.FIRE, 2, constants.ELEMENT_COLORS[nc.Element.FIRE]),
        (nc.Element.METAL, None, constants.ELEMENT_COLORS[nc.Element.METAL]),
    ],
)
def test_element_color(element, mewa, expected):
    assert _element_color(element, mewa) == expected


# --- _band_colors: sequence, LIFE weave, deep-water center-only -------------------


def test_band_colors_non_life_is_the_sequence(make_aspect):
    sequence = (nc.Element.WOOD, nc.Element.FIRE)
    aspect = make_aspect(nc.Aspect.BODY, nc.Element.METAL, sequence)
    assert _band_colors(nc.Aspect.BODY, aspect, None) == [
        constants.ELEMENT_COLORS[nc.Element.WOOD],
        constants.ELEMENT_COLORS[nc.Element.FIRE],
    ]


def test_band_colors_life_weaves_three_times(make_aspect):
    sequence = (nc.Element.WOOD, nc.Element.FIRE, nc.Element.EARTH)
    aspect = make_aspect(nc.Aspect.LIFE, nc.Element.METAL, sequence)
    colors = _band_colors(nc.Aspect.LIFE, aspect, None)
    sequence_colors = [constants.ELEMENT_COLORS[e] for e in sequence]
    center = constants.ELEMENT_COLORS[nc.Element.METAL]
    assert colors == (
        sequence_colors + [center] + sequence_colors + [center] + sequence_colors
    )
    assert len(colors) == 3 * len(sequence) + 2


def test_band_colors_deep_water_only_on_woven_center(make_aspect):
    """WATER center + mewa==2 recolors the woven LIFE center to dark blue, but the
    WATER threads inside the sequence stay normal blue. Deliberate split."""
    sequence = (nc.Element.WATER, nc.Element.FIRE)
    aspect = make_aspect(nc.Aspect.LIFE, nc.Element.WATER, sequence)
    colors = _band_colors(nc.Aspect.LIFE, aspect, 2)
    water = constants.ELEMENT_COLORS[nc.Element.WATER]
    fire = constants.ELEMENT_COLORS[nc.Element.FIRE]
    dark = constants.MEWA_TWO_COLOR
    assert colors == [water, fire, dark, water, fire, dark, water, fire]
    assert colors.count(dark) == 2  # the two woven centers
    assert colors.count(water) == 3  # sequence water threads untouched


# --- _diamond_geometry: bbox center + half-extents --------------------------------


@pytest.mark.parametrize(
    "d",
    [
        "M 0 0 H 20 V 20 H 0 Z",
        "M 10 0 L 20 10 L 10 20 L 0 10 Z",
        "m 10 0 l 10 10 l -10 10 l -10 -10 z",
    ],
)
def test_diamond_geometry(d):
    path = etree.fromstring(f'<path d="{d}"/>')
    assert _diamond_geometry(path) == (10.0, 10.0, 10.0, 10.0)


# --- _set_fill: rewrites fill inside the style attribute --------------------------


def _rect(style=None):
    rect = etree.Element("rect")
    if style is not None:
        rect.set("style", style)
    return rect


def test_set_fill_rewrites_existing_style_fill():
    rect = _rect("fill:#000000;stroke:red")
    _set_fill(rect, "#abcdef")
    assert "fill:#abcdef" in rect.get("style")
    assert "#000000" not in rect.get("style")
    assert "stroke:red" in rect.get("style")


def test_set_fill_appends_when_no_fill_in_style():
    rect = _rect("stroke:red")
    _set_fill(rect, "#abcdef")
    assert "fill:#abcdef" in rect.get("style")
    assert "stroke:red" in rect.get("style")


def test_set_fill_creates_style_when_absent():
    rect = _rect()
    _set_fill(rect, "#abcdef")
    assert rect.get("style") == "fill:#abcdef"


# --- _set_label: sets tspan text by id, missing id is a no-op ---------------------


def _svg_with_label():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    text = etree.SubElement(root, f"{{{SVG_NS}}}text")
    text.set("id", "label_left_aspect")
    tspan = etree.SubElement(text, f"{{{SVG_NS}}}tspan")
    tspan.text = "placeholder"
    return root


def test_set_label_sets_tspan_text():
    root = _svg_with_label()
    _set_label(root, "label_left_aspect", "Capacity")
    assert root.find(f".//{{{SVG_NS}}}tspan").text == "Capacity"


def test_set_label_missing_id_is_noop():
    root = _svg_with_label()
    _set_label(root, "label_does_not_exist", "X")  # must not raise


# --- _utc_offset: sign and half-hour zones ----------------------------------------


def test_utc_offset_sub_hour(make_request):
    # Nepal was UTC+5:30 in 1985 (the builder's default date); it moved to +5:45
    # only in 1986. pytz.localize reflects the historical offset -> exercises the
    # minutes branch either way.
    subject = make_request(timezone="Asia/Kathmandu").subject
    assert _utc_offset(subject) == "(UTC+5:30)"


def test_utc_offset_whole_hour(make_request):
    # 1985-03-15 is before European DST began that year -> CET (+1).
    subject = make_request(timezone="Europe/Berlin").subject
    assert _utc_offset(subject) == "(UTC+1)"


# --- _sanitize_svg: defense-in-depth before embedding as raw HTML -----------------


def test_sanitize_svg_strips_dangerous_content():
    svg = (
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="http://www.w3.org/1999/xlink">'
        "<script>alert(1)</script>"
        "<foreignObject></foreignObject>"
        '<rect onclick="x()" onmouseover="y()"/>'
        '<a href="javascript:alert(1)">a</a>'
        '<a xlink:href="javascript:alert(2)">b</a>'
        "</svg>"
    ).encode()
    root = etree.fromstring(_sanitize_svg(svg))
    assert root.find(f"{{{SVG_NS}}}script") is None
    assert root.find(f"{{{SVG_NS}}}foreignObject") is None
    rect = root.find(f"{{{SVG_NS}}}rect")
    assert "onclick" not in rect.attrib
    assert "onmouseover" not in rect.attrib
    for anchor in root.findall(f"{{{SVG_NS}}}a"):
        assert anchor.get("href") is None
        assert anchor.get(XLINK_HREF) is None
    assert root.get("aria-hidden") == "true"
    assert root.get("role") == "presentation"
