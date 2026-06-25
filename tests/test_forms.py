"""build_request: each parse branch maps bad input to its user-facing message,
and a good form produces the right NamkhaRequest.

Hardening note: build_request reads `birth_date`/`birth_time` by subscript
(forms.py:42), so a *missing* key raises KeyError -> 500, not the friendly
message. The HTML form always sends both keys (empty -> the friendly path
below), so that case is unreachable from the UI and is not tested here.
"""

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


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"birth_date": "", "birth_time": ""}, "Enter a valid birth date and time."),
        ({"birth_date": "not-a-date"}, "Enter a valid birth date and time."),
        ({"gender": "OTHER"}, "Select a gender."),
        ({"timezone": "Mars/Phobos"}, "Select a valid birth time zone."),
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
    with pytest.raises(ValueError, match=f"^{message}$"):
        build_request(_good_form(**overrides))
