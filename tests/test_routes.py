"""HTTP routes via Starlette's TestClient, plus the error-mapping and rate-limit
helpers they depend on."""

import pytest

from app import main


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
    form = fixture_form("year_classic_berlin")
    form["birth_date"] = "0900-01-01"  # below the ephemeris range [1551, 2598]
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


def test_download_pdf_happy(client, fixture_form):
    response = client.post("/download.pdf", data=fixture_form("year_classic_berlin"))
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


def test_download_pdf_error_is_400(client, fixture_form):
    response = client.post("/download.pdf", data=fixture_form("error_month_cnnr"))
    assert response.status_code == 400
    assert "Switch the calculation method to Classic." in response.json()["detail"]


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
    issued_client, issued_at = main._session_tokens[stale]
    main._session_tokens[stale] = (
        issued_client,
        issued_at - main.SESSION_TOKEN_TTL - 1,
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
        for token, (issued_client, _) in main._session_tokens.items()
        if issued_client == "flooder"
    ]
    assert len(flooder_tokens) == main.MAX_TOKENS_PER_CLIENT
    assert flooder_tokens == list(main._client_tokens["flooder"])
    assert bystander in main._session_tokens


def test_session_tokens_untrimmed_while_the_store_has_room(monkeypatch):
    """The per-client cap only engages under pressure, so a shared NAT address
    doesn't evict its own tokens for nothing."""
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


# --- calculate_namkha result cache --------------------------------------------------


def test_download_pdf_reuses_calculate_result(client, fixture_form, monkeypatch):
    """The typical flow hits /calculate then /download.pdf with the same form;
    the second call should skip recomputing calculate_namkha (skyfield), only
    redoing the Typst compile (different output format, can't be shared)."""
    calls = []
    original = main.nc.calculate_namkha

    def counting_calculate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(main.nc, "calculate_namkha", counting_calculate)

    form = fixture_form("year_classic_berlin")
    assert client.post("/calculate", data=form).status_code == 200
    assert client.post("/download.pdf", data=form).status_code == 200
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


def test_timezone_lookup_ok(client):
    response = client.get("/timezone", params={"latitude": 52.52, "longitude": 13.40})
    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Berlin"


@pytest.mark.parametrize(
    "params",
    [
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": 181},
        {"latitude": "nan", "longitude": 0},
    ],
)
def test_timezone_rejects_bad_coordinates(client, params):
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
        response = client.get(
            "/timezone", params={"latitude": 52.52, "longitude": 13.40}
        )
    assert response.status_code == 429
