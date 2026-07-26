

# Namkha Calculator web app
<img src="app/static/img/favicon.svg" alt="Namkha Calculator logo" width="200" height="200">

---
Web app for calculating a [Namkha thread-cross](https://en.wikipedia.org/wiki/Namkha)
color scheme in the tradition of [Chögyal Namkhai Norbu Rinpoche](https://en.wikipedia.org/wiki/Namkhai_Norbu).
Enter the date, time, and place of birth; the app computes the harmonization of the
elements and renders a sheet with the color table and a thread-cross illustration,
shown in the browser and downloadable as a printable PDF.

All calculations are done by the
[namkha-calculator](https://github.com/NamkhaEncyclopedia/namkha-calculator)
library.

## Table of contents

- [Development status](#development-status)
- [Features](#features)
- [Stack](#stack)
- [Running the app](#running-the-app)
- [Deployment](#deployment)
- [Testing](#testing)
- [Development](#development)
- [License](#license)

## Development status

> [!WARNING]
> The project is in an alpha stage – all calculations should be checked manually when
> making a real Namkha.

## Features

- **User-friendly interface** – a simple form for entering the subject's birth data (with autocomplete, help tips and formatting). The result
  shown on the page is easy to view and download.
- **Time zone handling** – by default the time zone is derived from the birth place
  and date by the library, including historical rules for older births. It can also
  be picked from the IANA zone list or given as a plain UTC offset. A summer time
  (DST) question covers the ambiguous fall-back hour.
- **Calculation sheet** – one document holds the subject data, the color table for
  all eight aspects (with the full harmonization sequence for each), any calculation
  notes in plain language, and the illustration – a flat, colored
  depiction of the made Namkha.
- **Mobile friendly** – the form and the sheet adapt to small screens.

## Stack

Server-side Python, server-rendered HTML.

- **Backend** – [FastAPI](https://fastapi.tiangolo.com/) with Uvicorn and Jinja2
  templates. The `namkha-calculator` library does all the astrology and bundles its
  own ephemeris and time zone data, so results do not depend on the host system.
- **Sheet rendering** – [Typst](https://typst.app/) (via the `typst` Python package)
  compiles one document to both PDF and SVG. The thread-cross illustration is a
  hand-drawn SVG template that the code fills in per request – coloring regions and
  writing labels by element id – and Typst embeds it in the sheet.
- **Frontend** – [HTMX](https://htmx.org/) swaps the calculation result into the
  page; [Alpine.js](https://alpinejs.dev/) drives the form behavior (field gating,
  autocomplete, time zone modes); plain CSS. Place autocomplete is powered by
  [Komoot Photon](https://photon.komoot.io/), and the form is gated by
  [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/). All
  scripts are vendored except Turnstile's, which must come from Cloudflare –
  no Node, no bundler.

## Running the app

Requires Python 3.13+ and [Poetry](https://python-poetry.org/).

Two things are required and the app refuses to start without them: a salt for the
birth-name hash in the log, and a [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/)
secret for the bot check on the form. For local development, use Cloudflare's
[test keys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/) –
they always pass and work on any host name:

```sh
export NAMKHA_LOG_SALT=some-random-string
export TURNSTILE_SITEKEY=1x00000000000000000000AA
export TURNSTILE_SECRET=1x0000000000000000000000000000000AA
poetry install
poetry run uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/>.

## Deployment

The app ships as a Docker container. The image installs only the main dependency group
into a virtualenv in a builder stage, copies that venv into a slim runtime image,
and runs as a non-root user on port 8080:

```sh
docker build -t namkha-webapp .
docker run --rm -p 8080:8080 \
  -e NAMKHA_LOG_SALT=some-random-string \
  -e TURNSTILE_SECRET=your-cloudflare-secret \
  namkha-webapp
```

`TURNSTILE_SECRET` is the only confidential value and never belongs in the
repository; the site key and the accepted host names are public.

Required, the app refuses to start without them:

- `NAMKHA_LOG_SALT` – salt for the birth-name hash in the log.
- `TURNSTILE_SECRET` – secret used to verify the widget token server-side.

Optional:

- `TURNSTILE_SITEKEY` – public widget key rendered into the form. Defaults to the
  deployed one (`app/constants.py`).
- `TURNSTILE_HOSTNAMES` – comma-separated host names a token may be issued for.
  Defaults to the deployed ones (`app/turnstile.py`).
- `NAMKHA_TRUSTED_PROXIES` – comma-separated IPs or CIDRs of reverse proxies
  allowed to set `X-Forwarded-For`. Empty by default, meaning no proxy is trusted
  and the header is ignored.
- `LOG_LEVEL` – level for the application logger, `INFO` by default.
- `NAMKHA_TEST_MODE` – `1` adds the sample-data picker to the form. Leave it unset
  in production; the routes are not registered at all without it.

The calculation and time zone endpoints are rate limited per client, and Typst
compiles run behind a concurrency cap. The container already starts uvicorn with
`--proxy-headers`, but the limiter only trusts `X-Forwarded-For` from proxies
named in `NAMKHA_TRUSTED_PROXIES` – set it when running behind one, otherwise
every request looks like it comes from the proxy and all visitors share a single
bucket. The limiter state lives in the process, so running several workers
multiplies the effective limit.

## Testing

Two layers: a backend suite that runs on its own, and an opt-in browser suite.

```sh
poetry run pytest                        # backend + render tests, browser auto-skipped
poetry install --with browser            # add the browser test layer
poetry run playwright install chromium   # one-time browser download
poetry run pytest tests/browser          # Playwright end-to-end tests
```

Setting the `NAMKHA_TEST_MODE=1` environment variable adds a sample-data picker to the form, useful for manual testing.

## Development

Pre-commit hooks cover Python linting and formatting (ruff), type checking (mypy),
templates (djlint, Jinja-aware), CSS and JS (prettier), the Dockerfile (hadolint),
Typst formatting (typstyle), file hygiene, and conventional commit messages:

```sh
poetry run pre-commit install
poetry run pre-commit install --hook-type commit-msg
poetry run pre-commit run --all-files
```

Note: the illustration SVG is maintained as code – automatic SVG optimizers are
deliberately not used, because the render code depends on element ids that they
would strip.

## License

Licensed under the GNU General Public License, version 3 or later
(GPL-3.0-or-later). See [LICENSE](LICENSE).

This project depends on the `namkha-calculator` library, which is licensed under
the GPL-3.0-or-later; distributing this app therefore requires GPL-compatible
terms.

Copyright 2026 Namkha Encyclopedia.
