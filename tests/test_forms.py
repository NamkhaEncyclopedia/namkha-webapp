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
from app.timezone_tickets import FIELD_NAME as TIMEZONE_TICKET_FIELD
from app.timezone_tickets import SESSION_TTL, _tickets
from tests.support import timezone_ticket_field

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


def _base_ticket():
    """A ticket for the baseline form. Tests whose own form cannot be resolved at
    all, such as a birth date of "not-a-date" with no time zone to find, pass this
    so they reach the parse error they are actually about.
    """
    return timezone_ticket_field(_BASE_FORM)


def _good_form(**overrides):
    """A form as the page submits one, including the time zone ticket.

    The ticket stands for a zone worked out from whatever the form ends up saying,
    so a test overriding the date or the coordinates still gets one that fits it.
    Tests about a ticket that does *not* fit pass the field themselves.
    """
    form = dict(_BASE_FORM)
    form.update(overrides)
    if TIMEZONE_TICKET_FIELD not in form:
        form[TIMEZONE_TICKET_FIELD] = timezone_ticket_field(form)
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


def test_the_zone_behind_the_ticket_is_the_one_used():
    request = build_request(_good_form())
    assert request.subject.resolved_timezone.key == "Asia/Kathmandu"


@pytest.mark.parametrize("field_value", ["", "   ", None])
def test_a_form_without_a_ticket_is_refused(field_value):
    """Nothing is missing by accident: the page always sends one. Filling the
    gap here would hide a broken form, and the zone we picked could differ from
    the one the user was shown."""
    form = _good_form()
    if field_value is None:
        del form[TIMEZONE_TICKET_FIELD]
    else:
        form[TIMEZONE_TICKET_FIELD] = field_value
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


def test_a_ticket_the_server_never_issued_is_refused():
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(_good_form(**{TIMEZONE_TICKET_FIELD: "not-a-real-ticket"}))


def test_an_expired_ticket_is_refused():
    form = _good_form()
    ticket = form[TIMEZONE_TICKET_FIELD]
    resolved_timezone, issued_at = _tickets[ticket]
    _tickets[ticket] = (resolved_timezone, issued_at - SESSION_TTL - 1)
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


# Paris, far enough from the baseline form's Kathmandu that a zone worked out for
# one cannot belong to the other.
_PARIS = {"latitude": "48.86", "longitude": "2.35"}


def test_a_moved_birth_place_is_refused():
    for_kathmandu = _good_form()[TIMEZONE_TICKET_FIELD]
    form = _good_form(**_PARIS, **{TIMEZONE_TICKET_FIELD: for_kathmandu})
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


def test_a_moved_birth_date_is_refused():
    for_the_baseline_date = _good_form()[TIMEZONE_TICKET_FIELD]
    form = _good_form(
        birth_date="1990-07-23", **{TIMEZONE_TICKET_FIELD: for_the_baseline_date}
    )
    with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
        build_request(form)


def test_the_birth_time_may_move_without_resolving_again():
    """`kept` is the ticket for a zone worked out for birth_time 08:15. `form`
    submits birth_time 09:40 with that same ticket. assert_binds compares
    only the date and the place, not the time, so build_request does not
    raise. The assert then checks that birth_datetime uses the new time."""
    kept = _good_form()[TIMEZONE_TICKET_FIELD]
    form = _good_form(birth_time="09:40", **{TIMEZONE_TICKET_FIELD: kept})
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
    must still be in `timezone`. An offset entered by hand must still be in
    `utc_offset`. A zone that came from the birth place must leave both empty.
    """

    def _keeping_the_resolved_value(self, resolved_with, **posted):
        """Resolve a zone one way, then submit its ticket with something else
        posted."""
        ticket = _good_form(**resolved_with)[TIMEZONE_TICKET_FIELD]
        return _good_form(**posted, **{TIMEZONE_TICKET_FIELD: ticket})

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
            **{TIMEZONE_TICKET_FIELD: resolved[TIMEZONE_TICKET_FIELD]},
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_dropping_the_answer_is_refused(self):
        resolved = _good_form(**self.REPEATED, on_summer_time="true")
        form = _good_form(
            **self.REPEATED,
            on_summer_time="",
            **{TIMEZONE_TICKET_FIELD: resolved[TIMEZONE_TICKET_FIELD]},
        )
        with pytest.raises(ValueError, match=re.escape(RESOLVE_AGAIN_MESSAGE)):
            build_request(form)

    def test_adding_an_answer_is_refused(self):
        resolved = _good_form(**self.REPEATED, on_summer_time="")
        form = _good_form(
            **self.REPEATED,
            on_summer_time="true",
            **{TIMEZONE_TICKET_FIELD: resolved[TIMEZONE_TICKET_FIELD]},
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
    form = _good_form(**overrides, **{TIMEZONE_TICKET_FIELD: _base_ticket()})
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        build_request(form)
