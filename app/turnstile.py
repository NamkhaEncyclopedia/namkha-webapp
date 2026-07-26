"""Cloudflare Turnstile verification for the calculator form.

The widget on the form issues a single-use token; this module trades it in at
Cloudflare's siteverify endpoint before an expensive calculation runs. Only the
server ever talks to siteverify -- browser -> our backend -> Cloudflare.

Fails closed: missing configuration, a network error, an unexpected response, a
wrong action or an unknown frontend hostname all reject the request.
"""

import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Name of the hidden input the widget adds to the surrounding form.
TOKEN_FIELD = "cf-turnstile-response"

# Must match data-action on the widget; siteverify echoes it back, so a token
# minted for some other surface can't be spent here.
ACTION = "calculate"

MAX_TOKEN_LENGTH = 2048
REQUEST_TIMEOUT = 10.0

# Frontends allowed to produce tokens. Both hostnames serve this app and are
# registered on the widget. Never add localhost here for a production deploy --
# local development overrides this with the Cloudflare test keys instead.
DEFAULT_HOSTNAMES = "calculator.namkha-encyclopedia.com,namkha-webapp.fly.dev"

# Cloudflare's published test secrets (always-passes, always-fails, and
# token-already-spent). Local development runs on these; see verify().
TEST_SECRETS = frozenset(
    {
        "1x0000000000000000000000000000000AA",
        "2x0000000000000000000000000000000AA",
        "3x0000000000000000000000000000000AA",
    }
)


class RejectionReason(StrEnum):
    """The closed set of rejection buckets. Deliberately small: this value is
    what goes into the aggregated event log, and an unbounded set of slugs
    (every HTTP status, every combination of Cloudflare error codes) would
    scatter one rejection across dozens of distinct log lines. Anything finer
    lives in `Verification.detail` and is logged at debug level only.

    UNCONFIGURED covers both a deploy missing its settings and a secret or
    widget id Cloudflare itself rejects -- one bucket to alert on, because both
    mean the deploy is broken and every visitor is being turned away.
    """

    UNCONFIGURED = "unconfigured"
    MISSING_TOKEN = "missing-token"
    OVERSIZED_TOKEN = "oversized-token"
    EXPIRED_TOKEN = "expired-token"
    TOKEN_REJECTED = "token-rejected"
    ACTION_MISMATCH = "action-mismatch"
    HOSTNAME_MISMATCH = "hostname-mismatch"
    SITEVERIFY_UNAVAILABLE = "siteverify-unavailable"
    BAD_RESPONSE = "bad-response"


# Cloudflare's documented error codes that map to something more specific than
# "Cloudflare said no". Codes about the token itself (missing-input-response,
# invalid-input-response, bad-request) and any code added in the future fall
# through to TOKEN_REJECTED.
_ERROR_CODE_REASONS = {
    "missing-input-secret": RejectionReason.UNCONFIGURED,
    "invalid-input-secret": RejectionReason.UNCONFIGURED,
    "invalid-parsed-secret": RejectionReason.UNCONFIGURED,
    "invalid-widget-id": RejectionReason.UNCONFIGURED,
    "timeout-or-duplicate": RejectionReason.EXPIRED_TOKEN,
    "internal-error": RejectionReason.SITEVERIFY_UNAVAILABLE,
}


def _reason_for_codes(codes: list[str]) -> RejectionReason:
    """Normalize Cloudflare's error codes to one bucket. A response can carry
    several codes; the first mapped one wins, so a configuration problem still
    surfaces when it arrives alongside an ordinary token complaint."""
    for code in codes:
        reason = _ERROR_CODE_REASONS.get(code)
        if reason is not None:
            return reason
    return RejectionReason.TOKEN_REJECTED


@dataclass(frozen=True)
class Verification:
    """Outcome of one siteverify round trip. `reason` is a normalized bucket for
    the log only -- visitors always get the same generic message, so a probing
    client learns nothing about which check rejected it. `detail` carries the
    raw, high-cardinality specifics (HTTP status, Cloudflare error codes) for
    debug logging."""

    ok: bool
    reason: RejectionReason | None = None
    detail: str = ""


def secret() -> str:
    """Widget secret, read live so it is never baked into an import-time
    constant (and so tests can set it per case). Never logged."""
    return os.getenv("TURNSTILE_SECRET", "").strip()


def expected_hostnames() -> frozenset[str]:
    raw = os.getenv("TURNSTILE_HOSTNAMES") or DEFAULT_HOSTNAMES
    return frozenset(host.strip() for host in raw.split(",") if host.strip())


def check_configuration() -> None:
    """Refuse to boot without the pieces verification needs, the same way
    configure_logging refuses to boot without a log salt. Without this the app
    would start and reject every single visitor instead."""
    if not secret():
        raise RuntimeError(
            "TURNSTILE_SECRET must be set: the calculator form is gated by "
            "Cloudflare Turnstile and every submission is verified server-side."
        )
    if not expected_hostnames():
        raise RuntimeError("TURNSTILE_HOSTNAMES must not be empty if it is set.")


# One client for the whole process, so verifications keep the connection to
# siteverify alive instead of paying for a TCP + TLS handshake on every form
# submission. Opened and closed by the app lifespan (see main._lifespan), on the
# loop that will run the requests -- httpx pools connections per event loop, so
# a client built on one loop must not be used from another.
#
# A module global rather than app.state: _siteverify has no request or app
# handle, and threading one through verify() would change its signature and
# every caller for no gain.
_client: httpx.AsyncClient | None = None


def open_client() -> None:
    """Open the shared siteverify client. Call from the app lifespan."""
    global _client
    _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)


async def close_client() -> None:
    """Close the shared client on shutdown. Clearing the global *before*
    awaiting the close is load-bearing: a verification racing shutdown then
    sees no shared client and opens its own, instead of posting to a closed
    one. Don't reorder these two lines."""
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


@asynccontextmanager
async def _siteverify_client():
    """The shared client if the lifespan opened one, otherwise a throwaway.
    The fallback keeps verify() usable with no app running -- tests and
    scripts call it directly."""
    if _client is not None:
        yield _client
    else:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            yield client


async def _siteverify(payload: dict[str, str]) -> dict:
    """POST the token to Cloudflare and return the parsed response.

    Raises httpx.HTTPError on transport failure or a non-2xx status, and
    ValueError on a body that isn't JSON.
    """
    async with _siteverify_client() as client:
        response = await client.post(SITEVERIFY_URL, data=payload)
    response.raise_for_status()
    return response.json()


async def verify(token: str, client_ip: str) -> Verification:
    """Verify one form submission's Turnstile token.

    Callers pass a plain string; the isinstance check below stays anyway, so a
    caller that hands over a raw form value still fails closed instead of
    reaching Cloudflare with something odd.
    """
    widget_secret = secret()
    hostnames = expected_hostnames()
    if not widget_secret or not hostnames:
        logger.error("turnstile is not configured; rejecting")
        return Verification(
            False, RejectionReason.UNCONFIGURED, "no secret or hostname"
        )
    if not isinstance(token, str) or not token:
        return Verification(False, RejectionReason.MISSING_TOKEN)
    if len(token) > MAX_TOKEN_LENGTH:
        return Verification(False, RejectionReason.OVERSIZED_TOKEN)

    payload = {"secret": widget_secret, "response": token}
    # Advisory input to Cloudflare's scoring, not part of our decision. Only
    # sent when it really is an address: _client_ip falls back to "unknown"
    # (and is "testclient" under TestClient), which earns a bad-request.
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        pass
    else:
        payload["remoteip"] = client_ip

    try:
        result = await _siteverify(payload)
    except httpx.HTTPStatusError as error:
        return Verification(
            False,
            RejectionReason.SITEVERIFY_UNAVAILABLE,
            f"http-{error.response.status_code}",
        )
    except httpx.HTTPError:
        return Verification(
            False, RejectionReason.SITEVERIFY_UNAVAILABLE, "network-error"
        )
    except ValueError:
        return Verification(False, RejectionReason.BAD_RESPONSE, "unparseable body")

    if result.get("success") is not True:
        codes = [str(code) for code in result.get("error-codes") or []]
        return Verification(
            False, _reason_for_codes(codes), ",".join(codes) or "no-error-code"
        )
    # Cloudflare's test secrets answer with a canned verdict whatever the token:
    # hostname "example.com" and no action at all, so the two checks below could
    # never pass and local development would be impossible. Gated on the secret
    # being one of the published test keys, not only on Cloudflare's flag, so
    # this branch cannot be reached by a deploy holding a real secret.
    if (
        widget_secret in TEST_SECRETS
        and isinstance(result.get("metadata"), dict)
        and result["metadata"].get("result_with_testing_key")
    ):
        logger.warning("turnstile verified with a Cloudflare test key")
        return Verification(True)
    if result.get("action") != ACTION:
        return Verification(False, RejectionReason.ACTION_MISMATCH)
    if result.get("hostname") not in hostnames:
        return Verification(False, RejectionReason.HOSTNAME_MISMATCH)
    return Verification(True)
