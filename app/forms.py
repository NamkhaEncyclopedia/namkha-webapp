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

    Raises ValueError on bad input (bad number, unknown enum, bad coords).
    The library also raises ValueError for out-of-range coords/years.
    """
    name = (form.get("name") or "").strip() or None

    # birth_date "YYYY-MM-DD" + birth_time "HH:MM" -> naive local datetime.
    birth_datetime = datetime.fromisoformat(
        f"{form['birth_date']}T{form['birth_time']}"
    )

    subject = nc.Subject(
        name=name,
        gender=nc.Gender[form["gender"]],
        birth_datetime=birth_datetime,
        birth_timezone=pytz.timezone(form["timezone"]),
        birth_location=nc.Location(
            latitude=float(form["latitude"]),
            longitude=float(form["longitude"]),
        ),
    )
    return NamkhaRequest(
        subject=subject,
        namkha_type=nc.NamkhaType[form["namkha_type"]],
        method=nc.CalculationMethod[form["method"]],
    )
