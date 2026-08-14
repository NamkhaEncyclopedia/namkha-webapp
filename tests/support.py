"""Helpers shared by conftest and the test modules.

Not in conftest.py: there is a second conftest under tests/browser/, so a plain
`import conftest` picks up whichever one the collector reached first.

Never hand-write a serialized `resolved_timezone` string in a test. It rots
when the field list or version marker in app/resolved_timezone.py changes, and
a rotted value that still parses is believed rather than caught.
"""

from datetime import datetime

import namkha_calculator as nc
from namkha_calculator.zone_derivation import resolve_timezone

from app.forms import parse_on_summer_time, parse_utc_offset
from app.resolved_timezone import serialize_resolved_timezone


def resolve_timezone_at(
    latitude, longitude, birth_datetime, timezone=None, offset=None, on_summer_time=None
):
    """Resolve a time zone, the way the /timezone route does."""
    return resolve_timezone(
        nc.Location(latitude=latitude, longitude=longitude),
        birth_datetime,
        zone_key=timezone,
        offset=offset,
        on_summer_time=on_summer_time,
    )


def resolved_timezone_field(form):
    """Make the `resolved_timezone` field value that goes with this form.

    The date, time, place and zone choice come out of the form itself, so the
    text belongs to that exact form. Subject refuses one worked out for a different
    date or place, so call this again after changing any of them.
    """
    birth_datetime = datetime.fromisoformat(
        f"{form['birth_date']}T{form['birth_time']}"
    )
    utc_offset = (form.get("utc_offset") or "").strip()
    resolved = resolve_timezone_at(
        float(form["latitude"]),
        float(form["longitude"]),
        birth_datetime,
        timezone=(form.get("timezone") or "").strip() or None,
        offset=parse_utc_offset(utc_offset) if utc_offset else None,
        on_summer_time=parse_on_summer_time(form.get("on_summer_time") or ""),
    )
    return serialize_resolved_timezone(resolved)
