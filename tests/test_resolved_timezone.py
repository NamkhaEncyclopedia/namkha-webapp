"""The hidden field that carries a settled time zone through the form."""

import datetime as dt

import namkha_calculator as nc
import pytest
from namkha_calculator.zone_derivation import derive_timezone

from app.resolved_timezone import (
    ResolvedTimezoneParseError,
    parse_resolved_timezone,
    serialize_resolved_timezone,
)

BERLIN = nc.Location(latitude=52.52, longitude=13.405)
LVIV = nc.Location(latitude=49.8397, longitude=24.0297)
KATHMANDU = nc.Location(latitude=27.7172, longitude=85.3240)
PACIFIC = nc.Location(latitude=0.0, longitude=-140.0)


def _round_trip(resolved: nc.ResolvedTimezone) -> nc.ResolvedTimezone:
    return parse_resolved_timezone(serialize_resolved_timezone(resolved))


@pytest.mark.parametrize(
    "resolved",
    [
        # A modern birth: a named zone, certain.
        derive_timezone(BERLIN, dt.datetime(1985, 6, 15, 12)),
        # A historical one, where the zone and the modern zone differ.
        derive_timezone(LVIV, dt.datetime(1940, 6, 15, 12)),
        # Open water, where the offset came from longitude.
        derive_timezone(PACIFIC, dt.datetime(1900, 1, 1, 12)),
        # A zone the user chose.
        derive_timezone(
            KATHMANDU, dt.datetime(1985, 6, 15, 12), zone_key="Asia/Kathmandu"
        ),
        # An offset the user provided, with each possible answer about summer time.
        derive_timezone(
            KATHMANDU, dt.datetime(1985, 6, 15, 12), offset=dt.timedelta(minutes=345)
        ),
        derive_timezone(BERLIN, dt.datetime(1985, 6, 15, 12), on_summer_time=True),
        derive_timezone(BERLIN, dt.datetime(1985, 6, 15, 12), on_summer_time=False),
    ],
)
def test_survives_the_round_trip(resolved):
    assert _round_trip(resolved) == resolved


def test_the_rebuilt_timezone_still_matches():
    resolved = derive_timezone(PACIFIC, dt.datetime(1900, 1, 1, 12))
    assert _round_trip(resolved).tzinfo.utcoffset(None) == resolved.tzinfo.utcoffset(
        None
    )


def test_a_serialized_value_is_readable():
    resolved = derive_timezone(LVIV, dt.datetime(1940, 6, 15, 12))
    text = serialize_resolved_timezone(resolved)
    assert text.startswith("v1|LOCATION_DERIVED|Europe/Warsaw||BORDERS_UNCERTAIN|")
    assert text.endswith("|1940-06-15|Europe/Kyiv|1918-02-14")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a resolved timezone",
        "v1|LOCATION_DERIVED|Europe/Berlin",  # too few fields
        "v2|LOCATION_DERIVED|Europe/Berlin||CERTAIN|0|_|52.52|13.405|1985-06-15|Europe/Berlin|1700-03-01",
        "v1|NOT_A_PROVENANCE|Europe/Berlin||CERTAIN|0|_|52.52|13.405|1985-06-15|Europe/Berlin|1700-03-01",
        "v1|LOCATION_DERIVED|Europe/Berlin||CERTAIN|0|maybe|52.52|13.405|1985-06-15|Europe/Berlin|1700-03-01",
        "v1|LOCATION_DERIVED|Europe/Berlin||CERTAIN|0|_|north|13.405|1985-06-15|Europe/Berlin|1700-03-01",
        # Both a zone and an offset: the library refuses it.
        "v1|LOCATION_DERIVED|Europe/Berlin|3600|CERTAIN|0|_|52.52|13.405|1985-06-15|Europe/Berlin|1700-03-01",
    ],
)
def test_refuses_what_it_cannot_read(text):
    with pytest.raises(ResolvedTimezoneParseError):
        parse_resolved_timezone(text)
