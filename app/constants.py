"""Static option lists, color map, and sample data for the preview route."""

from datetime import datetime

import namkha_calculator as nc
import pytz

# Element -> hex color. METAL is near-white, so swatches need a stroke.
ELEMENT_COLORS: dict[nc.Element, str] = {
    nc.Element.WOOD: "#3ABE5B",
    nc.Element.FIRE: "#E53B43",
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

# Form dropdown options: (form value, label).
GENDERS = [(gender.name, gender.name.title()) for gender in nc.Gender]
NAMKHA_TYPES = [
    (namkha_type.name, namkha_type.name.title()) for namkha_type in nc.NamkhaType
]
METHODS = [(method.name, method.name) for method in nc.CalculationMethod]
TIMEZONES = pytz.common_timezones

# TODO: remove before release
# Sample input used by GET /preview so the sheet shows real data while editing.
SAMPLE_SUBJECT = nc.Subject(
    name="Sample Person",
    gender=nc.Gender.MALE,
    birth_datetime=datetime(1985, 3, 15, 14, 30),
    birth_timezone=pytz.timezone("Europe/Berlin"),
    birth_location=nc.Location(latitude=52.52, longitude=13.40, name="Berlin"),
)
SAMPLE_NAMKHA_TYPE = nc.NamkhaType.YEAR
SAMPLE_METHOD = nc.CalculationMethod.CLASSIC
