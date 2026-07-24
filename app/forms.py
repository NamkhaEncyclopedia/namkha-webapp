"""Turn raw form fields into the library's input types."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfoNotFoundError

import namkha_calculator as nc


@dataclass
class NamkhaRequest:
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
    "latitude",
    "longitude",
    "namkha_type",
    "method",
)


def build_request(form) -> NamkhaRequest:
    """Parse a form mapping into a NamkhaRequest.

    Raises ValueError with a user-facing message on bad input. The library's
    own ValueErrors (method/type mismatch, unsupported birth year) come out
    of nc.calculate_namkha instead and are mapped separately by the caller.
    """
    name = (form.get("name") or "").strip() or None

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
        birth_timezone = nc.zone(form.get("timezone") or "")
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Select a valid birth time zone.") from exc

    try:
        latitude = float(form["latitude"])
        longitude = float(form["longitude"])
    except ValueError as exc:
        raise ValueError("Latitude and longitude must be numbers.") from exc

    # `location_name` is the place field text: the autocomplete label, or whatever
    # the user typed in manual-coordinate mode. Blank -> no name (bare coordinates).
    place_name = (form.get("location_name") or "").strip() or None
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
            birth_location=birth_location,
        )
    except TypeError as exc:
        raise ValueError("Enter a valid birth date, time, and time zone.") from exc
    except ValueError as exc:
        if "longitude" in str(exc):
            raise ValueError(
                "The selected time zone does not match the birth location. "
                "Check the place and the time zone."
            ) from exc
        raise ValueError(
            "Could not use this birth date, time, and place; check the values."
        ) from exc
    return NamkhaRequest(subject=subject, namkha_type=namkha_type, method=method)
