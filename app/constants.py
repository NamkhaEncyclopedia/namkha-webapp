"""Static option lists and color map."""

import importlib.metadata
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

import namkha_calculator as nc

# Versions surfaced in the UI as flat badges, computed once at import.
# Library: same call the sheet render uses, so page and sheet never disagree.
# App: read from pyproject (package-mode=false, so not an installed dist).
# Both wrapped: a missing dist or pyproject.toml must not break app startup.
try:
    LIBRARY_VERSION = importlib.metadata.version("namkha-calculator")
except importlib.metadata.PackageNotFoundError:
    LIBRARY_VERSION = "unknown"

_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
try:
    APP_VERSION = tomllib.loads(_PYPROJECT_PATH.read_text())["project"]["version"]
except OSError as error:
    raise RuntimeError(f"could not read {_PYPROJECT_PATH} for app version") from error
except tomllib.TOMLDecodeError as error:
    raise RuntimeError(f"could not parse {_PYPROJECT_PATH} as TOML") from error
except KeyError as error:
    raise RuntimeError(f"{_PYPROJECT_PATH} missing [project].version") from error


def _prerelease_label(version: str) -> str | None:
    """PEP 440 pre-release segment -> short badge text; None for a stable release."""
    match = re.search(r"(a|b|rc)\d*", version)
    return {"a": "alpha", "b": "beta", "rc": "rc"}[match.group(1)] if match else None


# Project-wide pre-release marker (the calculation engine governs result validity);
# shown on both badges. Self-clears once the library ships a stable release.
PRERELEASE_LABEL = _prerelease_label(LIBRARY_VERSION)  # "alpha" for 0.1.0a4

# Cloudflare Turnstile widget for the calculator form. A sitekey is public by
# design -- it ships in the page HTML -- so the production one lives here in
# plain sight; only TURNSTILE_SECRET is confidential (see app/turnstile.py).
# Override for local development with Cloudflare's always-passes test sitekey,
# which works on any hostname: TURNSTILE_SITEKEY=1x00000000000000000000AA.
TURNSTILE_SITEKEY = os.getenv("TURNSTILE_SITEKEY", "0x4AAAAAAD9sLh4BbknGiGZq")

# Element -> hex color. METAL is near-white, so swatches need a stroke.
ELEMENT_COLORS: dict[nc.Element, str] = {
    nc.Element.WOOD: "#3ABE5B",
    nc.Element.FIRE: "#C81D24",
    nc.Element.EARTH: "#FBD448",
    nc.Element.METAL: "#F9F7F8",
    nc.Element.WATER: "#3E7DC6",
}

# Deep-water variant: a Water cell whose mewa number is 2 uses dark blue.
MEWA_TWO_COLOR = "#2A276D"

ELEMENT_COLOR_NAMES: dict[nc.Element, str] = {
    nc.Element.WATER: "Blue",
    nc.Element.WOOD: "Green",
    nc.Element.FIRE: "Red",
    nc.Element.EARTH: "Yellow",
    nc.Element.METAL: "White",
}
MEWA_TWO_COLOR_NAME = "Dark Blue"

# Tibetan syllable and its CAPS romanisation per element.
ELEMENT_SYLLABLES: dict[nc.Element, tuple[str, str]] = {
    nc.Element.WATER: ("བྃ", "BAM"),
    nc.Element.WOOD: ("ཡྃ", "YAM"),
    nc.Element.FIRE: ("ཪྃ", "RAM"),
    nc.Element.EARTH: ("ལྃ", "LAM"),
    nc.Element.METAL: ("ལྃ", "LAM"),
}

# Color name of the syllable (thread color); Water/deep-water syllable is white.
ELEMENT_SYLLABLE_COLOR_NAMES: dict[nc.Element, str] = {
    nc.Element.WATER: "White",
    nc.Element.WOOD: "Green",
    nc.Element.FIRE: "Red",
    nc.Element.EARTH: "Yellow",
    nc.Element.METAL: "Yellow",
}

# Single-letter abbreviation for concise seq display (by color initial).
ELEMENT_SEQ_ABBREV: dict[nc.Element, str] = {
    nc.Element.WATER: "B",
    nc.Element.WOOD: "G",
    nc.Element.FIRE: "R",
    nc.Element.EARTH: "Y",
    nc.Element.METAL: "W",
}

# Plain-language rewrites of the library's calculation notes, keyed by note
# identity. The library's own messages are aimed at developers (they name
# internal settings and use technical terms); these are what the sheet shows a
# reader instead. Any note without an entry here falls back to the library text.
NOTE_MESSAGES: dict[nc.CalculationNote, str] = {
    nc.CalculationNote.HIGH_LATITUDE: (
        "The birth place lies far north or south, so standard sunrise and "
        "sunset times were used."
    ),
    nc.CalculationNote.PERIOD_BOUNDARY: (
        "The birth time falls right at the turning point between two periods, "
        "where even a small error could change the result. Please make sure the time "
        "is exact."
    ),
    nc.CalculationNote.AMBIGUOUS_LOCAL_TIME: (
        "On this date the clocks were set back, so this hour happened twice, and "
        "the later one was assumed. If you know whether summer time was in effect "
        "at birth, please specify it."
    ),
    nc.CalculationNote.AMBIGUOUS_LOCAL_TIME_RESOLVED: (
        "On this date the clocks were set back, so this hour happened twice. It "
        "was resolved using the summer-time answer you gave."
    ),
    nc.CalculationNote.LOCAL_MEAN_TIME: (
        "The birth time was treated as sun-based local time rather than a "
        "standard clock time. This happens for births before standard clocks "
        "reached the region, or at sea and other places with no official time."
    ),
    nc.CalculationNote.PRE_GREGORIAN_DATE: (
        "On this date the birth place had not yet adopted Gregorian calendar. If "
        "the original record used Julian calendar, be sure to convert the date to "
        "Gregorian first."
    ),
    nc.CalculationNote.TIMEZONE_ESTIMATED: (
        "The exact time zone for this place and date could not be confirmed, so "
        "the best available historical local time was used. If you know the "
        "official local time, please enter it."
    ),
    nc.CalculationNote.TIMEZONE_BORDERS_UNCERTAIN: (
        "Borders near the birth place shifted around the birth year, so even "
        "which country's time applied is unclear. The best available historical "
        "local time was used. If you know the official local time, please enter it."
    ),
}

# Form dropdown options: (form value, label).
GENDERS = [(gender.name, gender.name.title()) for gender in nc.Gender]
NAMKHA_TYPES = [
    (namkha_type.name, namkha_type.name.title()) for namkha_type in nc.NamkhaType
]
METHOD_LABELS = {"CLASSIC": "Classic", "CNNR": "C. N. Norbu"}
METHODS = [
    (method.name, METHOD_LABELS.get(method.name, method.name.title()))
    for method in nc.CalculationMethod
]


def format_utc_offset(aware_datetime: datetime) -> str:
    """An aware datetime's UTC offset as 'UTC+5:45'. Bare, so each caller adds
    its own punctuation; used for both the picker labels and the sheet."""
    total = int(aware_datetime.utcoffset().total_seconds())  # type: ignore[union-attr]
    sign = "+" if total >= 0 else "-"
    hours, seconds = divmod(abs(total), 3600)
    return f"UTC{sign}{hours}:{seconds // 60:02d}"


def _tz_label(zone_name: str) -> str:
    """e.g. 'Asia/Kathmandu (UTC+5:45)'. Offset is the CURRENT one (DST included
    if in effect now) -- a display hint in the picker, not the offset used for
    the actual birth date, which the render computes separately."""
    return f"{zone_name} ({format_utc_offset(datetime.now(nc.zone(zone_name)))})"


# Geographic zones from the library's bundled zone.tab: the canonical picker
# set, without the legacy aliases the full tzdata tree also carries.
TIMEZONES = [
    (zone_name, _tz_label(zone_name)) for zone_name in sorted(set(nc.zone_keys()))
]
