"""Alpine.js form behavior that TestClient can't reach.

Namkha type gating is half method-based and half frozen. Classic offers Year and
Month, CNNR offers Year alone, and Day and Hour stay switched off under a "Coming
soon" cover until the library calculates them. The assertions below track that
state, so unfreezing a type means changing them.
"""

import json
from datetime import date

import namkha_calculator as nc
import pytest
from playwright.sync_api import expect

from app import constants
from app.timezone_tickets import _tickets, read_ticket

pytestmark = pytest.mark.browser


def resolved_zone(ticket_field):
    """The zone the form's ticket stands for.

    live_server runs uvicorn in this same process, so the ticket the browser holds
    can be looked up here. That is what lets these tests assert the zone the whole
    chain arrived at, not just that some ticket landed.
    """
    expect(ticket_field).not_to_have_value("")  # wait for the reply
    zone = read_ticket(ticket_field.input_value())
    assert zone is not None, "the server does not know the ticket the form carries"
    return zone


# /calculate needs a Turnstile check against Cloudflare, which this offline suite
# cannot make, so the two tests below answer it themselves and read what the form
# sent. `trigger` is the header the real route sends to ask for a new time zone;
# tests/test_routes.py checks that it sends it.
STUB_ANSWER = "answered by the test"


def stub_calculate(page, posted, trigger=None):
    def answer(route):
        posted.append(route.request.post_data)
        route.fulfill(
            status=200,
            content_type="text/html",
            headers={"HX-Trigger": trigger} if trigger else {},
            body=f'<p class="error">{STUB_ANSWER}</p>',
        )

    page.route("**/calculate", answer)


def submitted_ticket(body):
    for pair in (body or "").split("&"):
        key, _, value = pair.partition("=")
        if key == "timezone_ticket":
            return value
    return ""


def test_pressing_calculate_straight_after_an_edit_sends_a_zone(page, live_server):
    """Pressing Calculate is what ends the edit in a coordinate field, so the
    lookup and the submit start together. The form has to hold the submit until
    the answer lands, or it posts no zone and the server refuses a form that is
    in fact complete."""
    posted = []
    stub_calculate(page, posted)
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    ticket = page.locator("input[name='timezone_ticket']")
    resolved_zone(ticket)

    # Re-enter a coordinate and press Calculate without leaving the field first.
    page.fill("#longitude-ui", "13.4070")
    page.locator("button[type='submit']").click()
    expect(page.locator("#result")).to_contain_text(STUB_ANSWER)
    assert len(posted) == 1
    assert read_ticket(submitted_ticket(posted[0])) is not None


def test_a_forgotten_zone_is_asked_for_again(page, live_server):
    """What a restart or a full ticket store does to an open page: the server no
    longer holds the zone the form points at. Without the ask-again header every
    further press of Calculate would send the same dead ticket, and the refusal
    telling the user to submit again would never happen."""
    posted = []
    stub_calculate(page, posted, trigger="namkha-resolve-timezone-again")
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    ticket = page.locator("input[name='timezone_ticket']")
    resolved_zone(ticket)

    forgotten = ticket.input_value()
    _tickets.clear()  # the server forgets, while the page keeps its ticket
    page.locator("button[type='submit']").click()
    expect(page.locator("#result")).to_contain_text(STUB_ANSWER)
    assert read_ticket(submitted_ticket(posted[-1])) is None  # the dead one went out

    # The refusal carried the header, so the form asked again. Wait for the dead
    # ticket to go before reading, or the old value is what gets read.
    expect(ticket).not_to_have_value(forgotten)
    resolved_zone(ticket)
    page.locator("button[type='submit']").click()
    expect(page.locator("#result")).to_contain_text(STUB_ANSWER)
    assert read_ticket(submitted_ticket(posted[-1])) is not None


def choose_type(page, value):
    """Pick a Namkha type by clicking its label.

    The label covers the radio button, so a click aimed at the button never
    reaches it. A visitor clicks the label, and the browser checks the button
    behind it.
    """
    page.locator(f"label[for='type-{value}']").click()


def test_month_is_selectable_with_the_classic_method(page, live_server):
    page.goto(live_server)
    year = page.locator("#type-YEAR")
    expect(year).to_be_enabled()
    expect(year).to_be_checked()  # the type the form opens on

    page.select_option("#method", "CLASSIC")
    month = page.locator("#type-MONTH")
    expect(month).to_be_enabled()
    choose_type(page, "MONTH")
    expect(month).to_be_checked()


def test_month_is_switched_off_under_cnnr(page, live_server):
    """CNNR calculates the year alone. Choosing it puts the type back to Year and
    switches Month off, so the pair the server refuses cannot be built."""
    page.goto(live_server)
    page.select_option("#method", "CLASSIC")
    choose_type(page, "MONTH")

    page.select_option("#method", "CNNR")
    expect(page.locator("#type-YEAR")).to_be_checked()
    expect(page.locator("#type-MONTH")).to_be_disabled()


def test_month_says_why_it_is_switched_off(page, live_server):
    """A cover over the button carries the reason. Day and Hour carry the same
    kind of cover, reading "Coming soon"."""
    page.goto(live_server)
    cover = page.get_by_text("Classic only")
    expect(cover).to_be_visible()

    page.select_option("#method", "CLASSIC")
    expect(cover).to_be_hidden()


def test_day_and_hour_are_still_frozen(page, live_server):
    page.goto(live_server)
    page.select_option("#method", "CLASSIC")
    for value in ("DAY", "HOUR"):
        expect(page.locator(f"#type-{value}")).to_be_disabled()
    expect(page.get_by_text("Coming soon").first).to_be_visible()


def test_choosing_month_shows_the_birth_time_precision_warning(page, live_server):
    """Year is the only type that a rough birth time is enough for, so the warning
    could not be reached while Month was frozen."""
    warning = page.locator(".inline-tooltip", has_text="precision of at least")

    page.goto(live_server)
    expect(warning).to_be_hidden()
    page.select_option("#method", "CLASSIC")
    choose_type(page, "MONTH")
    expect(warning).to_be_visible()


def test_place_autocomplete_fills_coords_and_timezone(page, live_server):
    # Stub Komoot Photon so the suggestion + selection are deterministic.
    page.route(
        "**/photon.komoot.io/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "features": [
                        {
                            "properties": {"name": "Berlin", "country": "Germany"},
                            "geometry": {"coordinates": [13.405, 52.52]},
                        }
                    ]
                }
            ),
        ),
    )
    page.goto(live_server)
    # The birth date and time come first: which zone applied depends on them, so
    # the lookup declines until both are set.
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    page.fill("#place", "Berlin")
    suggestion = page.locator(".place-suggestion").first
    expect(suggestion).to_be_visible()
    suggestion.click()
    # selectPlace fills lat/lon (4 dp); fetchTimezone resolves the zone.
    expect(page.locator("input[name='latitude']")).to_have_value("52.5200")
    expect(page.locator("input[name='longitude']")).to_have_value("13.4050")
    expect(page.locator(".timezone-detected")).to_have_text("Detected: Europe/Berlin")
    # A ticket for the resolved zone rides along in its own field, ready to
    # submit back.
    zone = resolved_zone(page.locator("input[name='timezone_ticket']"))
    assert zone.key == "Europe/Berlin"
    assert zone.provenance is nc.TimezoneProvenance.LOCATION_DERIVED
    # Automatic mode submits no chosen zone; the ticket carries the answer.
    expect(page.locator("input[name='timezone']")).to_have_value("")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")
    # The selected label submits as the location name.
    expect(page.locator("input[name='location_name']")).to_have_value("Berlin, Germany")
    # Picking a suggestion must not leave the "No places found." status up.
    expect(page.locator(".place-status", has_text="No places found.")).to_be_hidden()


def test_editing_a_birth_detail_replaces_the_ticket(page, live_server):
    """A resolved zone belongs to the details it was worked out for. Editing one
    must drop its ticket, so a stale zone can never reach the calculation."""
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    ticket = page.locator("input[name='timezone_ticket']")
    assert resolved_zone(ticket).for_birth_date == date(1985, 6, 15)
    for_1985 = ticket.input_value()
    # A different birth date can mean a different zone. What lands must be a
    # ticket for the new date, never the one worked out for the old one.
    page.fill("#birth_date", "1940-06-15")
    # Wait for the old ticket to go before reading: it is still in the field for a
    # moment, and reading then would look up the zone for the old date.
    expect(ticket).not_to_have_value(for_1985)
    assert resolved_zone(ticket).for_birth_date == date(1940, 6, 15)
    # An edit that leaves nothing to look up clears the field and stops there,
    # so an incomplete form has no zone to submit.
    page.fill("#birth_time", "")
    expect(ticket).to_have_value("")


def test_timezone_modes_feed_hidden_inputs(page, live_server):
    """Every mode has to end with a ticket in timezone_ticket, whatever the user
    picked. That field is the only zone the calculation accepts, so a mode that
    fills the visible controls but leaves it empty submits nothing usable.

    Each mode is checked by the zone its ticket stands for, so a mode that fills
    the wrong hidden field is caught rather than passing on a non-empty ticket.

    Berlin coordinates throughout: the library checks a chosen zone or offset
    against the birth place, so a zone from somewhere else is refused.
    """
    page.goto(live_server)
    # Automatic is the default: only the mode select and status line render,
    # and neither hidden field submits a value.
    expect(page.locator(".timezone-detected")).to_be_visible()
    expect(page.locator("#timezone-search")).to_have_count(0)
    expect(page.locator("input[name='timezone']")).to_have_value("")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")

    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    # Disabled UI inputs aren't serialized; the hidden :value mirrors are what
    # posts. Entered coordinates are rounded to four decimals on leaving the
    # field, the same as the ones a place from the list brings.
    expect(page.locator("input[name='latitude']")).to_have_value("52.5200")
    expect(page.locator("input[name='longitude']")).to_have_value("13.4050")
    ticket = page.locator("input[name='timezone_ticket']")
    assert resolved_zone(ticket).provenance is nc.TimezoneProvenance.LOCATION_DERIVED

    # List mode: fuzzy search narrows the library's zone list, selection commits.
    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")
    expect(search).to_be_visible()
    search.fill("berl")
    suggestion = page.locator(".timezone-suggestion").first
    expect(suggestion).to_contain_text("Europe/Berlin")
    suggestion.click()
    # The search field filters the list, it does not hold the choice: picking a
    # zone empties it and marks that zone in the full list.
    expect(search).to_have_value("")
    expect(page.locator(".timezone-suggestion.is-selected")).to_contain_text(
        "Europe/Berlin"
    )
    expect(page.locator("input[name='timezone']")).to_have_value("Europe/Berlin")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")
    zone = resolved_zone(ticket)
    assert zone.key == "Europe/Berlin"
    assert zone.provenance is nc.TimezoneProvenance.USER_ZONE

    # Offset mode: sign + time-ish entry submit combined; zone field goes empty.
    page.select_option("#timezone-mode", "offset")
    expect(page.locator("#timezone-search")).to_have_count(0)
    page.fill("#utc-offset-time", "1:00")
    expect(page.locator("input[name='utc_offset']")).to_have_value("+1:00")
    expect(page.locator("input[name='timezone']")).to_have_value("")
    # The offset input resolves on change, so the lookup waits for the user to
    # leave the field rather than firing on every keystroke of "1:00".
    page.locator("#utc-offset-time").blur()
    zone = resolved_zone(ticket)
    assert zone.offset_seconds == 3600
    assert zone.provenance is nc.TimezoneProvenance.USER_OFFSET
    # DST is meaningless for a fixed offset: the control folds away.
    expect(page.locator("#on_summer_time")).to_be_hidden()
    # An out-of-range entry is flagged invalid, never silently dropped.
    page.fill("#utc-offset-time", "16:30")
    assert not page.locator("#utc-offset-time").evaluate("el => el.checkValidity()")


def test_status_line_shows_the_name_the_sheet_will_use(page, live_server):
    """Berlin in 1890 predates standard time there, so the calculation runs on
    sun-based local time while the resolved zone key stays Europe/Berlin. The
    status line has to say what the sheet will say, not the key behind it."""
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_time", "12:00")

    page.fill("#birth_date", "1985-06-15")
    expect(page.locator(".timezone-detected")).to_have_text("Detected: Europe/Berlin")

    page.fill("#birth_date", "1890-06-15")
    expect(page.locator(".timezone-detected")).to_have_text("Detected: mean solar time")
    # The key underneath is unchanged; only what the reader is shown differs.
    ticket = page.locator("input[name='timezone_ticket']")
    assert resolved_zone(ticket).key == "Europe/Berlin"


def test_timezone_search_without_selection_blocks_submit(page, live_server):
    # A search without committing a zone must not silently fall back to automatic:
    # the combobox carries a custom validity error until a zone is chosen.
    page.goto(live_server)
    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")
    search.fill("nowhere")
    assert not search.evaluate("el => el.checkValidity()")
    # An exact key entered by hand commits on blur and clears the error.
    search.fill("Asia/Kathmandu")
    search.blur()
    expect(page.locator("input[name='timezone']")).to_have_value("Asia/Kathmandu")
    assert search.evaluate("el => el.checkValidity()")


def test_timezone_list_is_ready_before_any_search(page, live_server):
    """The mode is called "Choose from list", so a list has to be on screen the
    moment it is chosen. It starts on the zone already detected, with every other
    zone under it to scroll through, and the search field only narrows it."""
    zone_count = len(constants.TIMEZONES)
    region_count = len({key.split("/")[0] for key, _ in constants.TIMEZONES})

    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    expect(page.locator(".timezone-detected")).to_have_text("Detected: Europe/Berlin")

    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")
    # The search field is empty, and every zone is already listed under its
    # region heading.
    expect(search).to_have_value("")
    expect(page.locator(".timezone-suggestion")).to_have_count(zone_count)
    expect(page.locator(".timezone-group")).to_have_count(region_count)
    expect(page.locator(".timezone-group").first).to_have_text("Africa")
    # The detected zone is the marked one, so it can be confirmed or moved to a
    # neighbor rather than searched for again.
    expect(page.locator(".timezone-suggestion.is-selected")).to_contain_text(
        "Europe/Berlin"
    )
    expect(page.locator("input[name='timezone']")).to_have_value("Europe/Berlin")

    # Escape clears the search, and not the choice. Picking a zone leaves the focus
    # in the search field, so Escape is easy to press right after.
    search.press("Escape")
    expect(page.locator(".timezone-suggestion.is-selected")).to_contain_text(
        "Europe/Berlin"
    )
    expect(page.locator("input[name='timezone']")).to_have_value("Europe/Berlin")
    ticket = page.locator("input[name='timezone_ticket']")
    assert resolved_zone(ticket).key == "Europe/Berlin"

    # A search narrows that same list, and the headings go: the zones are then
    # ordered by how well they match, which cuts across the regions.
    search.fill("kathm")
    expect(page.locator(".timezone-suggestion").first).to_contain_text("Asia/Kathmandu")
    expect(page.locator(".timezone-group")).to_have_count(0)
    # Emptying the search brings the whole list back.
    search.fill("")
    expect(page.locator(".timezone-suggestion")).to_have_count(zone_count)
    expect(page.locator(".timezone-group")).to_have_count(region_count)

    # Walking up with the arrow keys scrolls the list, and a region's first zone
    # has to stop below the heading stuck to the top rather than under it.
    for _ in range(12):
        search.press("ArrowUp")
    clearance = page.evaluate("""() => {
        const list = document.getElementById('timezone-listbox');
        const active = list.querySelector('.timezone-suggestion.is-active');
        const heading = list.querySelector('.timezone-group');
        return active.getBoundingClientRect().top
             - list.getBoundingClientRect().top
             - heading.offsetHeight;
    }""")
    assert clearance >= -1, f"the active zone sits {-clearance}px under the heading"


def test_timezone_labels_follow_the_birth_date(page, live_server):
    """Kathmandu moved from UTC+5:30 to UTC+5:45 in 1986, so a label has to carry
    the offset of the birth date rather than today's. tzdata writes a space in a
    zone name as an underscore, which belongs in the key, not on screen."""
    page.goto(live_server)
    page.fill("#birth_date", "1940-06-15")
    page.fill("#birth_time", "12:00")
    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")

    search.fill("kathmandu")
    expect(page.locator(".timezone-suggestion").first).to_have_text(
        "Asia/Kathmandu (UTC+5:30)"
    )
    # Moving the birth date past the change relabels the same zone.
    page.fill("#birth_date", "2000-06-15")
    expect(page.locator(".timezone-suggestion").first).to_have_text(
        "Asia/Kathmandu (UTC+5:45)"
    )

    # The name reads with a space; the key that submits keeps the underscore.
    search.fill("south georgia")
    option = page.locator(".timezone-suggestion").first
    expect(option).to_contain_text("Atlantic/South Georgia")
    option.click()
    expect(page.locator("input[name='timezone']")).to_have_value(
        "Atlantic/South_Georgia"
    )


def test_the_list_keeps_its_place_when_offsets_arrive(page, live_server):
    """The offsets reach the list after it is drawn, and they make every label
    longer. On a narrow screen that wraps rows and grows the list by over a
    thousand pixels, so the list has to re-find its place by row. Holding a count
    of pixels instead would leave the chosen zone far below the view."""
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1940-06-15")
    page.fill("#birth_time", "12:00")
    expect(page.locator(".timezone-detected")).to_contain_text("Europe/")

    page.select_option("#timezone-mode", "list")
    selected = page.locator(".timezone-suggestion.is-selected")
    expect(selected).to_contain_text("Europe/Berlin")
    expect(selected).to_contain_text("UTC")  # the offsets have landed
    placement = page.evaluate("""() => {
        const list = document.getElementById('timezone-listbox');
        const selected = list.querySelector('.timezone-suggestion.is-selected');
        return {
            top: selected.getBoundingClientRect().top
               - list.getBoundingClientRect().top,
            height: list.clientHeight,
        };
    }""")
    assert 0 <= placement["top"] <= placement["height"] - 10, (
        f"the chosen zone sits {placement['top']}px down a {placement['height']}px list"
    )


def test_manual_coords_make_place_a_plain_text_field(page, live_server):
    # With manual coordinates on, the place field is plain text: no autocomplete
    # search fires, and whatever is entered submits as the location name verbatim.
    # Photon is stubbed to return a hit, so a regressed guard would surface a
    # suggestion (and fail the count assertion) instead of silently passing.
    page.route(
        "**/photon.komoot.io/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "features": [
                        {
                            "properties": {"name": "Should Not Appear"},
                            "geometry": {"coordinates": [0, 0]},
                        }
                    ]
                }
            ),
        ),
    )
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#place", "Remote Ranch, Patagonia")
    expect(page.locator(".place-suggestion")).to_have_count(0)
    # Plain text field: combobox semantics are dropped.
    expect(page.locator("#place")).not_to_have_attribute("role", "combobox")
    expect(page.locator("input[name='location_name']")).to_have_value(
        "Remote Ranch, Patagonia"
    )


# Holds every /timezone reply open so the test decides what lands and in what
# order. Installed before the page scripts run, so the form's own fetch is the
# one replaced. Anything else (place autocomplete) passes straight through.
_HOLD_TIMEZONE_REPLIES = """
window.__heldTimezoneReplies = [];
const realFetch = window.fetch;
window.fetch = function (resource, ...rest) {
  if (typeof resource === 'string' && resource.startsWith('/timezone?')) {
    return new Promise(resolve => {
      window.__heldTimezoneReplies.push({ url: resource, resolve });
    });
  }
  return realFetch.apply(this, [resource, ...rest]);
};
"""

# Answers the held requests in the given order, oldest request first in the list.
_RELEASE_REPLIES = """
(order) => {
  const held = window.__heldTimezoneReplies;
  for (const index of order) {
    const body = {
      timezone_ticket: 'reply-' + index,
      timezone: 'Europe/Berlin',
      label: 'Europe/Berlin',
      derivation: 'CERTAIN',
      notes: [],
    };
    held[index].resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
  }
}
"""


def test_a_late_reply_cannot_overwrite_a_newer_one(page, live_server):
    """A pre-1970 date searches the historical border maps, so its reply can
    arrive after that of a modern date entered later. The older answer must be
    dropped, not written over the newer one."""
    page.add_init_script(_HOLD_TIMEZONE_REPLIES)
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_time", "12:00")

    # Only the two edits below may be in flight when the replies are released.
    page.evaluate("() => { window.__heldTimezoneReplies = []; }")
    page.fill("#birth_date", "1940-06-15")  # request 0, the slow one
    # The form waits before asking, so let this request go out before the next
    # edit. Back to back the two would collapse into one, and there would be no
    # late reply to test.
    page.wait_for_function("() => window.__heldTimezoneReplies.length === 1")
    page.fill("#birth_date", "1985-06-15")  # request 1, asked for later
    page.wait_for_function("() => window.__heldTimezoneReplies.length === 2")

    # The newer request answers first, the older one straggles in behind it.
    page.evaluate(_RELEASE_REPLIES, [1, 0])
    ticket = page.locator("input[name='timezone_ticket']")
    expect(ticket).to_have_value("reply-1")


def test_edits_in_a_row_ask_once(page, live_server):
    """The form waits before asking, so a run of edits sends one request. Every
    edit on its own would be one request each, and the endpoint is rate
    limited."""
    page.add_init_script(_HOLD_TIMEZONE_REPLIES)
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_time", "12:00")

    page.evaluate("() => { window.__heldTimezoneReplies = []; }")
    # Three edits, close enough together that only the last one is asked about.
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_date", "1986-06-15")
    page.fill("#birth_date", "1987-06-15")
    page.wait_for_function("() => window.__heldTimezoneReplies.length === 1")
    # Long enough for the two earlier waits to have ended, had they survived.
    page.wait_for_timeout(600)
    assert page.evaluate("() => window.__heldTimezoneReplies.length") == 1
    held = page.evaluate("() => window.__heldTimezoneReplies[0].url")
    assert "birth_date=1987-06-15" in held


def test_the_birth_time_field_keeps_the_picker_by_default(page, live_server):
    """A time input opens the phone's picker, which is the easier way to enter a
    time. Only the browsers that crash on it get the plain field."""
    page.goto(live_server)
    expect(page.locator("#birth_time")).to_have_attribute("type", "time")


def test_time_input_text_turns_the_birth_time_field_into_plain_text(page, live_server):
    """Instagram's and Facebook's in-app browsers close down when the time picker
    opens. There the field is text, so tapping it opens the keyboard instead. The
    query parameter forces that form in any browser, so it can be tested here."""
    page.goto(f"{live_server}?time_input=text")
    expect(page.locator("#birth_time")).to_have_attribute("type", "text")


@pytest.mark.parametrize("entered", ["9:30", "0930", "930", "09:30"])
def test_the_plain_birth_time_field_reaches_hh_mm(page, live_server, entered):
    """The server reads the birth time as HH:MM. A time picker can only give that
    shape, a text field can give several. The numeric keypad on a phone has no
    colon key, so a bare "930" has to arrive as "09:30" too."""
    page.goto(f"{live_server}?time_input=text")
    # Wait for the swap: a fill of "9:30" into the time input it starts as fails.
    expect(page.locator("#birth_time")).to_have_attribute("type", "text")
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", entered)
    page.locator("#birth_time").blur()  # fill() alone fires no change event

    expect(page.locator("#birth_time")).to_have_value("09:30")


def test_the_plain_birth_time_field_resolves_a_zone(page, live_server):
    """A birth time entered as plain text ends in a ticket like any other.

    Ensures proper processing order of time text field data.
    """
    page.goto(f"{live_server}?time_input=text")
    expect(page.locator("#birth_time")).to_have_attribute("type", "text")
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "930")
    page.locator("#birth_time").blur()

    ticket = page.locator("input[name='timezone_ticket']")
    assert resolved_zone(ticket).key == "Europe/Berlin"
