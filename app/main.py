"""FastAPI app: form -> calculate_namkha -> (Typst sheet <- inline SVG) -> PDF."""

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as time_of_day  # `time` is the module, imported above
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfoNotFoundError

import namkha_calculator as nc
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from namkha_calculator.zone_derivation import resolve_timezone
from starlette.concurrency import run_in_threadpool

from app import constants, turnstile
from app.calculation_render import render_pdf, render_svg
from app.constants import SESSION_TTL
from app.event_log import configure_logging, log_event
from app.forms import (
    FIELDS,
    MAX_LOCATION_NAME_LENGTH,
    MAX_NAME_LENGTH,
    RESOLVE_AGAIN_MESSAGE,
    NamkhaRequest,
    build_request,
    parse_on_summer_time,
    parse_utc_offset,
)
from app.notes import notes_for_display
from app.timezone_tickets import issue_ticket

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start the non-blocking log listener on boot, stop it on shutdown so its
    background thread drains and exits cleanly. Turnstile is checked here too:
    without a secret the app would boot and then reject every visitor, so it is
    better to refuse to start; its siteverify client is opened here as well, so
    every verification shares one pooled connection to Cloudflare."""
    turnstile.check_configuration()
    listener = configure_logging()
    turnstile.open_client()
    try:
        yield
    finally:
        try:
            await turnstile.close_client()
        finally:
            listener.stop()  # must run even if closing the client fails


app = FastAPI(title="Namkha Calculator Web", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# Sent as HX-Trigger when /calculate refuses for want of a usable time zone. The
# form listens for it and asks /timezone again, so the next press of Calculate has
# a ticket this process knows. Without it the page would keep resubmitting the one
# ticket the server has already forgotten.
TIMEZONE_AGAIN_EVENT = "namkha-resolve-timezone-again"

# Off by default: enables the "load sample data" picker on the form, backed by
# tests/fixtures/*.json. Routes are only registered (not just hidden) when set,
# so the surface doesn't exist on a normal/production boot.
TEST_MODE_ENABLED = os.getenv("NAMKHA_TEST_MODE") == "1"


# /timezone abuse protection. Working out a time zone costs CPU in this process
# - a polygon search, and for a pre-1970 birth a ray cast over the historical
# border maps - so the cache keeps repeats cheap and a per-IP fixed window caps
# how hard one client can hammer the endpoint.
# The limit is this high because one person filling the form in sends many
# requests: the form asks again after every change to the place, the date, the
# time, the mode, the chosen zone, the offset and the summer time answer. The
# count is kept per worker, so running N workers allows N times this number.
TIMEZONE_RATE_LIMIT = 120  # requests per window per client
TIMEZONE_RATE_WINDOW = 60.0  # seconds
_timezone_hits: dict[str, tuple[float, int]] = {}
# Last sweep time per bucket store, so the stale-bucket scan runs at most once
# per window instead of on every request once the store is large.
_last_sweep: dict[int, float] = {}


@lru_cache(maxsize=4096)
def _cached_resolve_timezone(
    latitude: float,
    longitude: float,
    birth_datetime: datetime,
    zone_key: str | None,
    offset_seconds: int | None,
    on_summer_time: bool | None,
) -> nc.ResolvedTimezone:
    """The library's time zone derivation, cached.

    What comes back is a zone, not an offset. Which zone applied turns on the
    date; the zone keeps its own clock changes, so the offset is worked out
    later, when the calculation applies the zone to the birth time. The time of
    day still matters here for a zone or offset the user supplied, which is
    checked against the place at that instant.

    Nothing here catches errors. A time zone that cannot be worked out has to
    reach the user.
    """
    return resolve_timezone(
        nc.Location(latitude=latitude, longitude=longitude),
        birth_datetime,
        zone_key=zone_key,
        offset=None if offset_seconds is None else timedelta(seconds=offset_seconds),
        on_summer_time=on_summer_time,
    )


# Trusted proxy handling for the rate-limit client key. Comma-separated IPs/CIDRs
# (e.g. "10.0.0.0/8,127.0.0.1") naming reverse proxies allowed to set
# X-Forwarded-For. Empty by default: with no trusted proxies, the header is
# never consulted and the direct peer is always used, so a client can't spoof
# its way into someone else's bucket (or out of its own) by sending the header
# itself.
_TRUSTED_PROXIES = [
    ipaddress.ip_network(cidr.strip())
    for cidr in os.getenv("NAMKHA_TRUSTED_PROXIES", "").split(",")
    if cidr.strip()
]


def _is_trusted_proxy(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in _TRUSTED_PROXIES)


def _client_ip(request: Request) -> str:
    """Rate-limit bucket key for a request. If the direct peer is a trusted
    proxy, walk X-Forwarded-For from the right, skipping hops that are
    themselves trusted proxies, and return the first hop that isn't -- the
    real client as seen by our outermost trusted proxy. Otherwise the header
    is untrusted (anyone could set it) and ignored in favor of the direct peer.
    """
    direct_peer = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(direct_peer):
        return direct_peer
    forwarded_for = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop
    return direct_peer


def _rate_limited(
    hits: dict[str, tuple[float, int]], client: str, limit: int, window: float
) -> bool:
    """Fixed-window per-client limiter: one (window_start, count) pair per
    client, not a growing timestamp list -- a flooding client can't inflate
    its own bucket past O(1). In-memory, so per-worker: running uvicorn with
    N workers multiplies the effective limit by N."""
    now = time.monotonic()
    if now - _last_sweep.get(id(hits), 0.0) >= window:
        _last_sweep[id(hits)] = now
        for key, (start, _) in list(hits.items()):
            if now - start >= window:
                del hits[key]
    start, count = hits.get(client, (now, 0))
    if now - start >= window:
        start, count = now, 0
    count += 1
    hits[client] = (start, count)
    return count > limit


def _timezone_rate_limited(client: str) -> bool:
    return _rate_limited(
        _timezone_hits, client, TIMEZONE_RATE_LIMIT, TIMEZONE_RATE_WINDOW
    )


# /calculate and /download.pdf abuse protection. Unlike /timezone (an in-memory
# boundary search), these run skyfield's calculation plus a full Typst compile --
# real CPU cost per request. Same per-client fixed window, separate bucket, plus
# a process-wide concurrency cap so a handful of clients can't pin every worker
# thread compiling at once.
COMPILE_RATE_LIMIT = 10  # requests per window per client
COMPILE_RATE_WINDOW = 60.0  # seconds
_compile_hits: dict[str, tuple[float, int]] = {}
MAX_CONCURRENT_COMPILES = 4
_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILES)
COMPILE_CONTENTION_LOG_THRESHOLD = 0.5  # seconds; below this, waiting is unremarkable


def _compile_rate_limited(client: str) -> bool:
    return _rate_limited(_compile_hits, client, COMPILE_RATE_LIMIT, COMPILE_RATE_WINDOW)


def _reject_if_compile_rate_limited(client: str, route: str) -> None:
    """Shared opening guard for the two compile-bearing routes, so a third one
    can't pick up the limiter without also picking up the log line."""
    if _compile_rate_limited(client):
        logger.warning("compile rate limit exceeded for %s on %s", client, route)
        raise HTTPException(status_code=429, detail="Too many requests")


@asynccontextmanager
async def _compile_slot(route: str):
    """Acquire the shared compile semaphore, logging how long the request waited.
    `asyncio.Semaphore` doesn't expose wait time directly, so it's measured with
    a wall clock around the acquire rather than reaching into private attrs."""
    wait_start = time.monotonic()
    async with _compile_semaphore:
        waited = time.monotonic() - wait_start
        if waited > COMPILE_CONTENTION_LOG_THRESHOLD:
            logger.warning(
                "%s: waited %.2fs for compile slot (contention)", route, waited
            )
        else:
            logger.debug("%s: waited %.3fs for compile slot", route, waited)
        yield


# Lightweight gate against scripts that hit /calculate or /download.pdf directly,
# skipping the form entirely. GET / mints a token bound to the issuing client's IP;
# the form carries it as a hidden field, and _result.html repeats it in the
# download form, so one token covers both submissions from a page load. The
# routes reject requests with no token, a token this process never issued, or a
# token replayed from a different client. This is on top of, not instead of,
# the per-client compile rate limit above - it doesn't stop a
# determined attacker (load the page once, replay the token from the same IP), it
# blocks copy-pasted curl commands that never load the page at all, and tokens
# leaked or copy-pasted to a different client.
#
# A token also carries the Turnstile verdict: a /calculate that passes the bot
# check AND produces a sheet marks its token verified, and /download.pdf requires
# that mark. Otherwise the bot check would only move the abuse path -- load /,
# then POST the same compile work to /download.pdf without ever touching the
# widget.

# A token lives for SESSION_TTL, shared with the result handles below and with the
# time zone tickets, because all three stop being usable together.
# Hard cap on the store, so many distinct clients minting fresh tokens can't grow
# it without bound -- TTL alone doesn't bound anything, since a flood of tokens is
# by definition not yet expired. At the cap the oldest live token is evicted to
# make room, which is a reload for whoever held it, not a failed calculation.
MAX_SESSION_TOKENS = 4096

# Per-client cap, so one address can't fill the store on its own and push every
# other visitor out. Deliberately generous: _client_ip buckets a whole office,
# school or mobile carrier behind one NAT address, and they legitimately share it.
MAX_TOKENS_PER_CLIENT = 32

# token -> (client, issued_at, turnstile_verified), oldest first. Insertion order
# is issue order (issued_at ascending) and MUST stay that way -- the sweep below
# reads it as such. _mark_session_verified rewrites a value in place, which does
# not reorder; never move_to_end here (unlike _result_cache) or the sweep breaks.
_session_tokens: OrderedDict[str, tuple[str, float, bool]] = OrderedDict()

# client -> its own tokens, oldest first. Only an index into _session_tokens, kept
# so both caps are enforced without scanning the whole store on every GET / --
# that scan is the CPU churn the caps are here to prevent in the first place.
_client_tokens: dict[str, deque[str]] = {}


def _drop_session_token(token: str) -> None:
    """Forget a token, keeping the per-client index in step with the store."""
    issued_client, _issued_at, _verified = _session_tokens.pop(token)
    client_tokens = _client_tokens[issued_client]
    client_tokens.remove(token)  # at most MAX_TOKENS_PER_CLIENT long
    if not client_tokens:
        del _client_tokens[issued_client]


def _issue_session_token(client: str) -> str:
    now = time.monotonic()
    # Expired tokens are the oldest ones, so they sit at the front: drop until the
    # front is live again. Costs one step per token actually evicted, not a scan.
    while _session_tokens:
        oldest, (_, issued_at, _verified) = next(iter(_session_tokens.items()))
        if now - issued_at < SESSION_TTL:
            break
        _drop_session_token(oldest)
    # Trim this client to one below its cap, since a fresh token follows. Only
    # once the store is filling up: the cap is about one client crowding the
    # others out, and with room to spare nobody is being crowded. Trimming
    # unconditionally would mean a NAT'd office evicting its own verified tokens
    # -- and a verified token is what /download.pdf needs after /calculate.
    if len(_session_tokens) >= MAX_SESSION_TOKENS // 2:
        client_tokens = _client_tokens.get(client)
        while client_tokens and len(client_tokens) >= MAX_TOKENS_PER_CLIENT:
            _drop_session_token(client_tokens[0])
    # Whole store still over the hard cap (many distinct clients): oldest goes.
    while len(_session_tokens) >= MAX_SESSION_TOKENS:
        _drop_session_token(next(iter(_session_tokens)))
    token = secrets.token_urlsafe(32)
    _session_tokens[token] = (client, now, False)
    _client_tokens.setdefault(client, deque()).append(token)
    return token


def _mark_session_verified(token) -> None:
    """Record that this token's page passed Turnstile, so the download of the
    same result doesn't need a second widget."""
    entry = _session_tokens.get(token)
    if entry is not None:
        issued_client, issued_at, _verified = entry
        _session_tokens[token] = (issued_client, issued_at, True)


def _valid_session_token(token, client: str, *, require_verified: bool = False) -> bool:
    if not token:
        return False
    entry = _session_tokens.get(token)
    if entry is None:
        return False
    issued_client, issued_at, verified = entry
    if require_verified and not verified:
        return False
    return issued_client == client and time.monotonic() - issued_at < SESSION_TTL


# /calculate keeps the NamkhaRequest it calculated and sends the browser an id
# for it. /download.pdf posts that id back, so the PDF is built from the request
# that produced the sheet, not from form fields the browser could edit.
# A handle expires with the session token /download.pdf checks first. The count
# is capped as well, because an expiry time does not limit how many unexpired
# handles pile up.
MAX_RESULT_HANDLES = 4096

# id -> (request, form fields for the log, generation time), oldest first.
_result_handles: OrderedDict[str, tuple[NamkhaRequest, dict, float]] = OrderedDict()


def _oldest_handle_generated_at() -> float:
    """When the handle at the front, the oldest one, was generated."""
    _, _, generated_at = next(iter(_result_handles.values()))
    return generated_at


def _store_result_handle(namkha_request: NamkhaRequest, log_inputs: dict) -> str:
    """Keep a calculated request for its download and return the id for it.

    log_inputs is passed along because /download.pdf no longer reads a form, and its
    log line records the same fields /calculate did.
    """
    now = time.monotonic()
    # Expired handles are the oldest, so they sit at the front:
    # drop until the front is live again.
    while _result_handles and now - _oldest_handle_generated_at() >= SESSION_TTL:
        _result_handles.popitem(last=False)
    while len(_result_handles) >= MAX_RESULT_HANDLES:
        _result_handles.popitem(last=False)
    result_id = secrets.token_urlsafe(32)
    _result_handles[result_id] = (namkha_request, log_inputs, now)
    return result_id


def _read_result_handle(result_id) -> tuple[NamkhaRequest, dict] | None:
    """The request an id stands for, or None when the id is unknown or expired.

    Reading does not spend the handle: a user may download the same sheet
    multiple times.
    """
    if not result_id:
        return None
    entry = _result_handles.get(result_id)
    if entry is None:
        return None
    namkha_request, log_inputs, generated_at = entry
    if time.monotonic() - generated_at >= SESSION_TTL:
        return None
    return namkha_request, log_inputs


# calculate_namkha (skyfield astronomy) result cache. The typical flow submits the
# same form twice - /calculate for the preview, then /download.pdf for the file -
# and without this both runs redo the astronomy from scratch. The Typst compile
# itself still runs twice (SVG vs. PDF are different output formats, nothing to
# share there); this only saves the calculation in between.
# `nc.Subject` is a frozen dataclass (hashable) carrying every input that shapes
# the result and its notes - resolved_timezone, which holds the zone and the
# summer-time answer, included - and NamkhaRequest is frozen too, so the request
# itself keys the cache and a field added to it joins the key automatically.
RESULT_CACHE_MAXSIZE = 256
_result_cache: OrderedDict[NamkhaRequest, nc.NamkhaCalculationResult] = OrderedDict()
_result_cache_lock = threading.Lock()


def _cached_calculate_namkha(
    namkha_request: NamkhaRequest,
) -> nc.NamkhaCalculationResult:
    key = namkha_request
    with _result_cache_lock:
        cached = _result_cache.get(key)
        if cached is not None:
            _result_cache.move_to_end(key)
            logger.debug("result cache hit (size=%d)", len(_result_cache))
            return cached
    logger.debug("result cache miss (size=%d)", len(_result_cache))
    # Raises ValueError on bad input (method/type mismatch, unsupported birth
    # year); nothing is cached in that case since this line never returns.
    # Runs outside the lock: concurrent misses on the same key may both
    # compute (redundant work, not corruption) rather than block each other.
    result = nc.calculate_namkha(
        namkha_request.namkha_type, namkha_request.subject, namkha_request.method
    )
    with _result_cache_lock:
        _result_cache[key] = result
        if len(_result_cache) > RESULT_CACHE_MAXSIZE:
            _result_cache.popitem(last=False)
    return result


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers and crawlers request /favicon.ico at the root regardless of the
    <link> tags; redirect to the static file instead of 404ing."""
    return RedirectResponse("/static/img/favicon.ico")


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request):
    """/ is the only page worth crawling; everything else is a form action or
    JSON endpoint with nothing to index."""
    body = (
        "User-agent: *\n"
        "Disallow: /calculate\n"
        "Disallow: /download.pdf\n"
        "Disallow: /timezone\n"
        "Disallow: /test-mode/\n"
        f"Sitemap: {request.url_for('sitemap')}\n"
    )
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    """Single-page site: the sitemap just points crawlers at the homepage."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{request.url_for('index')}</loc></url>\n"
        "</urlset>\n"
    )
    return Response(body, media_type="application/xml")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "genders": constants.GENDERS,
            "namkha_types": constants.NAMKHA_TYPES,
            "methods": constants.METHODS,
            "timezones": constants.TIMEZONES,
            "fields": FIELDS,
            "library_version": constants.LIBRARY_VERSION,
            "app_version": constants.APP_VERSION,
            "prerelease_label": constants.PRERELEASE_LABEL,
            "current_year": datetime.now().year,
            "test_mode_enabled": TEST_MODE_ENABLED,
            "session_token": _issue_session_token(_client_ip(request)),
            "turnstile_sitekey": constants.TURNSTILE_SITEKEY,
            # Same caps build_request enforces, so the two can't drift apart.
            "max_name_length": MAX_NAME_LENGTH,
            "max_location_name_length": MAX_LOCATION_NAME_LENGTH,
        },
    )


def _userfriendly_calculation_error(exc: ValueError) -> str:
    """Map namkha_calculator's own ValueErrors to user-facing text instead of
    showing the raw message, which names an internal (the ephemeris file)."""
    message = str(exc)
    if "supports only the CLASSIC" in message:
        return f"{message}. Switch the calculation method to Classic."
    if "outside the supported range" in message:
        message = message.replace(" (limited by the bundled ephemeris)", "")
        return message[0].upper() + message[1:]
    if "does not exist in" in message:
        return (
            "This birth time does not exist at the birth place: it was skipped "
            "when the clocks jumped forward. Check the birth date and time."
        )
    return "Could not calculate this Namkha; check the birth date, time, and place."


# Same text whatever failed the Turnstile check, so a probing client learns
# nothing; the reason goes to the log instead.
TURNSTILE_ERROR = (
    "Could not verify that you are human. Please try again – if it keeps "
    "happening, reload the page."
)


def _sheet_label(form) -> str:
    """Alt text for the sheet image: who it is for and what was calculated."""
    name = (form.get("name") or "").strip()
    named = f" for {name}" if name else ""
    namkha_type = (form.get("namkha_type") or "").lower()
    method = (form.get("method") or "").lower()
    return f"Namkha calculation sheet{named}, {namkha_type} type, {method} method"


def _log_unexpected(route: str, form, exc: Exception) -> None:
    """Unexpected failure (e.g. a Typst compile error): log with form context.
    The caller re-raises unchanged, so the 500 response is exactly as before."""
    log_event(
        logger, route, form, outcome="error", error=repr(exc), level=logging.ERROR
    )


def _result_response(
    request: Request,
    form,
    error: str | None = None,
    svg: str | None = None,
    result_id: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Single context shape for _result.html, used by both the success and
    error swaps so the template never sees a partial context.

    The page keeps the ticket it was given. Once this process no longer holds the
    zone behind it, every further press of Calculate would fail the same way, so a
    refusal carrying RESOLVE_AGAIN_MESSAGE also asks the page to fetch a new ticket
    from /timezone.
    """
    headers = {}
    if error == RESOLVE_AGAIN_MESSAGE:
        headers["HX-Trigger"] = TIMEZONE_AGAIN_EVENT
    return templates.TemplateResponse(
        request,
        "_result.html",
        {
            "error": error,
            "svg": svg,
            "result_id": result_id,
            "session_token": form.get("session_token"),
            "sheet_label": _sheet_label(form) if svg else "",
        },
        status_code=status_code,
        headers=headers,
    )


@app.post("/calculate", response_class=HTMLResponse)
async def calculate(request: Request):
    client = _client_ip(request)
    _reject_if_compile_rate_limited(client, "/calculate")

    form = await request.form()
    session_token = form.get("session_token")
    if not _valid_session_token(session_token, client):
        raise HTTPException(
            status_code=403, detail="Session expired; reload the page and try again."
        )

    # Bot gate, before any of the expensive work. The token is single-use and
    # spent here even when the form itself turns out to be invalid -- the page
    # resets the widget after every submission, so a retry gets a fresh one.
    # form.get hands back an UploadFile when a multipart body sends the field as
    # a file part; anything that is not a plain string is no token at all.
    submitted_token = form.get(turnstile.TOKEN_FIELD)
    if not isinstance(submitted_token, str):
        submitted_token = ""
    verification = await turnstile.verify(submitted_token, client)
    if not verification.ok:
        # Only the normalized bucket goes into the event log, so rejections
        # aggregate instead of scattering over one slug per HTTP status or
        # error-code combination. The raw specifics stay at debug level, off
        # unless LOG_LEVEL says otherwise.
        log_event(
            logger,
            "/calculate",
            form,
            outcome="reject",
            error=f"turnstile: {verification.reason}",
            level=logging.WARNING,
        )
        if verification.detail:
            logger.debug(
                "turnstile rejected (%s): %s", verification.reason, verification.detail
            )
        return _result_response(request, form, error=TURNSTILE_ERROR, status_code=403)

    try:
        namkha_request = build_request(form)
    except ValueError as exc:
        # build_request only raises ValueErrors with user-facing messages.
        log_event(
            logger,
            "/calculate",
            form,
            outcome="reject",
            error=str(exc),
            level=logging.WARNING,
        )
        return _result_response(request, form, error=str(exc))

    try:
        async with _compile_slot("/calculate"):
            result = await run_in_threadpool(_cached_calculate_namkha, namkha_request)
            svg = await run_in_threadpool(render_svg, result)
    except ValueError as exc:
        log_event(
            logger,
            "/calculate",
            form,
            outcome="fail",
            error=str(exc),
            level=logging.ERROR,
        )
        return _result_response(
            request, form, error=_userfriendly_calculation_error(exc)
        )
    except Exception as exc:
        _log_unexpected("/calculate", form, exc)
        raise

    # Only now, with a result in hand: the download of this very result is what
    # the mark unlocks, so a submission that never produced one doesn't earn it.
    _mark_session_verified(session_token)
    log_event(logger, "/calculate", form, outcome="ok", result=result)
    result_id = _store_result_handle(
        namkha_request, {field: form.get(field) for field in FIELDS}
    )
    return _result_response(request, form, svg=svg, result_id=result_id)


def _pdf_filename(namkha_request: NamkhaRequest) -> str:
    """Descriptive download name: subject name (if given), namkha type,
    calculation method, birth date."""
    subject = namkha_request.subject
    parts = []
    if subject.name:
        slug = re.sub(r"[^\w]+", "-", subject.name, flags=re.UNICODE).strip("-_")
        if slug:
            parts.append(slug)
    parts.append(namkha_request.namkha_type.name.capitalize())
    parts.append(namkha_request.method.name.capitalize())
    parts.append(subject.birth_datetime.date().isoformat())
    return "namkha-" + "-".join(parts) + ".pdf"


def _content_disposition(filename: str) -> str:
    """RFC 6266 header: ASCII fallback plus a UTF-8 filename* for names with
    non-ASCII characters (e.g. accented or Tibetan subject names).

    Strips CR/LF itself rather than trusting the caller to have done so --
    header-injection safety must not depend on _pdf_filename's regex staying
    strict."""
    filename = filename.replace("\r", "").replace("\n", "")
    ascii_fallback = (
        filename.encode("ascii", "ignore").decode("ascii").replace('"', "'")
    )
    if not ascii_fallback:
        ascii_fallback = "namkha.pdf"
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


@app.post("/download.pdf")
async def download_pdf(request: Request):
    client = _client_ip(request)
    _reject_if_compile_rate_limited(client, "/download.pdf")

    form = await request.form()
    # The result being downloaded came from a /calculate that passed Turnstile;
    # requiring the mark keeps this route from being a way around the widget.
    if not _valid_session_token(
        form.get("session_token"), client, require_verified=True
    ):
        raise HTTPException(
            status_code=403,
            detail="Session expired; reload the page and calculate again.",
        )

    handle = _read_result_handle(form.get("result_id"))
    if handle is None:
        raise HTTPException(
            status_code=400,
            detail="This result has expired. Please calculate again.",
        )
    namkha_request, log_inputs = handle

    try:
        async with _compile_slot("/download.pdf"):
            result = await run_in_threadpool(_cached_calculate_namkha, namkha_request)
            pdf = await run_in_threadpool(render_pdf, result)
    except ValueError as exc:
        # /calculate already ran this request, so a failure here means the
        # library answered differently the second time. Kept as a safeguard.
        log_event(
            logger,
            "/download.pdf",
            log_inputs,
            outcome="fail",
            error=str(exc),
            level=logging.ERROR,
        )
        raise HTTPException(
            status_code=400, detail=_userfriendly_calculation_error(exc)
        ) from exc
    except Exception as exc:
        _log_unexpected("/download.pdf", log_inputs, exc)
        raise

    log_event(logger, "/download.pdf", log_inputs, outcome="ok", result=result)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(_pdf_filename(namkha_request))
        },
    )


@app.get("/timezone")
async def timezone_lookup(
    request: Request,
    birth_date: date,
    birth_time: time_of_day,
    # Declarative bounds: FastAPI rejects out-of-range / non-numeric / inf / nan
    # before the body runs, returning a structured 422 naming the bad field.
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    timezone: str = Query(default=""),
    utc_offset: str = Query(default=""),
    on_summer_time: str = Query(default=""),
):
    """Resolve the birth time zone, keep it, and hand the form a ticket for it.

    This is the only place a time zone is worked out. The answer carries how
    sure it is, so the user sees any doubt before committing to a chart rather
    than finding it on the finished sheet.
    """
    client = _client_ip(request)
    if _timezone_rate_limited(client):
        raise HTTPException(status_code=429, detail="Too many requests")

    zone_key = timezone.strip()
    # The library refuses both at once with a plain ValueError, which would
    # leave this route as the one input error that 500s instead of 400s.
    if zone_key and utc_offset.strip():
        raise HTTPException(
            status_code=400,
            detail="Give either a time zone or a UTC offset, not both.",
        )

    # The library turns a zone key into a path under its bundled tzdata, so only
    # a key the picker offers reaches it. An empty key is automatic mode, where
    # the zone comes from a place and a date.
    if zone_key and zone_key not in constants.ZONE_KEYS:
        raise HTTPException(status_code=400, detail="Select a valid birth time zone.")

    try:
        offset = parse_utc_offset(utc_offset) if utc_offset.strip() else None
        summer_time = parse_on_summer_time(on_summer_time)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    birth_datetime = datetime.combine(birth_date, birth_time)
    try:
        # CPU-bound; offload so it doesn't block the event loop.
        resolved = await run_in_threadpool(
            _cached_resolve_timezone,
            latitude,
            longitude,
            birth_datetime,
            zone_key or None,
            None if offset is None else round(offset.total_seconds()),
            summer_time,
        )
    except ZoneInfoNotFoundError as error:
        raise HTTPException(
            status_code=400, detail="Select a valid birth time zone."
        ) from error
    except nc.TimezoneLocationMismatchError as error:
        raise HTTPException(
            status_code=400,
            detail="That time zone does not match the birth place. "
            "Check the place and the time zone.",
        ) from error
    except nc.TimezoneError as error:
        raise HTTPException(
            status_code=400,
            detail="Could not work out the time zone for this birth.",
        ) from error

    # input_notes below is deliberately outside _cached_resolve_timezone: next to
    # the polygon search it costs nothing, and keeping it out leaves that cache
    # holding only the expensive part.
    return {
        # The zone itself stays here. The form gets only this ticket for it, and
        # /calculate reads the zone back out of the store.
        "timezone_ticket": issue_ticket(resolved),
        "timezone": resolved.key,
        # What the sheet will name this time zone. Not always the key: a birth
        # before standard time reached the place runs on sun-based local time.
        "label": nc.timezone_label(resolved, birth_datetime),
        "derivation": resolved.derivation.name,
        "notes": notes_for_display(
            nc.input_notes(
                resolved,
                nc.Location(latitude=latitude, longitude=longitude),
                birth_datetime,
            )
        ),
    }


if TEST_MODE_ENABLED:
    FIXTURES_DIR = BASE.parent / "tests" / "fixtures"

    @app.get("/test-mode/fixtures")
    async def list_fixtures() -> list[str]:
        return sorted(path.stem for path in FIXTURES_DIR.glob("*.json"))

    @app.get("/test-mode/fixtures/{name}")
    async def get_fixture(name: str) -> dict:
        # Build from a bare stem (FastAPI's {name} can't contain "/") and
        # confirm the result still resolves inside FIXTURES_DIR, so "../x"
        # tricks via dot-segments in `name` can't escape the fixtures folder.
        path = (FIXTURES_DIR / f"{name}.json").resolve()
        if path.parent != FIXTURES_DIR.resolve() or not path.is_file():
            raise HTTPException(status_code=404)
        return json.loads(path.read_text())
