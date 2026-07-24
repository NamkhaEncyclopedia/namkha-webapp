"""build_request: each parse branch maps bad input to its user-facing message,
and a good form produces the right NamkhaRequest.

Hardening note: build_request reads `birth_date`/`birth_time` by subscript
(forms.py:42), so a *missing* key raises KeyError -> 500, not the friendly
message. The HTML form always sends both keys (empty -> the friendly path
below), so that case is unreachable from the UI and is not tested here.
"""

import re
from datetime import timedelta

import namkha_calculator as nc
import pytest

from app.forms import build_request


def _good_form(**overrides):
    form = {
        "name": "Jane",
        "gender": "FEMALE",
        "birth_date": "1990-07-22",
        "birth_time": "08:15",
        "timezone": "Asia/Kathmandu",
        "latitude": "27.70",
        "longitude": "85.32",
        "namkha_type": "YEAR",
        "method": "CNNR",
    }
    form.update(overrides)
    return form


def test_happy_path_builds_request():
    request = build_request(_good_form())
    assert request.namkha_type == nc.NamkhaType.YEAR
    assert request.method == nc.CalculationMethod.CNNR
    assert request.subject.name == "Jane"
    assert request.subject.gender == nc.Gender.FEMALE
    assert request.subject.birth_location.latitude == pytest.approx(27.70)


@pytest.mark.parametrize("name_value", ["", "   ", None])
def test_blank_name_becomes_none(name_value):
    request = build_request(_good_form(name=name_value))
    assert request.subject.name is None


def test_location_name_sets_location_name():
    # Need not be a city: a village/monastery/ranch label flows through verbatim.
    request = build_request(_good_form(location_name="Tashigang Gonpa, Bhutan"))
    assert request.subject.birth_location.name == "Tashigang Gonpa, Bhutan"


@pytest.mark.parametrize(
    "overrides", [{}, {"location_name": ""}, {"location_name": "  "}]
)
def test_absent_or_blank_location_name_is_none(overrides):
    request = build_request(_good_form(**overrides))
    assert request.subject.birth_location.name is None


def test_explicit_timezone_is_passed_through():
    request = build_request(_good_form())
    assert request.subject.birth_timezone == nc.zone("Asia/Kathmandu")


@pytest.mark.parametrize("timezone_value", ["", "  ", None])
def test_blank_timezone_lets_library_derive(timezone_value):
    # Automatic mode submits an empty zone -> None -> derived from the place.
    request = build_request(_good_form(timezone=timezone_value))
    assert request.subject.birth_timezone is None


@pytest.mark.parametrize(
    "utc_offset, expected",
    [
        ("+5:45", timedelta(hours=5, minutes=45)),
        ("+5", timedelta(hours=5)),
    ],
)
def test_utc_offset_builds_fixed_offset(utc_offset, expected):
    request = build_request(_good_form(timezone="", utc_offset=utc_offset))
    assert request.subject.birth_timezone == nc.fixed_offset(expected)


def test_negative_utc_offset():
    # Location whose longitude matches -3:00, or Subject rejects the pair.
    request = build_request(
        _good_form(timezone="", utc_offset="-3", latitude="-10", longitude="-45")
    )
    assert request.subject.birth_timezone == nc.fixed_offset(timedelta(hours=-3))


def test_utc_offset_wins_over_timezone():
    request = build_request(_good_form(utc_offset="+5:45"))
    assert request.subject.birth_timezone == nc.fixed_offset(
        timedelta(hours=5, minutes=45)
    )


@pytest.mark.parametrize("utc_offset_value", ["", "  ", None])
def test_blank_utc_offset_falls_through_to_timezone(utc_offset_value):
    request = build_request(_good_form(utc_offset=utc_offset_value))
    assert request.subject.birth_timezone == nc.zone("Asia/Kathmandu")


@pytest.mark.parametrize(
    "raw, expected",
    [("", None), ("  ", None), (None, None), ("true", True), ("false", False)],
)
def test_on_summer_time_tri_state(raw, expected):
    form = _good_form()
    if raw is not None:
        form["on_summer_time"] = raw
    request = build_request(form)
    assert request.subject.on_summer_time is expected


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"birth_date": "", "birth_time": ""}, "Enter a valid birth date and time."),
        ({"birth_date": "not-a-date"}, "Enter a valid birth date and time."),
        ({"gender": "OTHER"}, "Select a gender."),
        ({"timezone": "Mars/Phobos"}, "Select a valid birth time zone."),
        ({"utc_offset": "abc"}, "Enter a UTC offset like +5:45 or -3:30."),
        ({"utc_offset": "5:45"}, "Enter a UTC offset like +5:45 or -3:30."),
        ({"utc_offset": "+2:75"}, "Enter a UTC offset like +5:45 or -3:30."),
        ({"utc_offset": "+17"}, "UTC offset must be between -16:00 and +16:00."),
        ({"utc_offset": "+16:30"}, "UTC offset must be between -16:00 and +16:00."),
        (
            {"on_summer_time": "maybe"},
            "Select whether summer time was in effect at birth.",
        ),
        ({"latitude": "abc"}, "Latitude and longitude must be numbers."),
        (
            {"latitude": "200"},
            "Latitude must be between -90 and 90, longitude between -180 and 180.",
        ),
        ({"namkha_type": "DECADE"}, "Select a valid Namkha type."),
        ({"method": "MYSTERY"}, "Select a valid calculation method."),
    ],
)
def test_bad_input_raises_user_message(overrides, message):
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        build_request(_good_form(**overrides))
