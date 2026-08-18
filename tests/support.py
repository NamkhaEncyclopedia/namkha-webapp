"""Helpers shared by conftest and the test modules.

Not in conftest.py: there is a second conftest under tests/browser/, so a plain
`import conftest` picks up whichever one the collector reached first.

A `timezone_ticket` cannot be hand-written: only the running process knows which
tickets it issued. Generate one through timezone_ticket_field below, inside the test,
because the autouse fixture empties the store around every test.
"""

from datetime import datetime

import namkha_calculator as nc
from namkha_calculator.zone_derivation import resolve_timezone

from app.forms import parse_on_summer_time, parse_utc_offset
from app.timezone_tickets import issue_ticket


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


def timezone_ticket_field(form):
    """Resolve the zone this form describes and return a ticket for it.

    The date, time, place and zone choice come out of the form itself, so the
    zone belongs to that exact form. Subject refuses one worked out for a different
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
    return issue_ticket(resolved)
