"""HTTP routes via Starlette's TestClient, plus the error-mapping and rate-limit
helpers they depend on."""

import logging

import namkha_calculator as nc
import pytest

from app import main, notes, turnstile


def test_index_ok(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Classic" in response.text  # a method label is rendered
    assert main.constants.APP_VERSION in response.text  # version badge


def test_favicon_redirects(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/static/img/favicon.ico"


def test_robots_txt(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /calculate" in response.text
    assert "Sitemap: http://testserver/sitemap.xml" in response.text


def test_sitemap_xml(client):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<loc>http://testserver/</loc>" in response.text


# --- /calculate -------------------------------------------------------------------


def test_calculate_happy(client, fixture_form):
    response = client.post("/calculate", data=fixture_form("year_classic_berlin"))
    assert response.status_code == 200
    assert '<div class="page"' in response.text


def test_calculate_parse_error_shows_banner(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    form["gender"] = "OTHER"
    response = client.post("/calculate", data=form)
    assert response.status_code == 200
    assert 'class="error"' in response.text
    assert "Select a gender." in response.text


def test_calculate_library_error_is_friendly(client, fixture_form):
    response = client.post("/calculate", data=fixture_form("error_month_cnnr"))
    assert response.status_code == 200
    assert "Switch the calculation method to Classic." in response.text


def test_calculate_out_of_range_year(client, fixture_form):
    # Below the ephemeris range [1551, 2598].
    form = fixture_form("year_classic_berlin", birth_date="0900-01-01")
    response = client.post("/calculate", data=form)
    assert response.status_code == 200
    assert "outside the supported range" in response.text


def test_calculate_escapes_name(client, fixture_form):
    """The name is echoed into hidden inputs and the aria-label; Jinja autoescape
    must neutralize markup."""
    form = fixture_form("year_classic_berlin")
    form["name"] = "<script>alert(1)</script>"
    response = client.post("/calculate", data=form)
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


# --- /download.pdf ----------------------------------------------------------------


def test_download_pdf_happy(client, fixture_form, download_form):
    payload = download_form(fixture_form("year_classic_berlin"))
    response = client.post("/download.pdf", data=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert (
        'filename="namkha-Sample-Person-Year-Classic-1985-03-15.pdf"'
        in response.headers["content-disposition"]
    )
    assert (
        "filename*=UTF-8''namkha-Sample-Person-Year-Classic-1985-03-15.pdf"
        in response.headers["content-disposition"]
    )


def test_download_pdf_reads_only_the_result_id(client, fixture_form, download_form):
    """Birth details posted beside the id are ignored, so the PDF is always mirrors the
    preview sheet that was shown. The filename carries the subject and birth date, which
    is where a substituted input would surface."""
    payload = download_form(fixture_form("year_classic_berlin"))
    response = client.post(
        "/download.pdf",
        data={**payload, "name": "Someone Else", "birth_date": "1999-01-01"},
    )
    assert response.status_code == 200
    assert (
        'filename="namkha-Sample-Person-Year-Classic-1985-03-15.pdf"'
        in response.headers["content-disposition"]
    )


def test_download_pdf_rejects_an_unknown_result_id(client, fixture_form, download_form):
    payload = download_form(fixture_form("year_classic_berlin"))
    payload["result_id"] = "not-a-real-id"
    response = client.post("/download.pdf", data=payload)
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_download_pdf_rejects_a_forgotten_result(client, fixture_form, download_form):
    """What a user meets after the handle is evicted or the server restarts."""
    payload = download_form(fixture_form("year_classic_berlin"))
    main._result_handles.clear()
    assert client.post("/download.pdf", data=payload).status_code == 400


# --- session token gate -------------------------------------------------------------


def test_calculate_rejects_missing_session_token(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    del form["session_token"]
    response = client.post("/calculate", data=form)
    assert response.status_code == 403


def test_calculate_rejects_unknown_session_token(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    form["session_token"] = "not-a-real-token"
    response = client.post("/calculate", data=form)
    assert response.status_code == 403


def test_download_pdf_rejects_missing_session_token(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    del form["session_token"]
    response = client.post("/download.pdf", data=form)
    assert response.status_code == 403


def test_index_issues_usable_session_token(client, fixture_form):
    page = client.get("/")
    token = page.text.split('name="session_token" value="')[1].split('"')[0]
    form = fixture_form("year_classic_berlin")
    form["session_token"] = token
    response = client.post("/calculate", data=form)
    assert response.status_code == 200


def test_session_token_store_is_hard_capped(monkeypatch):
    """Distinct clients minting fresh (unexpired) tokens can't grow the store
    past the cap; the oldest tokens go first."""
    monkeypatch.setattr(main, "MAX_SESSION_TOKENS", 8)
    tokens = [main._issue_session_token(f"client-{i}") for i in range(20)]
    assert len(main._session_tokens) == main.MAX_SESSION_TOKENS
    kept = [token for token in tokens if token in main._session_tokens]
    assert kept == tokens[-main.MAX_SESSION_TOKENS :]


def test_session_token_sweep_drops_expired():
    stale = main._issue_session_token("stale-client")
    issued_client, issued_at, verified = main._session_tokens[stale]
    main._session_tokens[stale] = (
        issued_client,
        issued_at - main.SESSION_TOKEN_TTL - 1,
        verified,
    )
    fresh = main._issue_session_token("fresh-client")
    assert stale not in main._session_tokens
    assert fresh in main._session_tokens


def test_session_token_per_client_cap_protects_other_clients(monkeypatch):
    """One client reloading / in a loop gets trimmed to its own cap instead of
    evicting everybody else's token."""
    monkeypatch.setattr(main, "MAX_SESSION_TOKENS", 64)
    monkeypatch.setattr(main, "MAX_TOKENS_PER_CLIENT", 4)
    bystander = main._issue_session_token("bystander")
    for i in range(main.MAX_SESSION_TOKENS // 2):  # put the store under pressure
        main._issue_session_token(f"visitor-{i}")
    for _ in range(main.MAX_SESSION_TOKENS):
        main._issue_session_token("flooder")
    flooder_tokens = [
        token
        for token, (issued_client, _, _verified) in main._session_tokens.items()
        if issued_client == "flooder"
    ]
    assert len(flooder_tokens) == main.MAX_TOKENS_PER_CLIENT
    assert flooder_tokens == list(main._client_tokens["flooder"])
    assert bystander in main._session_tokens


def test_session_tokens_untrimmed_while_the_store_has_room(monkeypatch):
    """The per-client cap only engages under pressure, so a shared NAT address
    doesn't evict its own (possibly Turnstile-verified) tokens for nothing."""
    monkeypatch.setattr(main, "MAX_SESSION_TOKENS", 64)
    monkeypatch.setattr(main, "MAX_TOKENS_PER_CLIENT", 4)
    tokens = [
        main._issue_session_token("office-nat")
        for _ in range(main.MAX_TOKENS_PER_CLIENT * 2)
    ]
    assert all(token in main._session_tokens for token in tokens)


def test_session_token_client_index_stays_in_step():
    """_client_tokens is an index into _session_tokens; a drift would leak
    entries or raise on the next eviction."""
    for i in range(5):
        main._issue_session_token(f"client-{i % 2}")
    indexed = [token for tokens in main._client_tokens.values() for token in tokens]
    assert sorted(indexed) == sorted(main._session_tokens)


def test_marking_verified_keeps_issue_order(monkeypatch):
    """_issue_session_token sweeps and evicts from the front, which only works
    while insertion order stays issue order -- guard that marking an old token
    verified doesn't move it to the back."""
    monkeypatch.setattr(main, "MAX_SESSION_TOKENS", 4)
    oldest = main._issue_session_token("client-a")
    main._mark_session_verified(oldest)
    newer = [main._issue_session_token(f"client-{i}") for i in range(3)]
    assert oldest in main._session_tokens
    main._issue_session_token("client-z")
    assert oldest not in main._session_tokens
    assert all(token in main._session_tokens for token in newer)


# --- Turnstile gate ------------------------------------------------------------------


@pytest.fixture
def turnstile_rejects(monkeypatch):
    """Undo the suite-wide pass (tests/conftest.py) for one test."""

    async def _verify(token, client_ip):
        return turnstile.Verification(
            False, turnstile.RejectionReason.EXPIRED_TOKEN, "timeout-or-duplicate"
        )

    monkeypatch.setattr(turnstile, "verify", _verify)


@pytest.fixture
def app_log(caplog):
    """Records from the "app" logger. caplog alone is not enough: the app
    logger stops propagating once configure_logging has run in this process
    (any test that enters the lifespan), so hang caplog's handler on it
    directly and restore the level after."""
    app_logger = logging.getLogger("app")
    saved_level = app_logger.level
    app_logger.addHandler(caplog.handler)
    app_logger.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    yield caplog
    app_logger.removeHandler(caplog.handler)
    app_logger.setLevel(saved_level)


def test_calculate_rejects_a_failed_turnstile_check(
    client, fixture_form, turnstile_rejects
):
    """403, but with the HTML error partial: index.html opts htmx into swapping
    4xx HTML, so the visitor sees the banner instead of nothing."""
    response = client.post("/calculate", data=fixture_form("year_classic_berlin"))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert 'class="error"' in response.text
    assert "Could not verify that you are human" in response.text
    assert '<div class="page"' not in response.text  # nothing was calculated


def test_calculate_reveals_nothing_about_why_it_failed(
    client, fixture_form, turnstile_rejects
):
    response = client.post("/calculate", data=fixture_form("year_classic_berlin"))
    assert "timeout-or-duplicate" not in response.text


def test_a_rejection_logs_the_normalized_reason_not_the_raw_code(
    client, fixture_form, turnstile_rejects, app_log
):
    """The event log gets the bucket only, so rejections aggregate instead of
    splitting over one slug per error-code combination; the raw code is debug."""
    client.post("/calculate", data=fixture_form("year_classic_berlin"))
    records = app_log.records
    warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    debug = [r.getMessage() for r in records if r.levelno == logging.DEBUG]
    assert any("turnstile: expired-token" in message for message in warnings)
    assert not any("timeout-or-duplicate" in message for message in warnings)
    assert any("timeout-or-duplicate" in message for message in debug)


def test_calculate_passes_the_submitted_token_and_client_ip(
    client, fixture_form, monkeypatch
):
    seen = {}

    async def _verify(token, client_ip):
        seen["token"] = token
        seen["client_ip"] = client_ip
        return turnstile.Verification(True)

    monkeypatch.setattr(turnstile, "verify", _verify)
    form = fixture_form("year_classic_berlin")
    form[turnstile.TOKEN_FIELD] = "submitted-token"
    client.post("/calculate", data=form)
    assert seen == {"token": "submitted-token", "client_ip": "testclient"}


def test_calculate_normalizes_a_token_sent_as_a_file_part(
    client, fixture_form, monkeypatch
):
    """A multipart body may carry the field as a file, so form.get answers with
    an UploadFile. verify() must still be handed a plain string."""
    seen = {}

    async def _verify(token, client_ip):
        seen["token"] = token
        return turnstile.Verification(False, turnstile.RejectionReason.MISSING_TOKEN)

    monkeypatch.setattr(turnstile, "verify", _verify)
    response = client.post(
        "/calculate",
        data=fixture_form("year_classic_berlin"),
        files={turnstile.TOKEN_FIELD: ("token.txt", b"not-a-form-field")},
    )
    assert seen["token"] == ""
    assert response.status_code == 403


def test_spent_token_is_not_echoed_into_the_download_form(client, fixture_form):
    """The token is single-use; carrying it into the download form would replay
    an already spent one."""
    response = client.post("/calculate", data=fixture_form("year_classic_berlin"))
    assert response.status_code == 200
    assert turnstile.TOKEN_FIELD not in response.text


def test_download_pdf_rejects_an_unverified_session(client, fixture_form):
    """A session token alone is not enough: without a /calculate that passed
    Turnstile, the compile is out of reach."""
    form = fixture_form("year_classic_berlin")
    form["session_token"] = main._issue_session_token("testclient")  # not verified
    response = client.post("/download.pdf", data=form)
    assert response.status_code == 403


def test_calculate_verifies_the_session_for_download(
    client, fixture_form, download_form
):
    form = fixture_form("year_classic_berlin")
    form["session_token"] = main._issue_session_token("testclient")
    # Nothing calculated yet, so the session is unverified and there is no id.
    assert client.post("/download.pdf", data=form).status_code == 403
    assert client.post("/download.pdf", data=download_form(form)).status_code == 200


def test_a_rejected_form_does_not_verify_the_session(client, fixture_form):
    """Passing Turnstile is not enough on its own: the session is only marked
    once a sheet was actually produced."""
    form = fixture_form("error_month_cnnr")  # method/type mismatch, no result
    form["session_token"] = main._issue_session_token("testclient")
    assert client.post("/calculate", data=form).status_code == 200
    assert client.post("/download.pdf", data=form).status_code == 403


# --- calculate_namkha result cache --------------------------------------------------


def test_download_pdf_reuses_calculate_result(
    client, fixture_form, download_form, monkeypatch
):
    """The typical flow calls /calculate then /download.pdf for the same result;
    the second call should skip recomputing calculate_namkha (skyfield), only
    redoing the Typst compile (different output format, can't be shared)."""
    calls = []
    original = main.nc.calculate_namkha

    def counting_calculate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(main.nc, "calculate_namkha", counting_calculate)

    payload = download_form(fixture_form("year_classic_berlin"))
    assert client.post("/download.pdf", data=payload).status_code == 200
    assert len(calls) == 1


def test_calculate_namkha_cache_distinguishes_inputs(client, fixture_form):
    form_a = fixture_form("year_classic_berlin")
    form_b = dict(form_a)
    form_b["name"] = "Someone Else"

    assert client.post("/calculate", data=form_a).status_code == 200
    assert client.post("/calculate", data=form_b).status_code == 200
    assert len(main._result_cache) == 2


# --- /calculate, /download.pdf rate limit ------------------------------------------


def test_compile_rate_limited_pure_function():
    client_id = "203.0.113.8"
    under_limit = [
        main._compile_rate_limited(client_id) for _ in range(main.COMPILE_RATE_LIMIT)
    ]
    assert not any(under_limit)
    assert main._compile_rate_limited(client_id) is True


def test_calculate_http_429_after_limit(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    response = None
    for _ in range(main.COMPILE_RATE_LIMIT + 1):
        response = client.post("/calculate", data=form)
    assert response.status_code == 429


def test_download_pdf_http_429_after_limit(client, fixture_form):
    form = fixture_form("year_classic_berlin")
    response = None
    for _ in range(main.COMPILE_RATE_LIMIT + 1):
        response = client.post("/download.pdf", data=form)
    assert response.status_code == 429


# --- _userfriendly_calculation_error ----------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "MONTH supports only the CLASSIC method",
            "Switch the calculation method to Classic.",
        ),
        (
            "birth year 900 is outside the supported range [1551, 2598] "
            "(limited by the bundled ephemeris)",
            "outside the supported range",
        ),
        ("some internal failure", "Could not calculate this Namkha"),
    ],
)
def test_userfriendly_error_mapping(raw, expected):
    assert expected in main._userfriendly_calculation_error(ValueError(raw))


def test_userfriendly_error_drops_internal_detail():
    message = main._userfriendly_calculation_error(
        ValueError(
            "birth year 900 is outside the supported range [1551, 2598] "
            "(limited by the bundled ephemeris)"
        )
    )
    assert "(limited by the bundled ephemeris)" not in message
    assert message[0].isupper()


# --- /timezone --------------------------------------------------------------------


BERLIN_1985 = {
    "latitude": 52.52,
    "longitude": 13.40,
    "birth_date": "1985-06-15",
    "birth_time": "12:00",
}

KATHMANDU_1985 = {
    "latitude": 27.7172,
    "longitude": 85.3240,
    "birth_date": "1985-06-15",
    "birth_time": "12:00",
}

LVIV_1940 = {
    "latitude": 49.8397,
    "longitude": 24.0297,
    "birth_date": "1940-06-15",
    "birth_time": "12:00",
}


def test_timezone_lookup_ok(client):
    response = client.get("/timezone", params=BERLIN_1985)
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Europe/Berlin"
    assert body["label"] == "Europe/Berlin"
    assert body["derivation"] == "CERTAIN"
    assert body["resolved_timezone"].startswith("v1|LOCATION_DERIVED|Europe/Berlin|")


def test_timezone_reports_how_sure_a_historical_answer_is(client):
    """Lviv changed hands around 1940, so the form has to be told the zone is
    a guess before the user commits to a chart."""
    response = client.get("/timezone", params=LVIV_1940)
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Europe/Warsaw"
    assert body["derivation"] == "BORDERS_UNCERTAIN"


def test_timezone_accepts_a_chosen_zone(client):
    response = client.get(
        "/timezone", params={**BERLIN_1985, "timezone": "Europe/Berlin"}
    )
    assert response.status_code == 200
    assert response.json()["resolved_timezone"].startswith(
        "v1|USER_ZONE|Europe/Berlin|"
    )


def test_timezone_accepts_a_provided_offset(client):
    response = client.get("/timezone", params={**KATHMANDU_1985, "utc_offset": "+5:45"})
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] is None
    assert body["label"] is None
    assert body["resolved_timezone"].startswith("v1|USER_OFFSET||20700|")


def test_timezone_label_differs_from_the_key_on_sun_based_local_time(client):
    """Arkhangelsk in 1849 predates standard time in Russia, so the calculation
    runs on the birth longitude's mean solar time. The form must say what the
    sheet will say, not the zone key behind it."""
    response = client.get(
        "/timezone",
        params={
            "latitude": 64.5401,
            "longitude": 40.5433,
            "birth_date": "1849-12-15",
            "birth_time": "05:00",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Europe/Moscow"
    assert body["label"] == "mean solar time"


def test_timezone_reports_what_the_entered_details_imply(client):
    """The uncertainty reaches the user while the form is open, not only on the
    finished sheet."""
    body = client.get("/timezone", params=LVIV_1940).json()
    assert body["notes"] == [
        {
            "message": notes.NOTE_MESSAGES[
                nc.CalculationNote.TIMEZONE_BORDERS_UNCERTAIN
            ],
            "kind": "caution",
        }
    ]


def test_the_form_and_the_sheet_word_a_note_the_same_way(client):
    """Both go through notes_for_display, so a note cannot read one way under
    the form and another on the sheet."""
    body = client.get("/timezone", params=LVIV_1940).json()
    from_sheet = notes.notes_for_display(
        (
            nc.CalculationNoteItem(
                note=nc.CalculationNote.TIMEZONE_BORDERS_UNCERTAIN,
                note_type=nc.CalculationNoteType.CAUTION,
                message="developer-facing text",
            ),
        )
    )
    assert body["notes"] == from_sheet


def test_a_resolved_zone_carries_no_notes(client):
    assert client.get("/timezone", params=BERLIN_1985).json()["notes"] == []


def test_timezone_refuses_an_offset_that_does_not_suit_the_place(client):
    """The user learns the pairing is wrong while picking it, not after
    submitting a chart built on it."""
    response = client.get("/timezone", params={**BERLIN_1985, "utc_offset": "-4:00"})
    assert response.status_code == 400


def test_a_zone_from_the_wrong_place_is_refused_in_plain_words(client):
    """The form shows whatever comes back, so it cannot be the library's own
    wording - that names arguments and quotes solar-gap limits."""
    response = client.get(
        "/timezone", params={**BERLIN_1985, "timezone": "America/New_York"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "That time zone does not match the birth place. "
        "Check the place and the time zone."
    )


def test_timezone_refuses_a_zone_and_an_offset_together(client):
    """The library rejects the pair with a plain ValueError, which would escape
    as a 500 while every other bad input on this route answers 400."""
    response = client.get(
        "/timezone",
        params={**BERLIN_1985, "timezone": "Europe/Berlin", "utc_offset": "+1:00"},
    )
    assert response.status_code == 400


def test_timezone_refuses_an_unknown_zone(client):
    response = client.get(
        "/timezone", params={**BERLIN_1985, "timezone": "Mars/Phobos"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Select a valid birth time zone."


def test_timezone_never_invents_an_answer(client):
    """A time zone that cannot be worked out must not come back as a usable
    value; the form has to block instead."""
    response = client.get("/timezone", params={**BERLIN_1985, "utc_offset": "+22"})
    assert response.status_code == 400
    assert "resolved_timezone" not in response.json()


@pytest.mark.parametrize(
    "params",
    [
        {**BERLIN_1985, "latitude": 91},
        {**BERLIN_1985, "longitude": 181},
        {**BERLIN_1985, "latitude": "nan"},
        {**BERLIN_1985, "birth_date": "not-a-date"},
        {**BERLIN_1985, "birth_time": "half past two"},
        {key: value for key, value in BERLIN_1985.items() if key != "birth_date"},
        {key: value for key, value in BERLIN_1985.items() if key != "birth_time"},
    ],
)
def test_timezone_rejects_bad_input(client, params):
    assert client.get("/timezone", params=params).status_code == 422


def test_timezone_rate_limited_pure_function():
    client_id = "203.0.113.7"
    under_limit = [
        main._timezone_rate_limited(client_id) for _ in range(main.TIMEZONE_RATE_LIMIT)
    ]
    assert not any(under_limit)
    assert main._timezone_rate_limited(client_id) is True


def test_timezone_http_429_after_limit(client):
    response = None
    for _ in range(main.TIMEZONE_RATE_LIMIT + 1):
        response = client.get("/timezone", params=BERLIN_1985)
    assert response.status_code == 429
