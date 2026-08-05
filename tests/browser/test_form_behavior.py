"""Alpine.js form behavior that TestClient can't reach. Assertions track the current
frozen state of the UI (read from index.html), not the eventual method-based gating.

Forward note: when MONTH/DAY/HOUR are unfrozen (TODO index.html:107), gating becomes
method-based (CLASSIC -> all four; CNNR forces YEAR via the $watch at index.html:68)
and the birth-time precision warning becomes reachable. Flip these assertions then.
"""

import json
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_type_gating_frozen_to_year(page, live_server):
    page.goto(live_server)
    year = page.locator("#type-YEAR")
    expect(year).to_be_enabled()
    expect(year).to_be_checked()
    for value in ("MONTH", "DAY", "HOUR"):
        expect(page.locator(f"#type-{value}")).to_be_disabled()
    expect(page.get_by_text("Coming soon").first).to_be_visible()


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
    # selectPlace fills lat/lon (4 dp); fetchTimezone settles the zone.
    expect(page.locator("input[name='latitude']")).to_have_value("52.5200")
    expect(page.locator("input[name='longitude']")).to_have_value("13.4050")
    expect(page.locator(".timezone-detected")).to_have_text("Detected: Europe/Berlin")
    # The settled zone rides along in its own field, ready to submit back.
    expect(page.locator("input[name='resolved_timezone']")).to_have_value(
        re.compile(r"^v1\|LOCATION_DERIVED\|Europe/Berlin\|")
    )
    # Automatic mode submits no chosen zone; the resolved value carries the answer.
    expect(page.locator("input[name='timezone']")).to_have_value("")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")
    # The selected label submits as the location name.
    expect(page.locator("input[name='location_name']")).to_have_value("Berlin, Germany")


def test_editing_a_birth_detail_drops_the_settled_timezone(page, live_server):
    """A settled zone belongs to the details it was worked out for. Editing one
    must clear it, so a stale zone can never reach the calculation."""
    page.goto(live_server)
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    resolved = page.locator("input[name='resolved_timezone']")
    expect(resolved).to_have_value(re.compile(r"\|1985-06-15\|"))
    # A different birth date can mean a different zone. What lands must be the
    # answer for the new date, never the one worked out for the old one.
    page.fill("#birth_date", "1940-06-15")
    expect(resolved).to_have_value(re.compile(r"\|1940-06-15\|"))
    # An edit that leaves nothing to look up clears the field and stops there,
    # so an incomplete form has no zone to submit.
    page.fill("#birth_time", "")
    expect(resolved).to_have_value("")


def test_timezone_modes_feed_hidden_inputs(page, live_server):
    page.goto(live_server)
    # Automatic is the default: only the mode select and status line render,
    # and neither hidden field submits a value.
    expect(page.locator(".timezone-detected")).to_be_visible()
    expect(page.locator("#timezone-search")).to_have_count(0)
    expect(page.locator("input[name='timezone']")).to_have_value("")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")

    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "10.5")
    page.fill("#longitude-ui", "20.5")
    # Disabled UI inputs aren't serialized; the hidden :value mirrors are what posts.
    expect(page.locator("input[name='latitude']")).to_have_value("10.5")
    expect(page.locator("input[name='longitude']")).to_have_value("20.5")

    # List mode: fuzzy search over the library's zone list, selection commits.
    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")
    expect(search).to_be_visible()
    search.fill("kath")
    suggestion = page.locator(".timezone-suggestion").first
    expect(suggestion).to_contain_text("Asia/Kathmandu")
    suggestion.click()
    expect(search).to_have_value("Asia/Kathmandu")
    expect(page.locator("input[name='timezone']")).to_have_value("Asia/Kathmandu")
    expect(page.locator("input[name='utc_offset']")).to_have_value("")

    # Offset mode: sign + time-ish entry submit combined; zone field goes empty.
    page.select_option("#timezone-mode", "offset")
    expect(page.locator("#timezone-search")).to_have_count(0)
    page.fill("#utc-offset-time", "5:45")
    expect(page.locator("input[name='utc_offset']")).to_have_value("+5:45")
    expect(page.locator("input[name='timezone']")).to_have_value("")
    # DST is meaningless for a fixed offset: the control folds away.
    expect(page.locator("#on_summer_time")).to_be_hidden()
    # An out-of-range entry is flagged invalid, never silently dropped.
    page.fill("#utc-offset-time", "16:30")
    assert not page.locator("#utc-offset-time").evaluate("el => el.checkValidity()")


def test_timezone_search_without_selection_blocks_submit(page, live_server):
    # Typing without committing a zone must not silently fall back to automatic:
    # the combobox carries a custom validity error until a zone is chosen.
    page.goto(live_server)
    page.select_option("#timezone-mode", "list")
    search = page.locator("#timezone-search")
    search.fill("nowhere")
    assert not search.evaluate("el => el.checkValidity()")
    # An exact key typed by hand commits on blur and clears the error.
    search.fill("Asia/Kathmandu")
    search.blur()
    expect(page.locator("input[name='timezone']")).to_have_value("Asia/Kathmandu")
    assert search.evaluate("el => el.checkValidity()")


def test_manual_coords_make_place_a_plain_text_field(page, live_server):
    # With manual coordinates on, the place field is plain text: no autocomplete
    # search fires, and whatever is typed submits as the location name verbatim.
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
      resolved_timezone: 'reply-' + index,
      timezone: 'Europe/Berlin',
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
    arrive after that of a modern date typed later. The older answer must be
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
    page.fill("#birth_date", "1985-06-15")  # request 1, asked for later
    page.wait_for_function("() => window.__heldTimezoneReplies.length === 2")

    # The newer request answers first, the older one straggles in behind it.
    page.evaluate(_RELEASE_REPLIES, [1, 0])
    resolved = page.locator("input[name='resolved_timezone']")
    expect(resolved).to_have_value("reply-1")
