"""Alpine.js form behavior that TestClient can't reach. Assertions track the current
frozen state of the UI (read from index.html), not the eventual method-based gating.

Forward note: when MONTH/DAY/HOUR are unfrozen (TODO index.html:107), gating becomes
method-based (CLASSIC -> all four; CNNR forces YEAR via the $watch at index.html:68)
and the birth-time precision warning becomes reachable. Flip these assertions then.
"""

import json

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
    page.fill("#place", "Berlin")
    suggestion = page.locator(".place-suggestion").first
    expect(suggestion).to_be_visible()
    suggestion.click()
    # selectPlace fills lat/lon (4 dp); fetchTimezone then auto-fills the zone.
    expect(page.locator("input[name='latitude']")).to_have_value("52.5200")
    expect(page.locator("input[name='longitude']")).to_have_value("13.4050")
    expect(page.locator("input[name='timezone']")).to_have_value("Europe/Berlin")
    # The selected label submits as the location name.
    expect(page.locator("input[name='location_name']")).to_have_value("Berlin, Germany")


def test_manual_toggles_feed_hidden_inputs(page, live_server):
    page.goto(live_server)
    # Toggle both manual modes first so coordinate @change doesn't fire /timezone.
    page.get_by_text("Manually set coordinates").click()
    page.get_by_text("Manually set time zone").click()
    page.fill("#latitude-ui", "10.5")
    page.fill("#longitude-ui", "20.5")
    page.select_option("#timezone-ui", "Asia/Kathmandu")
    # Disabled UI inputs aren't serialized; the hidden :value mirrors are what posts.
    expect(page.locator("input[name='latitude']")).to_have_value("10.5")
    expect(page.locator("input[name='longitude']")).to_have_value("20.5")
    expect(page.locator("input[name='timezone']")).to_have_value("Asia/Kathmandu")


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
