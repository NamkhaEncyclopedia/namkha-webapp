"""FastAPI app: form -> calculate_namkha -> (Typst sheet <- inline SVG) -> PDF."""

import asyncio
import json
import os
import time
from collections import OrderedDict
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
from app.forms import FIELDS, build_request

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
_timezone_hits: dict[str, list[float]] = {}


@lru_cache(maxsize=4096)
def _cached_timezone(latitude: float, longitude: float) -> str | None:
    """Boundary lookup keyed on rounded coordinates (see timezone_lookup).
    Returns None if the finder can't be built or the lookup fails, so the
    endpoint falls back to UTC instead of erroring."""
    try:
        return _timezone_finder().timezone_at(lat=latitude, lng=longitude)
    except Exception:
        return None


def _rate_limited(
    hits: dict[str, list[float]], client: str, limit: int, window: float
) -> bool:
    """Fixed-window per-client limiter. In-memory, so per-worker: running
    uvicorn with N workers multiplies the effective limit by N."""
    now = time.monotonic()
    if len(hits) > 1024:  # sweep stale buckets so unique IPs can't leak
        for key, recent_hits in list(hits.items()):
            if all(now - t >= window for t in recent_hits):
                del hits[key]
    recent = [t for t in hits.get(client, []) if now - t < window]
    recent.append(now)
    hits[client] = recent
    return len(recent) > limit


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
_compile_hits: dict[str, list[float]] = {}
MAX_CONCURRENT_COMPILES = 4
_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILES)


def _compile_rate_limited(client: str) -> bool:
    return _rate_limited(_compile_hits, client, COMPILE_RATE_LIMIT, COMPILE_RATE_WINDOW)


# calculate_namkha (skyfield astronomy) result cache. The typical flow submits the
# same form twice -- /calculate for the preview, then /download.pdf for the file --
# and without this both runs redo the astronomy from scratch. The Typst compile
# itself still runs twice (SVG vs. PDF are different output formats, nothing to
# share there); this only saves the calculation in between.
# `nc.Subject`/`Location` aren't hashable (not frozen dataclasses), so the key is
# built from their primitive fields rather than the objects themselves.
RESULT_CACHE_MAXSIZE = 256
_result_cache: OrderedDict[tuple, nc.NamkhaCalculationResult] = OrderedDict()


def _result_cache_key(namkha_request) -> tuple:
    subject = namkha_request.subject
    location = subject.birth_location
    return (
        subject.name,
        subject.gender,
        subject.birth_datetime,
        str(subject.birth_timezone),
        location.latitude,
        location.longitude,
        location.name,
        namkha_request.namkha_type,
        namkha_request.method,
    )


def _cached_calculate_namkha(namkha_request) -> nc.NamkhaCalculationResult:
    key = _result_cache_key(namkha_request)
    cached = _result_cache.get(key)
    if cached is not None:
        _result_cache.move_to_end(key)
        return cached
    # Raises ValueError on bad input (method/type mismatch, unsupported birth
    # year); nothing is cached in that case since this line never returns.
    result = nc.calculate_namkha(
        namkha_request.namkha_type, namkha_request.subject, namkha_request.method
    )
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
    client = request.client.host if request.client else "unknown"
    if _compile_rate_limited(client):
        raise HTTPException(status_code=429, detail="Too many requests")

    form = await request.form()
    try:
        namkha_request = build_request(form)
    except ValueError as exc:
        # build_request only raises ValueErrors with user-facing messages.
        return _result_response(request, form, error=str(exc))

    try:
        async with _compile_semaphore:
            result = _cached_calculate_namkha(namkha_request)
            svg = await run_in_threadpool(render_svg, result)
    except ValueError as exc:
        return _result_response(
            request, form, error=_userfriendly_calculation_error(exc)
        )

    return _result_response(request, form, svg=svg)


@app.post("/download.pdf")
async def download_pdf(request: Request):
    client = request.client.host if request.client else "unknown"
    if _compile_rate_limited(client):
        raise HTTPException(status_code=429, detail="Too many requests")

    form = await request.form()
    try:
        namkha_request = build_request(form)
    except ValueError as exc:
        # build_request only raises ValueErrors with user-facing messages.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        async with _compile_semaphore:
            result = _cached_calculate_namkha(namkha_request)
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
    # Single process / direct connection assumed: client.host is the real peer.
    # Behind a proxy this would collapse all users into one bucket -- read the
    # first X-Forwarded-For hop instead (only if the proxy is trusted).
    client = request.client.host if request.client else "unknown"
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
