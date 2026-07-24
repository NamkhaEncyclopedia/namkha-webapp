

# Namkha Calculator web app
<img src="app/static/img/favicon.svg" alt="Namkha Calculator logo" width="200" height="200">

---
Web app for calculating a [Namkha thread-cross](https://en.wikipedia.org/wiki/Namkha)
colour scheme in the tradition of [Chögyal Namkhai Norbu Rinpoche](https://en.wikipedia.org/wiki/Namkhai_Norbu).
Enter the date, time, and place of birth; the app computes the harmonization of the
elements and renders a sheet with the colour table and a thread-cross illustration,
shown in the browser and downloadable as a printable PDF.

All calculations are done by the
[namkha-calculator](https://github.com/NamkhaEncyclopedia/namkha-calculator)
library.

## Table of contents

- [Development status](#development-status)
- [Features](#features)
- [Stack](#how-it-is-built)
- [Running the app](#running-the-app)
- [Testing](#testing)
- [Development](#development)
- [Licence](#licence)

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

## Stack

Server-side Python, server-rendered HTML.

- **Backend** – [FastAPI](https://fastapi.tiangolo.com/) with Uvicorn and Jinja2
  templates. The `namkha-calculator` library does all the astrology and bundles its
  own ephemeris and time zone data, so results do not depend on the host system.
- **Sheet rendering** – [Typst](https://typst.app/) (via the `typst` Python package)
  compiles one document to both PDF and SVG. The thread-cross illustration is a
  hand-drawn SVG template that the code fills in per request – colouring regions and
  writing labels by element id – and Typst embeds it in the sheet.
- **Frontend** – [HTMX](https://htmx.org/) swaps the calculation result into the
  page; [Alpine.js](https://alpinejs.dev/) drives the form behavior (field gating,
  autocomplete, time zone modes); plain CSS. Place autocomplete is powered by
  [Komoot Photon](https://photon.komoot.io/). All scripts are vendored –
  no Node, no bundler.

## Running the app

Requires Python 3.13+ and [Poetry](https://python-poetry.org/).

The app hashes the birth name for logging, so a salt is required – it will refuse
to start without one:

```sh
export NAMKHA_LOG_SALT=some-random-string
poetry install
poetry run uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/>.


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

Pre-commit hooks cover linting and formatting (ruff), type checking (mypy), Typst
formatting (typstyle), file hygiene, and conventional commit messages:

```sh
poetry run pre-commit install
poetry run pre-commit install --hook-type commit-msg
poetry run pre-commit run --all-files
```

Note: the illustration SVG is maintained as code – automatic SVG optimizers are
deliberately not used, because the render code depends on element ids that they
would strip.

## Licence

Licensed under the GNU General Public License, version 3 or later
(GPL-3.0-or-later). See [LICENSE](LICENSE).

This project depends on the `namkha-calculator` library, which is licensed under
the GPL-3.0-or-later; distributing this app therefore requires GPL-compatible
terms.

Copyright 2026 Namkha Encyclopedia.
