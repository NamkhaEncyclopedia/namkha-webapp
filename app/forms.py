"""Turn raw form fields into the library's input types."""

from dataclasses import dataclass
from datetime import datetime

import namkha_calculator as nc
import pytz


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
        birth_timezone = pytz.timezone(form["timezone"])
    except pytz.UnknownTimeZoneError as exc:
        raise ValueError("Select a valid birth time zone.") from exc

    try:
        latitude = float(form["latitude"])
        longitude = float(form["longitude"])
    except ValueError as exc:
        raise ValueError("Latitude and longitude must be numbers.") from exc

    try:
        birth_location = nc.Location(latitude=latitude, longitude=longitude)
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

    subject = nc.Subject(
        name=name,
        gender=gender,
        birth_datetime=birth_datetime,
        birth_timezone=birth_timezone,
        birth_location=birth_location,
    )
    return NamkhaRequest(subject=subject, namkha_type=namkha_type, method=method)
