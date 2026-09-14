"""Helpers shared by the browser test modules."""

from playwright.sync_api import expect

from app.timezone_tickets import read_ticket


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
# cannot make, so tests that submit answer it themselves and read what the form
# sent. `trigger` is the header the real route sends to ask for a new time zone;
# tests/test_routes.py checks that it sends it.
STUB_ANSWER = "answered by the test"


def error_body(message):
    """The markup the server sends for a failed calculation, as
    app/templates/_error.html renders it. The warning icon and the placeholder
    artwork are left out, since no test reads them.
    """
    return (
        '<div class="diagrams"><div class="image-holder image-holder-error">'
        f'<p class="error" role="alert"><span class="error-text">{message}</span></p>'
        "</div></div>"
    )


def stub_calculate(page, posted, trigger=None, body=None):
    def answer(route):
        posted.append(route.request.post_data)
        route.fulfill(
            status=200,
            content_type="text/html",
            headers={"HX-Trigger": trigger} if trigger else {},
            body=body or error_body(STUB_ANSWER),
        )

    page.route("**/calculate", answer)


def fill_a_calculable_birth(page):
    """Enter enough for the form to submit: coordinates, a date and a time.
    Returns once the server has worked the time zone out.
    """
    page.get_by_text("Manually set coordinates").click()
    page.fill("#latitude-ui", "52.52")
    page.fill("#longitude-ui", "13.405")
    page.fill("#birth_date", "1985-06-15")
    page.fill("#birth_time", "12:00")
    resolved_zone(page.locator("input[name='timezone_ticket']"))
