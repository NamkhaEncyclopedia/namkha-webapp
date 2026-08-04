"""Turn raw form fields into the library's input types."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import namkha_calculator as nc


@dataclass(frozen=True)
class NamkhaRequest:
    """Frozen so it is hashable: the result cache keys on the request itself
    rather than restating its fields, and a new field joins the key for free."""

    subject: nc.Subject
    namkha_type: nc.NamkhaType
    method: nc.CalculationMethod


# Field names shared by the form and the hidden inputs on the download button.
FIELDS = (
    "name",
    "gender",
    "birth_date",
    "birth_time",
    "location_name",
    "timezone",
    "utc_offset",
    "on_summer_time",
    "latitude",
    "longitude",
    "namkha_type",
    "method",
)

# Caps on the two free-text fields. Both end up on the sheet, and the name also
# becomes the PDF download filename -- an unbounded one produces a
# Content-Disposition header past what fly-proxy and h11 accept, so the download
# fails. Enforced here rather than only as maxlength on the inputs, which a
# direct POST skips. Counted in characters, not bytes: the limit must mean the
# same thing for a Tibetan name as for an ASCII one.
MAX_NAME_LENGTH = 100
MAX_LOCATION_NAME_LENGTH = 200


# Signed hours with optional minutes: +5, -3:30, +05:45.
UTC_OFFSET_PATTERN = re.compile(r"([+-])(\d{1,2})(?::([0-5]\d))?")

# Widest offset any real clock has kept, matching the library's own bound.
MAX_UTC_OFFSET = timedelta(hours=16)

# The summer-time select. Blank is a viable answer: the user is not sure.
ON_SUMMER_TIME_VALUES = {"": None, "true": True, "false": False}


def parse_utc_offset(text: str) -> timedelta:
    """A "+-HH:MM" style offset as a timedelta."""
    match = UTC_OFFSET_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("Enter a UTC offset like +5:45 or -3:30.")
    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0))
    if offset > MAX_UTC_OFFSET:
        raise ValueError("UTC offset must be between -16:00 and +16:00.")
    return offset if match.group(1) == "+" else -offset


def parse_on_summer_time(text: str) -> bool | None:
    """Whether summer time was in effect at birth, or None when unknown."""
    answer = text.strip()
    if answer not in ON_SUMMER_TIME_VALUES:
        raise ValueError("Choose yes, no, or not sure for summer time.")
    return ON_SUMMER_TIME_VALUES[answer]


def build_request(form) -> NamkhaRequest:
    """Parse a form mapping into a NamkhaRequest.

    Raises ValueError with a user-facing message on bad input. The library's
    own ValueErrors (method/type mismatch, unsupported birth year) come out
    of nc.calculate_namkha instead and are mapped separately by the caller.
    """
    name = (form.get("name") or "").strip() or None
    if name is not None and len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be {MAX_NAME_LENGTH} characters or fewer.")

    # birth_date "YYYY-MM-DD" + birth_time "HH:MM" -> naive local datetime.
    try:
        birth_datetime = datetime.fromisoformat(
            f"{form['birth_date']}T{form['birth_time']}"
        )
    except ValueError as exc:
        raise ValueError("Enter a valid birth date and time.") from exc

    try:
        gender = nc.Gender[form["gender"]]
    except KeyError as exc:
        raise ValueError("Select a gender.") from exc

    # Three time-zone modes: manual UTC offset, zone from the list, or empty ->
    # None, the library derives the zone from the birth place and date.
    timezone_name = (form.get("timezone") or "").strip()
    utc_offset_text = (form.get("utc_offset") or "").strip()
    if utc_offset_text:
        birth_timezone = nc.fixed_offset(parse_utc_offset(utc_offset_text))
    elif timezone_name:
        try:
            birth_timezone = nc.zone(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Select a valid birth time zone.") from exc
    else:
        birth_timezone = None

    # Tri-state for a birth time in the repeated fall-back hour: unset means the
    # library guesses and notes it, yes/no pin the reading.
    on_summer_time = parse_on_summer_time(form.get("on_summer_time") or "")

    try:
        latitude = float(form["latitude"])
        longitude = float(form["longitude"])
    except ValueError as exc:
        raise ValueError("Latitude and longitude must be numbers.") from exc

    # `location_name` is the place field text: the autocomplete label, or whatever
    # the user typed in manual-coordinate mode. Blank -> no name (bare coordinates).
    place_name = (form.get("location_name") or "").strip() or None
    if place_name is not None and len(place_name) > MAX_LOCATION_NAME_LENGTH:
        raise ValueError(
            f"Birth place must be {MAX_LOCATION_NAME_LENGTH} characters or fewer."
        )
    try:
        birth_location = nc.Location(
            latitude=latitude, longitude=longitude, name=place_name
        )
    except ValueError as exc:
        raise ValueError(
            "Latitude must be between -90 and 90, longitude between -180 and 180."
        ) from exc

    try:
        namkha_type = nc.NamkhaType[form["namkha_type"]]
    except KeyError as exc:
        raise ValueError("Select a valid Namkha type.") from exc

    try:
        method = nc.CalculationMethod[form["method"]]
    except KeyError as exc:
        raise ValueError("Select a valid calculation method.") from exc

    try:
        subject = nc.Subject(
            name=name,
            gender=gender,
            birth_datetime=birth_datetime,
            birth_timezone=birth_timezone,
            on_summer_time=on_summer_time,
            birth_location=birth_location,
        )
    except TypeError as exc:
        raise ValueError("Enter a valid birth date, time, and time zone.") from exc
    except ValueError as exc:
        if "outside the real-timezone range" in str(exc):
            raise ValueError("UTC offset must be between -16:00 and +16:00.") from exc
        if "longitude" in str(exc):
            raise ValueError(
                "The selected time zone does not match the birth location. "
                "Check the place and the time zone."
            ) from exc
        raise ValueError(
            "Could not use this birth date, time, and place; check the values."
        ) from exc
    return NamkhaRequest(subject=subject, namkha_type=namkha_type, method=method)
