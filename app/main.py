"""FastAPI app: form -> calculate_namkha -> (Typst sheet <- inline SVG) -> PDF."""

from pathlib import Path

import namkha_calculator as nc
from fastapi import FastAPI, Request
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
