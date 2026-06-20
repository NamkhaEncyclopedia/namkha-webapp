"""Render a calculation result to PDF/SVG.

Pipeline: color the blank namkha illustration (illustration.svg) by id into
namkha.svg, dump the text data to JSON, then compile sheet.typ with Typst to PDF
(download) or SVG (inline). Typst responsible for the page + table text; the SVG has the colored
Namkha graphic and its own labels. One source, so the inline view matches the download.
Typst uses `resvg` to render SVGs, it has some quirks explained in the docstrings below.
"""

import importlib.metadata
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

import namkha_calculator as nc
import typst
from lxml import etree

from app import constants

TEMPLATES = Path(__file__).parent / "templates"
FONTS = Path(__file__).parent / "fonts"
SVG_TEMPLATE = TEMPLATES / "illustration.svg"  # blank source; filled -> namkha.svg
TYP_TEMPLATE = TEMPLATES / "sheet.typ"

SVG_NS = "http://www.w3.org/2000/svg"

_SVG_PATH_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
_SVG_PATH_COMMAND_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")

_SVG_DANGEROUS_TAGS = (f"{{{SVG_NS}}}script", f"{{{SVG_NS}}}foreignObject")
_SVG_LINK_ATTRIBUTES = ("href", f"{{http://www.w3.org/1999/xlink}}href")

# Aspect rendering order and human labels (Typst draws the table text).
ASPECTS = (
    (nc.Aspect.LIFE, "Life"),
    (nc.Aspect.BODY, "Body"),
    (nc.Aspect.CAPACITY, "Capacity"),
    (nc.Aspect.FORTUNE, "Fortune"),
    (nc.Aspect.MEWA_LIFE, "Life\nMewa"),
    (nc.Aspect.MEWA_BODY, "Body\nMewa"),
    (nc.Aspect.MEWA_CAPACITY, "Capacity\nMewa"),
    (nc.Aspect.MEWA_FORTUNE, "Fortune\nMewa"),
)


def _element_color(element: nc.Element, mewa: int | None = None) -> str:
    """Element hex color, with the deep-water (mewa=2) override."""
    if element == nc.Element.WATER and mewa == 2:
        return constants.MEWA_TWO_COLOR
    return constants.ELEMENT_COLORS[element]


def _by_id(root, node_id: str):
    found = root.xpath(f"//*[@id=$i]", i=node_id)
    return found[0] if found else None



def _diamond_geometry(path) -> tuple[float, float, float, float]:
    """Center (cx, cy) and half-extents (rx, ry) of an axis-aligned diamond path,
    from its bounding box. Walks the path's command endpoints; good enough for the
    diamonds + small corner arcs here."""
    x = y = sx = sy = 0.0
    xs: list[float] = []
    ys: list[float] = []
    for command, argument in _SVG_PATH_COMMAND_RE.findall(path.get("d", "")):
        numbers = [float(n) for n in _SVG_PATH_NUMBER_RE.findall(argument)]
        relative, command_type, index = command.islower(), command.upper(), 0

        def emit(next_x, next_y):
            nonlocal x, y
            x = x + next_x if relative else next_x
            y = y + next_y if relative else next_y
            xs.append(x)
            ys.append(y)

        match command_type:
            case "M":
                first = True
                while index + 1 < len(numbers):
                    emit(numbers[index], numbers[index + 1])
                    if first:
                        sx, sy, first = x, y, False
                    index += 2
            case "L" | "T":
                while index + 1 < len(numbers):
                    emit(numbers[index], numbers[index + 1])
                    index += 2
            case "H":
                for value in numbers:
                    x = x + value if relative else value
                    xs.append(x)
                    ys.append(y)
            case "V":
                for value in numbers:
                    y = y + value if relative else value
                    xs.append(x)
                    ys.append(y)
            case "C":
                while index + 5 < len(numbers):
                    emit(numbers[index + 4], numbers[index + 5])
                    index += 6
            case "S" | "Q":
                while index + 3 < len(numbers):
                    emit(numbers[index + 2], numbers[index + 3])
                    index += 4
            case "A":
                while index + 6 < len(numbers):
                    emit(numbers[index + 5], numbers[index + 6])
                    index += 7
            case "Z":
                x, y = sx, sy
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    return cx, cy, (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2


def _fill_bands(group, center_path, outer_path, colors) -> None:
    """Inject concentric diamond bands filling the space between the center
    diamond and the outer frame, one band per color. Innermost band = colors[0],
    outermost = colors[-1]. Drawn outermost-first so inner bands paint on top.
    Bands are centered to the outer frame.
    Polygons MUST be in the SVG namespace or resvg silently drops them."""
    _, _, rx_c, ry_c = _diamond_geometry(center_path)
    cx, cy, rx_o, ry_o = _diamond_geometry(outer_path)
    n = len(colors)
    if n == 0 or rx_o <= rx_c:
        return
    for k in range(n):
        f = (n - k) / n
        rx = rx_c + (rx_o - rx_c) * f
        ry = ry_c + (ry_o - ry_c) * f
        poly = etree.SubElement(group, f"{{{SVG_NS}}}polygon")
        poly.set(
            "points",
            f"{cx:.3f},{cy - ry:.3f} {cx + rx:.3f},{cy:.3f} "
            f"{cx:.3f},{cy + ry:.3f} {cx - rx:.3f},{cy:.3f}",
        )
        poly.set("fill", colors[n - 1 - k])


def _set_fill(element, color: str) -> None:
    """Set a region's fill. The SVG stores colors in inline `style="fill:#…"`,
    which overrides the `fill` attribute, so the style must be rewritten (resvg
    follows the same precedence, in both SVG and PDF)."""
    style = element.get("style") or ""
    if "fill:" in style:
        element.set(
            "style", re.sub(r"fill\s*:\s*[^;]*", f"fill:{color}", style, count=1)
        )
    else:
        element.set("style", (style + ";" if style else "") + f"fill:{color}")


def _set_label(root, label_id: str, text: str) -> None:
    """Set the visible text of an in-SVG label (text lives in its <tspan>)."""
    element = _by_id(root, label_id)
    if element is None:
        return
    tspan = element.find(f"{{{SVG_NS}}}tspan")
    (tspan if tspan is not None else element).text = text


# Rhombus position -> aspect, and the side labels. The two side rhombi (and their
# mewas) swap Capacity/Fortune by gender: male = Capacity left/Fortune right.
def _layout(gender) -> tuple[dict, dict]:
    capacity_left = gender == nc.Gender.MALE
    left, right = (
        (nc.Aspect.CAPACITY, nc.Aspect.FORTUNE)
        if capacity_left
        else (nc.Aspect.FORTUNE, nc.Aspect.CAPACITY)
    )
    left_mewa, right_mewa = (
        (nc.Aspect.MEWA_CAPACITY, nc.Aspect.MEWA_FORTUNE)
        if capacity_left
        else (nc.Aspect.MEWA_FORTUNE, nc.Aspect.MEWA_CAPACITY)
    )
    positions = {
        "life": nc.Aspect.LIFE,
        "body": nc.Aspect.BODY,
        "left": left,
        "right": right,
        "life_mewa": nc.Aspect.MEWA_LIFE,
        "body_mewa": nc.Aspect.MEWA_BODY,
        "left_mewa": left_mewa,
        "right_mewa": right_mewa,
    }
    labels = {
        "label_life": "Life",
        "label_body": "Body",
        "label_life_mewa": "Life Mewa",
        "label_body_mewa": "Body Mewa",
        "label_left_aspect": "Capacity" if capacity_left else "Fortune",
        "label_right_aspect": "Fortune" if capacity_left else "Capacity",
        "label_left_mewa": "Capacity Mewa" if capacity_left else "Fortune Mewa",
        "label_right_mewa": "Fortune Mewa" if capacity_left else "Capacity Mewa",
    }
    return positions, labels


def _band_colors(aspect, harmonized_aspect, mewa: int | None) -> list[str]:
    """Concentric band colors (innermost first) for one rhombus. Bands are the
    harmonization sequence; deep-water (mewa=2) applies only to the aspect's own
    center element, not to water threads in the sequence. The LIFE rhombus is ~3x
    the radius of the others, its sequence is woven once as-is, then twice more
    with the center color prepended."""
    center_color = _element_color(harmonized_aspect.center, mewa)
    sequence = [_element_color(e) for e in harmonized_aspect.harmonization_seq]
    if aspect == nc.Aspect.LIFE:
        return sequence + [center_color] + sequence + [center_color] + sequence
    return sequence


def fill_illustration(result, request) -> str:
    """Color the blank Namkha illustration from the result. Each rhombus
    is filled with concentric harmonization bands; the center diamond (aspect
    center element) and the outer frame (last/outermost color) sit on top. Side
    rhombi + labels follow gender. Returns SVG code."""
    tree = etree.parse(str(SVG_TEMPLATE))
    root = tree.getroot()
    positions, labels = _layout(request.subject.gender)
    by_aspect = {h.name: h for h in result.harmonized_aspects}
    for pos, aspect in positions.items():
        harmonized_aspect = by_aspect[aspect]
        mewa = result.mewa_numbers.get(aspect)
        center = _by_id(root, f"{pos}_rhombus_center")
        outer = _by_id(root, f"{pos}_rhombus_outer")
        if center is None or outer is None:
            continue
        group = center.getparent()
        colors = _band_colors(aspect, harmonized_aspect, mewa)
        _fill_bands(group, center, outer, colors)
        # Lift the center + outer frame above the freshly injected bands.
        for part in (center, outer):
            group.remove(part)
            group.append(part)
        _set_fill(center, _element_color(harmonized_aspect.center, mewa))
        _set_fill(outer, colors[-1])
    for label_id, text in labels.items():
        _set_label(root, label_id, text)
    return etree.tostring(root, encoding="unicode")


def _utc_offset(subject) -> str:
    aware = subject.birth_timezone.localize(subject.birth_datetime)
    total = int(aware.utcoffset().total_seconds())
    sign = "+" if total >= 0 else "-"
    h, m = divmod(abs(total), 3600)
    return f"(UTC{sign}{h}" + (f":{m // 60:02d}" if m else "") + ")"


def _build_data(result, request) -> dict:
    """Text fields for the Typst layout (everything that isn't color)."""
    subject = request.subject
    location = subject.birth_location
    coords = f"{location.latitude:.4f}, {location.longitude:.4f}"
    location_text = f"{location.name} ({coords})" if location.name else coords
    aspects = []
    for aspect, label in ASPECTS:
        harmonized_aspect = next(
            h for h in result.harmonized_aspects if h.name == aspect
        )
        mewa = result.mewa_numbers.get(aspect)
        tibetan, transcription = constants.ELEMENT_SYLLABLES[harmonized_aspect.center]
        center_color = (
            constants.MEWA_TWO_COLOR_NAME
            if harmonized_aspect.center == nc.Element.WATER and mewa == 2
            else constants.ELEMENT_COLOR_NAMES[harmonized_aspect.center]
        )
        aspects.append(
            {
                "label": label,
                "element": harmonized_aspect.center.value,
                "syllable_color": constants.ELEMENT_SYLLABLE_COLOR_NAMES[
                    harmonized_aspect.center
                ],
                "syllable_tibetan": tibetan,
                "syllable_roman": transcription,
                "center_color": center_color,
                "mewa": mewa,
                "sequence": " - ".join(
                    constants.ELEMENT_SEQ_ABBREV[e]
                    for e in harmonized_aspect.harmonization_seq
                ),
                "conflicted": bool(harmonized_aspect.is_conflicted),
            }
        )
    return {
        "subject": {
            "name": subject.name or "—",
            "gender": subject.gender.name.title(),
            "birth": f"{subject.birth_datetime:%Y-%m-%d %H:%M} {subject.birth_timezone} {_utc_offset(subject)}",
            "location": location_text,
        },
        "meta": {
            "type": request.namkha_type.name.title(),
            "method": {"CLASSIC": "Classic", "CNNR": "CNNR"}.get(
                request.method.name, request.method.name
            ),
            "birth_element": result.birth_element.value,
            "birth_animal": result.birth_animal.value,
            "birth_mewa": result.birth_mewa,
            "version": importlib.metadata.version("namkha-calculator"),
        },
        "aspects": aspects,
        "notes": [n.message for n in result.calculation_notes],
    }


@contextmanager
def _compile_tmpdir(result, request):
    """Fill SVG + data into a per-request tmpdir holding sheet.typ, ready to
    compile. image()/json() resolve relative to the .typ file, so the
    template, data, and illustration all live together here."""
    svg = fill_illustration(result, request)
    data = _build_data(result, request)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "namkha.svg").write_text(svg, encoding="utf-8")
        (tmpdir / "data.json").write_text(json.dumps(data), encoding="utf-8")
        (tmpdir / "logo.svg").write_bytes((TEMPLATES / "logo.svg").read_bytes())
        (tmpdir / "sheet.typ").write_text(
            TYP_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        yield tmpdir


def render_pdf(result, request) -> bytes:
    """Typst always returns single `bytes` for PDF, regardless of page count."""
    with _compile_tmpdir(result, request) as tmpdir:
        return typst.compile(
            str(tmpdir / "sheet.typ"),
            root=str(tmpdir),
            font_paths=[str(FONTS)],
            format="pdf",
        )



def _sanitize_svg(svg: bytes) -> bytes:
    """Strip script/foreignObject elements, event-handler attributes, and
    javascript: links from a Typst-rendered SVG page before it is embedded
    as raw HTML (`| safe`) in the result template. Typst's own SVG export is
    trusted, but this is a defense-in-depth backstop against future template
    or library changes that could let request-controlled text reach markup."""
    root = etree.fromstring(svg)
    for tag in _SVG_DANGEROUS_TAGS:
        for element in list(root.iter(tag)):
            element.getparent().remove(element)
    for element in root.iter():
        for attribute in list(element.attrib):
            local_name = etree.QName(attribute).localname
            if local_name.lower().startswith("on"):
                del element.attrib[attribute]
        for attribute in _SVG_LINK_ATTRIBUTES:
            value = element.get(attribute)
            if value and value.strip().lower().startswith("javascript:"):
                del element.attrib[attribute]
    return etree.tostring(root)


def render_svg(result, request) -> str:
    """Inline view. Typst returns single bytes for a one-page sheet, a list of
    per-page bytes otherwise; normalized to a list here so every page is
    stacked and nothing is truncated. Each page is sanitized before
    embedding, since the result template marks this output `| safe` (raw HTML)."""
    with _compile_tmpdir(result, request) as tmpdir:
        out = typst.compile(
            str(tmpdir / "sheet.typ"),
            root=str(tmpdir),
            font_paths=[str(FONTS)],
            format="svg",
        )
    pages = out if isinstance(out, list) else [out]
    return "\n".join(
        f'<div class="page">{_sanitize_svg(p).decode("utf-8")}</div>' for p in pages
    )
