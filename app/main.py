"""FastAPI app: form -> calculate_namkha -> (Typst sheet <- inline SVG) -> PDF."""

import asyncio
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import namkha_calculator as nc
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import constants
from app.calculation_render import render_pdf, render_svg
from app.forms import FIELDS, NamkhaRequest, build_request

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent

app = FastAPI(title="Namkha Calculator Web")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# Off by default: enables the "load sample data" picker on the form, backed by
# tests/fixtures/*.json. Routes are only registered (not just hidden) when set,
# so the surface doesn't exist on a normal/production boot.
TEST_MODE_ENABLED = os.getenv("NAMKHA_TEST_MODE") == "1"


@lru_cache(maxsize=1)
def _timezone_finder():
    """Lazily build the finder on first /timezone hit. Import + construction load
    boundary data and can fail (bad install, missing data); deferring it keeps a
    failure from taking down the whole app at startup -- only /timezone degrades."""
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


# /timezone abuse protection. The lookup is an in-memory boundary search (no
# external API), so the cost is CPU. Cache keeps repeated/nearby coordinates
# cheap; a per-IP fixed window caps how hard one client can hammer the endpoint.
TIMEZONE_RATE_LIMIT = 30  # requests per window per client
TIMEZONE_RATE_WINDOW = 60.0  # seconds
_timezone_hits: dict[str, tuple[float, int]] = {}


@lru_cache(maxsize=4096)
def _cached_timezone(latitude: float, longitude: float) -> str | None:
    """Boundary lookup keyed on rounded coordinates (see timezone_lookup).
    Returns None if the finder can't be built or the lookup fails, so the
    endpoint falls back to UTC instead of erroring."""
    try:
        return _timezone_finder().timezone_at(lat=latitude, lng=longitude)
    except Exception:
        return None


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
    if len(hits) > 1024:  # sweep stale buckets so unique IPs can't leak
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
# the form carries it as a hidden field, and _result.html's form.items() echo
# carries it on into the download form too, so one token covers both submissions
# from a page load. The routes reject requests with no token, a token this process
# never issued, or a token replayed from a different client. This is on top of,
# not instead of, the per-client compile rate limit above -- it doesn't stop a
# determined attacker (load the page once, replay the token from the same IP), it
# blocks copy-pasted curl commands that never load the page at all, and tokens
# leaked or copy-pasted to a different client.
SESSION_TOKEN_TTL = 1800.0  # seconds; long enough to fill the form unhurried
_session_tokens: dict[str, tuple[str, float]] = {}  # token -> (client, issued_at)


def _issue_session_token(client: str) -> str:
    if len(_session_tokens) > 4096:  # sweep stale, so the dict can't grow unbounded
        now = time.monotonic()
        for token, (_, issued_at) in list(_session_tokens.items()):
            if now - issued_at >= SESSION_TOKEN_TTL:
                del _session_tokens[token]
    token = secrets.token_urlsafe(32)
    _session_tokens[token] = (client, time.monotonic())
    return token


def _valid_session_token(token, client: str) -> bool:
    if not token:
        return False
    entry = _session_tokens.get(token)
    if entry is None:
        return False
    issued_client, issued_at = entry
    return issued_client == client and time.monotonic() - issued_at < SESSION_TOKEN_TTL


# calculate_namkha (skyfield astronomy) result cache. The typical flow submits the
# same form twice -- /calculate for the preview, then /download.pdf for the file --
# and without this both runs redo the astronomy from scratch. The Typst compile
# itself still runs twice (SVG vs. PDF are different output formats, nothing to
# share there); this only saves the calculation in between.
# `nc.Subject` is a frozen dataclass (hashable) carrying every input that shapes
# the result and its notes -- birth_timezone (None = derived) and on_summer_time
# included -- so the subject itself keys the cache.
RESULT_CACHE_MAXSIZE = 256
_result_cache: OrderedDict[tuple, nc.NamkhaCalculationResult] = OrderedDict()
_result_cache_lock = threading.Lock()


def _result_cache_key(namkha_request: NamkhaRequest) -> tuple:
    return (
        namkha_request.subject,
        namkha_request.namkha_type,
        namkha_request.method,
    )


def _cached_calculate_namkha(
    namkha_request: NamkhaRequest,
) -> nc.NamkhaCalculationResult:
    key = _result_cache_key(namkha_request)
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


def _result_response(
    request: Request, form, error: str | None = None, svg: str | None = None
) -> HTMLResponse:
    """Single context shape for _result.html, used by both the success and
    error swaps so the template never sees a partial context."""
    return templates.TemplateResponse(
        request, "_result.html", {"error": error, "svg": svg, "form": form}
    )


@app.post("/calculate", response_class=HTMLResponse)
async def calculate(request: Request):
    client = _client_ip(request)
    if _compile_rate_limited(client):
        logger.warning("compile rate limit exceeded for %s on /calculate", client)
        raise HTTPException(status_code=429, detail="Too many requests")

    form = await request.form()
    if not _valid_session_token(form.get("session_token"), client):
        raise HTTPException(
            status_code=403, detail="Session expired; reload the page and try again."
        )

    try:
        namkha_request = build_request(form)
    except ValueError as exc:
        # build_request only raises ValueErrors with user-facing messages.
        return _result_response(request, form, error=str(exc))

    try:
        async with _compile_slot("/calculate"):
            result = await run_in_threadpool(_cached_calculate_namkha, namkha_request)
            svg = await run_in_threadpool(render_svg, result)
    except ValueError as exc:
        return _result_response(
            request, form, error=_userfriendly_calculation_error(exc)
        )

    return _result_response(request, form, svg=svg)


@app.post("/download.pdf")
async def download_pdf(request: Request):
    client = _client_ip(request)
    if _compile_rate_limited(client):
        logger.warning("compile rate limit exceeded for %s on /download.pdf", client)
        raise HTTPException(status_code=429, detail="Too many requests")

    form = await request.form()
    if not _valid_session_token(form.get("session_token"), client):
        raise HTTPException(
            status_code=403, detail="Session expired; reload the page and try again."
        )

    try:
        namkha_request = build_request(form)
    except ValueError as exc:
        # build_request only raises ValueErrors with user-facing messages.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        async with _compile_slot("/download.pdf"):
            result = await run_in_threadpool(_cached_calculate_namkha, namkha_request)
            pdf = await run_in_threadpool(render_pdf, result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=_userfriendly_calculation_error(exc)
        ) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="namkha.pdf"'},
    )


@app.get("/timezone")
async def timezone_lookup(
    request: Request,
    # Declarative bounds: FastAPI rejects out-of-range / non-numeric / inf / nan
    # before the body runs, returning a structured 422 naming the bad field.
    latitude: float = Query(ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
):
    """IANA time zone for a coordinate; used by the form to auto-fill the zone."""
    client = _client_ip(request)
    if _timezone_rate_limited(client):
        raise HTTPException(status_code=429, detail="Too many requests")
    # Round to ~110m: finer than any time zone boundary, lifts the cache hit rate.
    # Boundary search is CPU-bound; offload so it doesn't block the event loop.
    zone = await run_in_threadpool(
        _cached_timezone, round(latitude, 3), round(longitude, 3)
    )
    return {"timezone": zone or "UTC"}


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
