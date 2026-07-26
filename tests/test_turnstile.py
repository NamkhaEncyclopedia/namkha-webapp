"""Turnstile verification: the decision logic against a stubbed siteverify, plus
one pass over the real HTTP path with a mock transport.

The suite never reaches Cloudflare. `_siteverify` is the only place that does,
so stubbing it isolates every branch of `verify`.

`verify` is a coroutine and the project has no async test plugin, so the tests
stay sync and drive it through `asyncio.run`. The real function is captured at
import, before the root conftest's autouse stub swaps the module attribute out.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from starlette.testclient import TestClient

from app import main, turnstile

_verify = turnstile.verify

TOKEN = "a-token"


def verify(token, client_ip="1.2.3.4"):
    """Drive the coroutine from a sync test. On its own thread, because when the
    browser layer is installed Playwright's sync API keeps an event loop running
    in the main thread and asyncio.run refuses to nest."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, _verify(token, client_ip)).result()


@pytest.fixture(autouse=True)
def _turnstile_config(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "test-secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", "example.test, other.test")


@pytest.fixture(autouse=True)
def _no_shared_client():
    """turnstile._client is process-global; clear it after every test here, since
    a client left behind is bound to that test's event loop, dead by the time the
    next test runs. Module-local on purpose: only this file opens one, and the
    browser layer's session-scoped live server must keep the client its own
    lifespan opened."""
    yield
    turnstile._client = None


def stub_siteverify(monkeypatch, response, calls=None):
    """Replace the HTTP call with a canned response, or an exception to raise."""

    async def _stub(payload):
        if calls is not None:
            calls.append(payload)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(turnstile, "_siteverify", _stub)


def passing(**overrides):
    result = {
        "success": True,
        "action": turnstile.ACTION,
        "hostname": "example.test",
        "challenge_ts": "2026-07-25T18:00:00Z",
    }
    result.update(overrides)
    return result


def stub_transport(monkeypatch, handler, opened=None):
    """Point httpx at a mock transport, so _siteverify itself can be exercised.
    `opened` collects every client built, for the pooling tests below."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def build_client(**kwargs):
        client = real_client(transport=transport, **kwargs)
        if opened is not None:
            opened.append(client)
        return client

    monkeypatch.setattr(turnstile.httpx, "AsyncClient", build_client)


# --- configuration ----------------------------------------------------------------


def test_rejects_without_secret(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET")
    stub_siteverify(monkeypatch, passing())
    assert verify(TOKEN).reason is turnstile.RejectionReason.UNCONFIGURED


def test_rejects_with_empty_hostname_allowlist(monkeypatch):
    monkeypatch.setenv("TURNSTILE_HOSTNAMES", " , ")
    stub_siteverify(monkeypatch, passing())
    assert verify(TOKEN).reason is turnstile.RejectionReason.UNCONFIGURED


def test_check_configuration_requires_secret(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET")
    with pytest.raises(RuntimeError, match="TURNSTILE_SECRET"):
        turnstile.check_configuration()


def test_check_configuration_passes_when_set():
    turnstile.check_configuration()  # env from the fixture; must not raise


def test_hostnames_fall_back_to_the_deployed_defaults(monkeypatch):
    monkeypatch.delenv("TURNSTILE_HOSTNAMES")
    assert turnstile.expected_hostnames() == frozenset(
        {"calculator.namkha-encyclopedia.com", "namkha-webapp.fly.dev"}
    )


# --- token shape (rejected before any network call) --------------------------------


@pytest.mark.parametrize("token", [None, "", 42])
def test_rejects_missing_token(monkeypatch, token):
    stub_siteverify(monkeypatch, RuntimeError("must not be called"))
    assert verify(token) == turnstile.Verification(
        False, turnstile.RejectionReason.MISSING_TOKEN
    )


def test_rejects_oversized_token(monkeypatch):
    stub_siteverify(monkeypatch, RuntimeError("must not be called"))
    huge = "x" * (turnstile.MAX_TOKEN_LENGTH + 1)
    assert verify(huge) == turnstile.Verification(
        False, turnstile.RejectionReason.OVERSIZED_TOKEN
    )


# --- the siteverify verdict --------------------------------------------------------


def test_accepts_a_valid_token(monkeypatch):
    stub_siteverify(monkeypatch, passing())
    assert verify(TOKEN).ok is True


def test_rejects_unsuccessful_verification(monkeypatch):
    stub_siteverify(
        monkeypatch, {"success": False, "error-codes": ["timeout-or-duplicate"]}
    )
    outcome = verify(TOKEN)
    assert outcome.ok is False
    assert outcome.reason is turnstile.RejectionReason.EXPIRED_TOKEN
    assert outcome.detail == "timeout-or-duplicate"


def test_rejects_unsuccessful_verification_without_codes(monkeypatch):
    stub_siteverify(monkeypatch, {"success": False})
    outcome = verify(TOKEN)
    assert outcome.reason is turnstile.RejectionReason.TOKEN_REJECTED
    assert outcome.detail == "no-error-code"


def test_rejects_a_token_minted_for_another_action(monkeypatch):
    stub_siteverify(monkeypatch, passing(action="signup"))
    assert verify(TOKEN) == turnstile.Verification(
        False, turnstile.RejectionReason.ACTION_MISMATCH
    )


def test_rejects_an_unknown_frontend_hostname(monkeypatch):
    stub_siteverify(monkeypatch, passing(hostname="evil.test"))
    assert verify(TOKEN) == turnstile.Verification(
        False, turnstile.RejectionReason.HOSTNAME_MISMATCH
    )


def test_accepts_any_hostname_on_the_allowlist(monkeypatch):
    stub_siteverify(monkeypatch, passing(hostname="other.test"))
    assert verify(TOKEN).ok is True


def _test_key_verdict(**overrides):
    result = {
        "success": True,
        "hostname": "example.com",
        "error-codes": [],
        "metadata": {"result_with_testing_key": True},
    }
    result.update(overrides)
    return result


def test_accepts_the_canned_verdict_from_a_cloudflare_test_secret(monkeypatch):
    """The test secrets answer with hostname "example.com" and no action, so the
    two checks above can only be skipped -- see the comment in verify()."""
    monkeypatch.setenv("TURNSTILE_SECRET", "1x0000000000000000000000000000000AA")
    stub_siteverify(monkeypatch, _test_key_verdict())
    assert verify(TOKEN).ok is True


def test_the_test_key_branch_is_out_of_reach_for_a_real_secret(monkeypatch):
    """The flag alone must not skip the hostname and action checks: a real
    deployment holds a real secret, so the branch can never open there."""
    stub_siteverify(monkeypatch, _test_key_verdict())
    assert verify(TOKEN) == turnstile.Verification(
        False, turnstile.RejectionReason.ACTION_MISMATCH
    )


def test_a_failed_test_key_verdict_is_still_a_rejection(monkeypatch):
    """The always-fails and token-already-spent test secrets must not slip
    through the test-key branch: success is checked first."""
    monkeypatch.setenv("TURNSTILE_SECRET", "3x0000000000000000000000000000000AA")
    stub_siteverify(
        monkeypatch,
        {
            "success": False,
            "error-codes": ["timeout-or-duplicate"],
            "metadata": {"result_with_testing_key": True},
        },
    )
    assert verify(TOKEN).reason is turnstile.RejectionReason.EXPIRED_TOKEN


# --- rejection reasons are normalized to a small set --------------------------------


@pytest.mark.parametrize(
    ("codes", "reason"),
    [
        (["invalid-input-response"], turnstile.RejectionReason.TOKEN_REJECTED),
        (["bad-request"], turnstile.RejectionReason.TOKEN_REJECTED),
        (["a-code-cloudflare-added-later"], turnstile.RejectionReason.TOKEN_REJECTED),
        (["timeout-or-duplicate"], turnstile.RejectionReason.EXPIRED_TOKEN),
        (["invalid-input-secret"], turnstile.RejectionReason.UNCONFIGURED),
        (["invalid-widget-id"], turnstile.RejectionReason.UNCONFIGURED),
        (["internal-error"], turnstile.RejectionReason.SITEVERIFY_UNAVAILABLE),
    ],
)
def test_error_codes_normalize_to_one_bucket(monkeypatch, codes, reason):
    stub_siteverify(monkeypatch, {"success": False, "error-codes": codes})
    assert verify(TOKEN).reason is reason


def test_a_configuration_code_wins_over_a_token_code(monkeypatch):
    """Several codes at once must not hide the one worth alerting on."""
    stub_siteverify(
        monkeypatch,
        {
            "success": False,
            "error-codes": ["invalid-input-response", "invalid-input-secret"],
        },
    )
    outcome = verify(TOKEN)
    assert outcome.reason is turnstile.RejectionReason.UNCONFIGURED
    assert outcome.detail == "invalid-input-response,invalid-input-secret"


def test_every_reason_is_a_member_of_the_enum(monkeypatch):
    """The whole point of the bucket: what reaches the log is a closed set, so
    the raw codes and statuses can only ever land in `detail`."""
    stub_siteverify(monkeypatch, {"success": False, "error-codes": ["bad-request"]})
    assert isinstance(verify(TOKEN).reason, turnstile.RejectionReason)


# --- transport failures all fail closed --------------------------------------------


def test_rejects_on_network_error(monkeypatch):
    stub_siteverify(monkeypatch, httpx.ConnectError("no route"))
    outcome = verify(TOKEN)
    assert outcome.reason is turnstile.RejectionReason.SITEVERIFY_UNAVAILABLE
    assert outcome.detail == "network-error"


def test_rejects_on_timeout(monkeypatch):
    stub_siteverify(monkeypatch, httpx.ReadTimeout("too slow"))
    assert verify(TOKEN).reason is turnstile.RejectionReason.SITEVERIFY_UNAVAILABLE


def test_rejects_on_unparseable_body(monkeypatch):
    stub_siteverify(monkeypatch, ValueError("not json"))
    assert verify(TOKEN).reason is turnstile.RejectionReason.BAD_RESPONSE


@pytest.mark.parametrize("status", [500, 503])
def test_rejects_on_server_error(monkeypatch, status):
    """Every status collapses into one bucket; the status itself is detail."""
    stub_transport(monkeypatch, lambda request: httpx.Response(status))
    outcome = verify(TOKEN)
    assert outcome.reason is turnstile.RejectionReason.SITEVERIFY_UNAVAILABLE
    assert outcome.detail == f"http-{status}"


# --- what actually goes on the wire ------------------------------------------------


def test_sends_secret_token_and_client_ip(monkeypatch):
    calls = []
    stub_siteverify(monkeypatch, passing(), calls=calls)
    verify(TOKEN, "203.0.113.7")
    assert calls == [
        {"secret": "test-secret", "response": TOKEN, "remoteip": "203.0.113.7"}
    ]


@pytest.mark.parametrize("client", ["unknown", "testclient", ""])
def test_omits_remoteip_when_the_client_is_not_an_ip(monkeypatch, client):
    """_client_ip falls back to placeholders; sending one earns a bad-request."""
    calls = []
    stub_siteverify(monkeypatch, passing(), calls=calls)
    verify(TOKEN, client)
    assert "remoteip" not in calls[0]


def test_posts_a_form_encoded_body_to_cloudflare(monkeypatch):
    """The one test over the real _siteverify: URL, method, and encoding are what
    canonical siteverify expects."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=passing())

    stub_transport(monkeypatch, handler)

    assert verify(TOKEN, "203.0.113.7").ok is True
    assert seen["method"] == "POST"
    assert seen["url"] == turnstile.SITEVERIFY_URL
    assert seen["content_type"] == "application/x-www-form-urlencoded"
    assert seen["body"] == f"secret=test-secret&response={TOKEN}&remoteip=203.0.113.7"


# --- the shared client -------------------------------------------------------------


def verify_twice():
    """Two verifications on ONE event loop, which is what the shared client
    needs: a pooled connection can only be reused within the loop it was
    opened on."""

    async def both():
        return [await _verify(TOKEN, "1.2.3.4"), await _verify(TOKEN, "1.2.3.4")]

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, both()).result()


def test_verifications_share_the_client_opened_by_the_lifespan(monkeypatch):
    """The point of the whole thing: with a client open, both verifications go
    through that one client instead of building (and handshaking) one each."""
    opened = []
    stub_transport(
        monkeypatch, lambda request: httpx.Response(200, json=passing()), opened
    )

    async def open_verify_twice_close():
        turnstile.open_client()
        try:
            return await asyncio.gather(
                _verify(TOKEN, "1.2.3.4"), _verify(TOKEN, "1.2.3.4")
            )
        finally:
            await turnstile.close_client()

    with ThreadPoolExecutor(1) as pool:
        outcomes = pool.submit(asyncio.run, open_verify_twice_close()).result()

    assert [outcome.ok for outcome in outcomes] == [True, True]
    assert len(opened) == 1  # the shared one; no per-verification fallback
    assert opened[0].is_closed  # closed on shutdown, not left dangling
    assert turnstile._client is None


def test_each_verification_opens_its_own_client_without_a_lifespan(monkeypatch):
    """Fallback path: verify() stays usable with no app running (tests, scripts),
    at the old cost of a client per call."""
    opened = []
    stub_transport(
        monkeypatch, lambda request: httpx.Response(200, json=passing()), opened
    )

    outcomes = verify_twice()

    assert [outcome.ok for outcome in outcomes] == [True, True]
    assert len(opened) == 2
    assert all(client.is_closed for client in opened)


def test_the_app_lifespan_opens_and_closes_the_client():
    """main._lifespan is what opens the client in the real app; without that
    wiring every request would silently take the fallback path. TestClient only
    runs the lifespan when it is entered as a context manager -- the `client`
    fixture doesn't, which is why this test builds its own."""
    with TestClient(main.app):
        assert turnstile._client is not None
    assert turnstile._client is None
