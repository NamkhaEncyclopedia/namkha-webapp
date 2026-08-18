"""Turn raw form fields into the library's input types."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import namkha_calculator as nc
from namkha_calculator.localization import is_ambiguous_local_time

from app.timezone_tickets import FIELD_NAME as TIMEZONE_TICKET_FIELD
from app.timezone_tickets import read_ticket


@dataclass(frozen=True)
class NamkhaRequest:
    """Frozen so it is hashable: the result cache keys on the request itself
    rather than restating its fields, and a new field joins the key for free."""

    subject: nc.Subject
    namkha_type: nc.NamkhaType
    method: nc.CalculationMethod


# Every field /calculate reads from the form. build_request parses them, and the
# event log records them. The download button carries none of them: it posts a
# result_id, and the server looks the request up by it.
FIELDS = (
    "name",
    "gender",
    "birth_date",
    "birth_time",
    "location_name",
    "timezone",
    "utc_offset",
    "on_summer_time",
    # Stands for the time zone the /timezone route worked out and kept.
    "timezone_ticket",
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

# Shown whenever the ticket is missing, the server no longer holds the zone it
# stands for, or that zone was worked out for something other than what was
# submitted. All three mean the same thing to the user: the answer on the page no
# longer belongs to these birth details.
RESOLVE_AGAIN_MESSAGE = (
    "Your birth details changed after the time zone was worked out. "
    "Please re-check the birth place and date and submit again."
)


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

    resolved_timezone = _timezone_from_form(form, birth_datetime)

    try:
        subject = nc.Subject(
            name=name,
            gender=gender,
            birth_datetime=birth_datetime,
            birth_location=birth_location,
            resolved_timezone=resolved_timezone,
        )
    except nc.StaleTimezoneError as exc:
        # Subject compares the value against the birth details it is given, so
        # this is where a place or date that moved on gets caught.
        raise ValueError(RESOLVE_AGAIN_MESSAGE) from exc
    except TypeError as exc:
        raise ValueError("Enter a valid birth date, time, and time zone.") from exc
    return NamkhaRequest(subject=subject, namkha_type=namkha_type, method=method)


def _timezone_from_form(form, birth_datetime: datetime) -> nc.ResolvedTimezone:
    """A zone the /timezone route worked out and kept, read back by its ticket."""
    resolved_timezone = read_ticket(form.get(TIMEZONE_TICKET_FIELD))
    if resolved_timezone is None:
        raise ValueError(RESOLVE_AGAIN_MESSAGE)

    # assert_binds covers the place and the date. The next two are what it
    # cannot see, so they are compared here or nowhere.
    if not _zone_choice_matches(form, resolved_timezone):
        raise ValueError(RESOLVE_AGAIN_MESSAGE)

    # resolved_timezone stores the birth date, not the time of day, so it cannot
    # tell whether this summer-time answer still fits. Compare it here instead,
    # and only when the clock really repeats this hour: resolve_timezone stores
    # None for any other birth time, so a "Yes" would read as a mismatch.
    posted = parse_on_summer_time(form.get("on_summer_time") or "")
    if posted != resolved_timezone.on_summer_time and is_ambiguous_local_time(
        birth_datetime, resolved_timezone.tzinfo
    ):
        raise ValueError(RESOLVE_AGAIN_MESSAGE)
    return resolved_timezone


def _zone_choice_matches(form, resolved_timezone: nc.ResolvedTimezone) -> bool:
    """Whether the zone or offset on the form is the one that was resolved.

    The form keeps sending what the user picked, so the two can disagree. A
    value that resolved to one zone, submitted next to a different chosen zone,
    would print a sheet for neither. Which field should hold the answer follows
    from where the resolved value came from.
    """
    posted_zone = (form.get("timezone") or "").strip()
    posted_offset = (form.get("utc_offset") or "").strip()

    if resolved_timezone.provenance is nc.TimezoneProvenance.USER_ZONE:
        return not posted_offset and posted_zone == resolved_timezone.key

    if resolved_timezone.provenance is nc.TimezoneProvenance.USER_OFFSET:
        if posted_zone or not posted_offset:
            return False
        try:
            offset = parse_utc_offset(posted_offset)
        except ValueError:
            # Unreadable, so it cannot be the offset that was resolved.
            return False
        return round(offset.total_seconds()) == resolved_timezone.offset_seconds

    # Worked out from the birth place, which happens only when the user named
    # no zone and no offset.
    return not posted_zone and not posted_offset
