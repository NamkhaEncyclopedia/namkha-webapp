"""Render pipeline.

fill_illustration / _build_data are asserted exactly with synthetic results; the
real `calculate_namkha` -> Typst path is asserted only for structure and validity
(not astrological values), plus one interface-drift guard that pushes a real result
through the render helpers.

Why label/color swap is checked on fill_illustration, not the compiled output:
Typst base64-embeds the illustration as one image, so its label text and fills are
not greppable in the compiled SVG. The raw fill_illustration SVG is the inspectable
layer; the compiled path is checked structurally.
"""

import namkha_calculator as nc
from lxml import etree
from namkha_calculator.calculation_notes import CalculationNote, CalculationNoteType

from app import constants
from app.calculation_render import (
    SVG_NS,
    _build_data,
    fill_illustration,
    render_pdf,
    render_svg,
)
from app.forms import build_request


def _illustration_root(result):
    return etree.fromstring(fill_illustration(result).encode("utf-8"))


def _by_id(root, node_id):
    return root.xpath("//*[@id=$i]", i=node_id)[0]


def _label_text(root, label_id):
    element = _by_id(root, label_id)
    tspan = element.find(f"{{{SVG_NS}}}tspan")
    return (tspan if tspan is not None else element).text


def _real(fixture_form, name):
    request = build_request(fixture_form(name))
    return nc.calculate_namkha(request.namkha_type, request.subject, request.method)


# --- fill_illustration ------------------------------------------------------------


def test_bands_are_namespaced_polygons_using_fill_attribute(make_result):
    root = _illustration_root(make_result())
    polygons = root.findall(f".//{{{SVG_NS}}}polygon")
    assert polygons, "no harmonization band polygons were injected"
    for polygon in polygons:
        assert polygon.get("fill")  # color via the fill attribute...
        assert polygon.get("style") is None  # ...not style (unlike center/outer)


def test_center_diamond_uses_style_fill(make_result):
    root = _illustration_root(make_result())
    style = _by_id(root, "life_rhombus_center").get("style")  # LIFE center = EARTH
    assert f"fill:{constants.ELEMENT_COLORS[nc.Element.EARTH]}" in style


def test_gender_swaps_side_colors_and_labels(make_result, make_request):
    male = _illustration_root(make_result(request=make_request(gender=nc.Gender.MALE)))
    female = _illustration_root(
        make_result(request=make_request(gender=nc.Gender.FEMALE))
    )
    # MALE left = Capacity (WOOD center); FEMALE left = Fortune (WATER center).
    male_left = _by_id(male, "left_rhombus_center").get("style")
    female_left = _by_id(female, "left_rhombus_center").get("style")
    assert f"fill:{constants.ELEMENT_COLORS[nc.Element.WOOD]}" in male_left
    assert f"fill:{constants.ELEMENT_COLORS[nc.Element.WATER]}" in female_left
    assert male_left != female_left
    # Labels swap in step with the colors.
    assert _label_text(male, "label_left_aspect") == "Capacity"
    assert _label_text(female, "label_left_aspect") == "Fortune"


def test_deep_water_paints_center_only_not_band_threads(
    make_result, make_aspect, default_aspects
):
    """The graphic composition rule (regressed once, fixed 2026-06-19): a deep-water
    rhombus paints its center swatch dark blue, while the WATER threads in its bands
    stay normal blue. Uses MEWA_BODY -> body_mewa (a gender-fixed position)."""
    deep_water = make_aspect(
        nc.Aspect.MEWA_BODY,
        nc.Element.WATER,
        sequence=(nc.Element.WATER, nc.Element.FIRE),
    )
    aspects = default_aspects({nc.Aspect.MEWA_BODY: deep_water})
    mewa_numbers = {
        nc.Aspect.MEWA_LIFE: 1,
        nc.Aspect.MEWA_BODY: 2,  # deep water
        nc.Aspect.MEWA_CAPACITY: 9,
        nc.Aspect.MEWA_FORTUNE: 8,
    }
    root = _illustration_root(make_result(aspects=aspects, mewa_numbers=mewa_numbers))
    center = _by_id(root, "body_mewa_rhombus_center")
    band_fills = [
        polygon.get("fill")
        for polygon in center.getparent().findall(f"{{{SVG_NS}}}polygon")
    ]
    assert f"fill:{constants.MEWA_TWO_COLOR}" in center.get("style")  # dark blue
    assert constants.ELEMENT_COLORS[nc.Element.WATER] in band_fills  # threads normal


# --- _build_data ------------------------------------------------------------------


def test_mewa_only_on_the_four_mewa_rows(make_result):
    data = _build_data(make_result())
    assert len(data["aspects"]) == 8
    base_rows, mewa_rows = data["aspects"][:4], data["aspects"][4:]
    assert all(row["mewa"] is None for row in base_rows)
    assert all(row["mewa"] is not None for row in mewa_rows)


def test_conflicted_none_collapses_to_false(make_result):
    life = _build_data(make_result())["aspects"][0]
    assert life["label"] == "Life"
    assert life["conflicted"] is False


def test_notes_use_friendly_overrides(make_result):
    # The sheet shows the app's plain-language message, not the library's own
    # developer-facing text.
    note = nc.CalculationNoteItem(
        note=CalculationNote.HIGH_LATITUDE,
        note_type=CalculationNoteType.CAUTION,
        message="developer-facing text",
    )
    data = _build_data(make_result(notes=[note]))
    assert data["notes"] == [constants.NOTE_MESSAGES[CalculationNote.HIGH_LATITUDE]]


def test_unmapped_note_falls_back_to_library_message(make_result, monkeypatch):
    # A note with no app override still renders, using the library's message.
    monkeypatch.delitem(constants.NOTE_MESSAGES, CalculationNote.HIGH_LATITUDE)
    note = nc.CalculationNoteItem(
        note=CalculationNote.HIGH_LATITUDE,
        note_type=CalculationNoteType.CAUTION,
        message="fallback text",
    )
    data = _build_data(make_result(notes=[note]))
    assert data["notes"] == ["fallback text"]


def test_subject_name_falls_back_to_dash(make_result, make_request):
    data = _build_data(make_result(request=make_request(name=None)))
    assert data["subject"]["name"] == "—"


def test_location_prepends_place_name_when_present(make_result, make_request):
    # A Photon-derived place name should show before the coordinates.
    data = _build_data(
        make_result(request=make_request(location_name="Berlin, Germany"))
    )
    assert data["subject"]["location"] == "Berlin, Germany (52.5200, 13.4000)"


def test_location_is_bare_coords_without_place_name(make_result, make_request):
    # Manual coordinates (no place picked) -> coordinates only, no name prefix.
    data = _build_data(make_result(request=make_request(location_name=None)))
    assert data["subject"]["location"] == "52.5200, 13.4000"


def test_constants_driven_aspect_fields(make_result):
    data = _build_data(make_result())
    capacity = next(
        row for row in data["aspects"] if row["label"] == "Capacity"
    )  # WOOD
    tibetan, roman = constants.ELEMENT_SYLLABLES[nc.Element.WOOD]
    assert capacity["element"] == nc.Element.WOOD.value
    assert capacity["syllable_tibetan"] == tibetan
    assert capacity["syllable_roman"] == roman
    assert capacity["center_color"] == constants.ELEMENT_COLOR_NAMES[nc.Element.WOOD]
    assert capacity["sequence"]  # non-empty abbreviation string


def test_deep_water_center_color_name(make_result, make_aspect, default_aspects):
    deep_water = make_aspect(nc.Aspect.MEWA_CAPACITY, nc.Element.WATER)
    aspects = default_aspects({nc.Aspect.MEWA_CAPACITY: deep_water})
    mewa_numbers = {
        nc.Aspect.MEWA_LIFE: 1,
        nc.Aspect.MEWA_BODY: 6,
        nc.Aspect.MEWA_CAPACITY: 2,  # deep water
        nc.Aspect.MEWA_FORTUNE: 8,
    }
    data = _build_data(make_result(aspects=aspects, mewa_numbers=mewa_numbers))
    row = next(r for r in data["aspects"] if r["label"] == "Capacity\nMewa")
    assert row["element"] == nc.Element.WATER.value
    assert row["center_color"] == constants.MEWA_TWO_COLOR_NAME  # "Dark Blue"


# --- real library + Typst: structure only -----------------------------------------


def test_render_svg_stacks_multiple_sanitized_pages(fixture_form):
    result = _real(fixture_form, "year_classic_berlin")
    svg = render_svg(result)
    assert svg.count('<div class="page"') >= 2  # 2-page sheet -> list-stacking path
    assert "<script" not in svg


def test_render_pdf_is_a_valid_pdf(fixture_form):
    result = _real(fixture_form, "year_classic_berlin")
    pdf = render_pdf(result)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


def test_render_pdf_embeds_document_metadata(fixture_form):
    """Title (incl. method) and author (lib/app versions) should land in the
    PDF's XMP metadata; no keywords are set. Assertions are tag-wrapped so a
    match can only come from the metadata stream, not the visible sheet text
    (which shows type/method/name separately, and the footer repeats the
    title string but without XMP tags)."""
    result = _real(fixture_form, "year_classic_berlin")
    pdf = render_pdf(result)
    assert (
        b'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">'
        b"Year Namkha Calculation (Classic): Sample Person</rdf:li>" in pdf
    )
    expected_author = (
        f"<dc:creator><rdf:Seq><rdf:li>Namkha Calculator "
        f"(lib: {constants.LIBRARY_VERSION}, app: {constants.APP_VERSION})"
        f"</rdf:li>"
    ).encode()
    assert expected_author in pdf
    assert b"<pdf:Keywords>" not in pdf


def test_real_result_flows_through_render_helpers(fixture_form):
    """Interface-drift guard: a real result must satisfy the same shape the synthetic
    builders assume."""
    result = _real(fixture_form, "year_cnnr_female")
    data = _build_data(result)
    assert len(data["aspects"]) == 8
    assert "version" in data["meta"]
    svg = fill_illustration(result)
    assert svg.lstrip().startswith("<")
    assert "polygon" in svg
