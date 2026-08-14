"""build_request: each parse branch maps bad input to its user-facing message,
and a good form produces the right NamkhaRequest.

Hardening note: build_request reads `birth_date`/`birth_time` by subscript,
so a *missing* key raises KeyError -> 500, not the friendly message. The
HTML form always sends both keys (empty -> the friendly path below), so
that case is unreachable from the UI and is not tested here.
"""

import re

import namkha_calculator as nc
import pytest

from app.forms import (
    MAX_LOCATION_NAME_LENGTH,
    MAX_NAME_LENGTH,
    RESOLVE_AGAIN_MESSAGE,
    build_request,
)
from app.resolved_timezone import FIELD_NAME as RESOLVED_TIMEZONE_FIELD
from tests.support import resolved_timezone_field

_BASE_FORM = {
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


# The resolved time zone for the baseline form. Tests whose form cannot be
# resolved at all, such as a birth date of "not-a-date" with no time zone to
# find, pass this so they reach the parse error they are actually about.
_BASE_RESOLVED = resolved_timezone_field(_BASE_FORM)


def _good_form(**overrides):
    """A form as the page submits one, including the resolved time zone.

    The hidden field is worked out from whatever the form ends up saying, so a
    test overriding the date or the coordinates still gets a value that fits it.
    Tests about a value that does *not* fit pass the field themselves.
    """
    form = dict(_BASE_FORM)
    form.update(overrides)
    if RESOLVED_TIMEZONE_FIELD not in form:
        form[RESOLVED_TIMEZONE_FIELD] = resolved_timezone_field(form)
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


@pytest.mark.parametrize(
    "field, limit, message",
    [
        ("name", MAX_NAME_LENGTH, "Name must be 100 characters or fewer."),
        (
            "location_name",
            MAX_LOCATION_NAME_LENGTH,
            "Birth place must be 200 characters or fewer.",
        ),
    ],
)
def test_free_text_fields_are_length_capped(field, limit, message):
    """maxlength on the input is bypassable by a direct POST, so the cap has to
    hold here too. An unbounded name lands in the PDF download filename."""
    with pytest.raises(ValueError, match=re.escape(message)):
        build_request(_good_form(**{field: "x" * (limit + 1)}))


@pytest.mark.parametrize(
    "field, limit",
    [("name", MAX_NAME_LENGTH), ("location_name", MAX_LOCATION_NAME_LENGTH)],
)
def test_free_text_fields_accept_the_limit_exactly(field, limit):
    build_request(_good_form(**{field: "x" * limit}))


def test_length_cap_counts_characters_not_bytes():
    """A Tibetan name must get the same allowance as an ASCII one; counting
    bytes would cut it to a third."""
    request = build_request(_good_form(name="ཀ" * MAX_NAME_LENGTH))
    assert request.subject.name is not None


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


def test_the_resolved_timezone_is_used_as_submitted():
    request = build_request(_good_form())
    assert request.subject.resolved_timezone.key == "Asia/Kathmandu"


@pytest.mark.parametrize("field_value", ["", "   ", None])
def test_a_form_without_a_resolved_timezone_is_refused(field_value):
    """Nothing is missing by accident: the page always sends one. Filling the
    gap here would hide a broken form, and the zone we picked could differ from
    the one the user was shown."""
    form = _good_form()
    if field_value is None:
        del form[RESOLVED_TIMEZONE_FIELD]
    else:
        form[RESOLVED_TIMEZONE_FIELD] = field_value
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


@pytest.mark.parametrize(
    "field_value",
    [
        "nonsense",
        "v1|too|few|fields",
        # A value this app once wrote but no longer reads.
        "v0|USER_ZONE|Asia/Kathmandu||CERTAIN|0|_|27.7|85.32|1990-07-22||1582-10-15",
    ],
)
def test_an_unreadable_resolved_timezone_is_refused(field_value):
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(_good_form(**{RESOLVED_TIMEZONE_FIELD: field_value}))


# Real values with one component swapped for an impossible one, rather than
# hand-written lines: the field order belongs to app/resolved_timezone.py and
# restating it here would rot the moment it changes.
_UNKNOWN_ZONE_KEY = _BASE_RESOLVED.replace("Asia/Kathmandu", "Mars/Phobos", 1)
_ABSURD_OFFSET = resolved_timezone_field(
    dict(_BASE_FORM, timezone="", utc_offset="+5:45")
).replace("|20700|", "|100000|", 1)


@pytest.mark.parametrize(
    "field_value",
    [
        _UNKNOWN_ZONE_KEY,  # no tzdb has this key
        _ABSURD_OFFSET,  # 27 hours is past what a UTC offset can be
    ],
)
def test_a_timezone_that_cannot_be_rebuilt_is_refused(field_value):
    """These get past the field-by-field read and only fail when the time zone
    is actually built. Left to the calculation, they would come back as a 500;
    build_request promises every error it raises is safe to show."""
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(_good_form(**{RESOLVED_TIMEZONE_FIELD: field_value}))


@pytest.mark.parametrize(
    "moved",
    [
        {"birth_date": "1990-07-23"},
        {"latitude": "48.86"},
        {"longitude": "2.35"},
    ],
)
def test_details_moving_after_the_timezone_was_resolved_are_refused(moved):
    """Working the zone out again here would be slow and might contradict what
    the page already showed. Going ahead with the old one would print a sheet
    for a place or date the user has left behind. Refusing is what is left."""
    stale = _good_form()[RESOLVED_TIMEZONE_FIELD]
    form = _good_form(**moved, **{RESOLVED_TIMEZONE_FIELD: stale})
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


def test_the_birth_time_may_move_without_resolving_again():
    """`kept` is the resolved_timezone worked out for birth_time 08:15. `form`
    submits birth_time 09:40 with the same `kept` value. assert_binds compares
    only the date and the place, not the time, so build_request does not
    raise. The assert then checks that birth_datetime uses the new time."""
    kept = _good_form()[RESOLVED_TIMEZONE_FIELD]
    form = _good_form(birth_time="09:40", **{RESOLVED_TIMEZONE_FIELD: kept})
    assert build_request(form).subject.birth_datetime.hour == 9


def test_a_birth_on_a_fixed_offset_keeps_the_offset():
    """Offset mode is a whole branch of the form: it resolves a bare offset
    rather than a named zone, and no zone key survives it."""
    request = build_request(_good_form(timezone="", utc_offset="+5:45"))
    assert request.subject.resolved_timezone.key is None
    assert request.subject.resolved_timezone.offset_seconds == 20700


class TestTheZoneChoiceMustMatch:
    """The form goes on sending the zone the user picked, next to the value
    worked out for it. Nothing else compares the two: assert_binds looks at the
    place and the date, so a resolved Kathmandu submitted beside a chosen Tokyo
    would sail through and print a sheet for Kathmandu.

    There are three ways to choose, so three rules. A zone picked from the list
    must still be in `timezone`. An offset typed by hand must still be in
    `utc_offset`. A zone that came from the birth place must leave both empty.
    """

    def _keeping_the_resolved_value(self, resolved_with, **posted):
        """Resolve a zone one way, then submit it with something else posted."""
        resolved = _good_form(**resolved_with)[RESOLVED_TIMEZONE_FIELD]
        return _good_form(**posted, **{RESOLVED_TIMEZONE_FIELD: resolved})

    def test_a_different_chosen_zone_is_refused(self):
        form = self._keeping_the_resolved_value(
            {"timezone": "Asia/Kathmandu"}, timezone="Asia/Thimphu"
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_a_different_offset_is_refused(self):
        form = self._keeping_the_resolved_value(
            {"timezone": "", "utc_offset": "+5:45"}, timezone="", utc_offset="+5:30"
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_switching_to_the_other_mode_is_refused(self):
        """A zone was resolved, but the form now offers an offset instead."""
        form = self._keeping_the_resolved_value(
            {"timezone": "Asia/Kathmandu"}, timezone="", utc_offset="+5:45"
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_naming_a_zone_against_a_derived_value_is_refused(self):
        """The resolved value came from the birth place, with no zone picked.
        The form now sends one, so the two disagree about what was chosen."""
        form = self._keeping_the_resolved_value(
            {"timezone": ""}, timezone="Asia/Kathmandu"
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_dropping_a_chosen_zone_is_refused(self):
        """The resolved value came from a zone the user picked. The form now
        sends none, so the two disagree about what was chosen."""
        form = self._keeping_the_resolved_value(
            {"timezone": "Asia/Kathmandu"}, timezone=""
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    @pytest.mark.parametrize(
        "mode, came_from",
        [
            ({"timezone": "Asia/Kathmandu"}, nc.TimezoneProvenance.USER_ZONE),
            (
                {"timezone": "", "utc_offset": "+5:45"},
                nc.TimezoneProvenance.USER_OFFSET,
            ),
            ({"timezone": ""}, nc.TimezoneProvenance.LOCATION_DERIVED),
        ],
    )
    def test_every_mode_is_accepted_when_it_agrees(self, mode, came_from):
        """Asserting where the zone came from, not just that nothing was
        raised: all three rows would pass a bare call even if they all took
        the same branch."""
        request = build_request(_good_form(**mode))
        assert request.subject.resolved_timezone.provenance is came_from


class TestSummerTimeAnswerMatches:
    """The resolved value records the birth date, not the time of day, so it
    cannot police the summer-time answer itself. build_request does.

    Berlin 1985-09-29 02:30 falls in a repeated hour; noon that day does not.
    """

    FALL_BACK_DAY = {
        "latitude": "52.52",
        "longitude": "13.40",
        "timezone": "Europe/Berlin",
        "birth_date": "1985-09-29",
    }
    REPEATED = FALL_BACK_DAY | {"birth_time": "02:30"}
    ORDINARY = FALL_BACK_DAY | {"birth_time": "12:00"}

    def test_the_answer_given_when_it_was_resolved_is_accepted(self):
        request = build_request(_good_form(**self.REPEATED, on_summer_time="true"))
        assert request.subject.resolved_timezone.on_summer_time is True

    def test_a_different_answer_is_refused(self):
        resolved = _good_form(**self.REPEATED, on_summer_time="true")
        form = _good_form(
            **self.REPEATED,
            on_summer_time="false",
            **{RESOLVED_TIMEZONE_FIELD: resolved[RESOLVED_TIMEZONE_FIELD]},
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_dropping_the_answer_is_refused(self):
        resolved = _good_form(**self.REPEATED, on_summer_time="true")
        form = _good_form(
            **self.REPEATED,
            on_summer_time="",
            **{RESOLVED_TIMEZONE_FIELD: resolved[RESOLVED_TIMEZONE_FIELD]},
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_adding_an_answer_is_refused(self):
        resolved = _good_form(**self.REPEATED, on_summer_time="")
        form = _good_form(
            **self.REPEATED,
            on_summer_time="true",
            **{RESOLVED_TIMEZONE_FIELD: resolved[RESOLVED_TIMEZONE_FIELD]},
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_an_answer_to_an_ordinary_hour_is_not_a_mismatch(self):
        """Nothing repeats at noon, so resolve_timezone drops the answer and the
        resolved value records none. Refusing that would reject a form anyone
        can produce by picking Yes for an ordinary birth."""
        request = build_request(_good_form(**self.ORDINARY, on_summer_time="true"))
        assert request.subject.resolved_timezone.on_summer_time is None


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"birth_date": "", "birth_time": ""}, "Enter a valid birth date and time."),
        ({"birth_date": "not-a-date"}, "Enter a valid birth date and time."),
        ({"gender": "OTHER"}, "Select a gender."),
        (
            {"on_summer_time": "maybe"},
            "Choose yes, no, or not sure for summer time.",
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
    # These forms cannot be resolved, so they carry the baseline value. Every
    # message below comes from a parse that runs before the time zone is read.
    form = _good_form(**overrides, **{RESOLVED_TIMEZONE_FIELD: _BASE_RESOLVED})
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        build_request(form)
