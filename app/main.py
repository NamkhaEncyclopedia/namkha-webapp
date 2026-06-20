"""FastAPI app: form -> calculate_namkha -> (Typst sheet <- inline SVG) -> PDF."""

import time
from functools import lru_cache
from pathlib import Path

import namkha_calculator as nc
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
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


def _timezone_rate_limited(client: str) -> bool:
    """Fixed-window per-client limiter. In-memory, so per-worker: running
    uvicorn with N workers multiplies the effective limit by N."""
    now = time.monotonic()
    if len(_timezone_hits) > 1024:  # sweep stale buckets so unique IPs can't leak
        for key, hits in list(_timezone_hits.items()):
            if all(now - t >= TIMEZONE_RATE_WINDOW for t in hits):
                del _timezone_hits[key]
    window = _timezone_hits.get(client, [])
    recent = [t for t in window if now - t < TIMEZONE_RATE_WINDOW]
    recent.append(now)
    _timezone_hits[client] = recent
    return len(recent) > TIMEZONE_RATE_LIMIT


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
        },
    )


@app.post("/calculate", response_class=HTMLResponse)
async def calculate(request: Request):
    form = await request.form()
    try:
        namkha_request = build_request(form)
        result = nc.calculate_namkha(
            namkha_request.namkha_type, namkha_request.subject, namkha_request.method
        )
        svg = await run_in_threadpool(render_svg, result, namkha_request)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "_result.html", {"error": str(exc), "svg": None, "form": form}
        )
    return templates.TemplateResponse(
        request, "_result.html", {"error": None, "svg": svg, "form": form}
    )


@app.post("/download.pdf")
async def download_pdf(request: Request):
    form = await request.form()
    namkha_request = build_request(form)
    result = nc.calculate_namkha(
        namkha_request.namkha_type, namkha_request.subject, namkha_request.method
    )
    pdf = await run_in_threadpool(render_pdf, result, namkha_request)
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


# TODO Remove before release
@app.get("/preview", response_class=HTMLResponse)
async def preview(request: Request):
    """Editing loop: render the full sheet with sample data; meta-refresh picks
    up edits to sheet.typ / illustration.svg / render code."""
    result = nc.calculate_namkha(
        constants.SAMPLE_NAMKHA_TYPE, constants.SAMPLE_SUBJECT, constants.SAMPLE_METHOD
    )

    class _NamkhaRequest:
        subject = constants.SAMPLE_SUBJECT
        namkha_type = constants.SAMPLE_NAMKHA_TYPE
        method = constants.SAMPLE_METHOD

    svg = await run_in_threadpool(render_svg, result, _NamkhaRequest())
    return templates.TemplateResponse(request, "preview.html", {"svg": svg})
