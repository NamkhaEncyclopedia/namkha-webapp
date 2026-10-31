"""Layout and touch behavior on a phone screen.

These tests run only with a --device flag, such as "iPhone 13" or "Pixel 5".
The desktop runs skip the whole module.
"""

import pytest
from playwright.sync_api import expect

from tests.browser.helpers import fill_a_calculable_birth, stub_calculate

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _phones_only(device):
    if device is None:
        pytest.skip("phone configurations only")


class TestHeaderMenu:
    """Below 640 px the header links fold into a menu behind the toggle button."""

    def test_the_menu_starts_closed(self, page, live_server):
        page.goto(live_server)
        expect(page.locator("#primary-nav")).to_be_hidden()

    def test_a_tap_on_the_toggle_opens_the_menu(self, page, live_server):
        page.goto(live_server)
        page.locator(".nav-toggle").tap()
        expect(page.locator("#primary-nav")).to_be_visible()

    def test_the_toggle_reports_the_open_menu(self, page, live_server):
        page.goto(live_server)
        page.locator(".nav-toggle").tap()
        expect(page.locator(".nav-toggle")).to_have_attribute("aria-expanded", "true")

    def test_a_second_tap_on_the_toggle_closes_the_menu(self, page, live_server):
        page.goto(live_server)
        toggle = page.locator(".nav-toggle")
        toggle.tap()
        expect(page.locator("#primary-nav")).to_be_visible()
        toggle.tap()
        expect(page.locator("#primary-nav")).to_be_hidden()

    def test_a_tap_on_a_link_closes_the_menu(self, page, live_server):
        page.goto(live_server)
        page.locator(".nav-toggle").tap()
        page.locator("#primary-nav").get_by_role("link", name="About").tap()
        expect(page.locator("#primary-nav")).to_be_hidden()


def help_text_position(page):
    return page.locator(".help-text").first.evaluate(
        "el => getComputedStyle(el).position"
    )


class TestBreakpoints:
    """style.css changes the layout at max-width 640 px and at max-width 480 px.

    Each test sets its own width on one side of a breakpoint. The device keeps
    its user agent, touch input and pixel ratio.
    """

    def test_the_menu_toggle_shows_at_640_px(self, page, live_server):
        page.set_viewport_size({"width": 640, "height": 800})
        page.goto(live_server)
        expect(page.locator(".nav-toggle")).to_be_visible()

    def test_the_menu_toggle_hides_at_641_px(self, page, live_server):
        page.set_viewport_size({"width": 641, "height": 800})
        page.goto(live_server)
        expect(page.locator(".nav-toggle")).to_be_hidden()

    def test_the_header_links_show_at_641_px(self, page, live_server):
        page.set_viewport_size({"width": 641, "height": 800})
        page.goto(live_server)
        expect(page.locator("#primary-nav")).to_be_visible()

    def test_help_text_is_a_fixed_banner_at_480_px(self, page, live_server):
        page.set_viewport_size({"width": 480, "height": 800})
        page.goto(live_server)
        assert help_text_position(page) == "fixed"

    def test_help_text_sits_by_its_button_at_481_px(self, page, live_server):
        page.set_viewport_size({"width": 481, "height": 800})
        page.goto(live_server)
        assert help_text_position(page) != "fixed"


# The download markup app/templates/_result.html renders. The sheet is left out,
# since only the button is measured.
RESULT_BODY = (
    '<div class="download">'
    '<form method="post" action="/download.pdf">'
    '<input type="hidden" name="result_id" value="stub">'
    '<input type="hidden" name="session_token" value="stub">'
    '<button type="submit" class="download-link">'
    '<img src="/static/img/download.svg" alt="">'
    "Download PDF"
    "</button>"
    "</form>"
    "</div>"
)

# A finger needs at least this much, per Apple's and WCAG's touch target guidance.
MIN_TAP_TARGET_PX = 44


def download_button(page, live_server):
    """Calculate once against a stubbed /calculate, and return the Download button."""
    stub_calculate(page, [], body=RESULT_BODY)
    page.goto(live_server)
    fill_a_calculable_birth(page)
    page.locator("button[type='submit']").click()
    button = page.locator(".download-link")
    expect(button).to_be_visible()
    return button


class TestTapTargets:
    def test_the_calculate_button_is_tall_enough_to_tap(self, page, live_server):
        page.goto(live_server)
        box = page.locator("button[type='submit']").bounding_box()
        assert box["height"] >= MIN_TAP_TARGET_PX

    def test_the_calculate_button_is_wide_enough_to_tap(self, page, live_server):
        page.goto(live_server)
        box = page.locator("button[type='submit']").bounding_box()
        assert box["width"] >= MIN_TAP_TARGET_PX

    def test_the_download_button_is_tall_enough_to_tap(self, page, live_server):
        box = download_button(page, live_server).bounding_box()
        assert box["height"] >= MIN_TAP_TARGET_PX

    def test_the_download_button_is_wide_enough_to_tap(self, page, live_server):
        box = download_button(page, live_server).bounding_box()
        assert box["width"] >= MIN_TAP_TARGET_PX


# iOS zooms the page into a field with smaller text when it gets focus.
MIN_FIELD_FONT_PX = 16


class TestFieldFontSize:
    """Playwright WebKit does not zoom on focus, so these tests check the font size."""

    def test_every_visible_field_is_large_enough_to_stop_the_zoom(
        self, page, live_server
    ):
        page.goto(live_server)
        sizes = page.locator(".input-text:visible").evaluate_all(
            "fields => fields.map(f => parseFloat(getComputedStyle(f).fontSize))"
        )
        assert sizes
        assert min(sizes) >= MIN_FIELD_FONT_PX


class TestBirthDateRow:
    """Day, month and year share one row, even on the narrowest phone."""

    def test_the_three_parts_sit_on_one_row(self, page, live_server):
        page.goto(live_server)
        tops = [
            page.locator(selector).bounding_box()["y"]
            for selector in ("#birth-day", "#birth-month", "#birth-year")
        ]
        assert len(set(tops)) == 1

    def test_the_page_does_not_scroll_sideways_at_320_px(self, page, live_server):
        page.set_viewport_size({"width": 320, "height": 800})
        page.goto(live_server)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth"
            " <= document.documentElement.clientWidth"
        )

    @pytest.mark.parametrize("field", ["#birth-day", "#birth-year"])
    def test_day_and_year_ask_for_the_number_keypad(self, page, live_server, field):
        page.goto(live_server)
        expect(page.locator(field)).to_have_attribute("inputmode", "numeric")


class TestPlainTimeField:
    """The birth time field as plain text, which in-app browsers get.

    The time_input query parameter forces that form on any browser.
    """

    def test_the_plain_time_field_asks_for_the_number_keypad(self, page, live_server):
        page.goto(f"{live_server}?time_input=text")
        expect(page.locator("#birth_time")).to_have_attribute("inputmode", "numeric")
