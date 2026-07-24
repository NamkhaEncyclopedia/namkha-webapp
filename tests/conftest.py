"""Shared test fixtures.

Two input styles, matching the plan's layer split:
- `fixture_form` loads the canonical JSON forms for real-library / route tests.
- The `make_*` builders construct the *real* library dataclasses with controlled
  field values, so pure-logic tests can reach cases (deep-water, the LIFE weave).
"""

import json
from datetime import datetime
from pathlib import Path

import namkha_calculator as nc
import pytest
from starlette.testclient import TestClient

from app import main
from app.forms import NamkhaRequest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _log_salt(monkeypatch):
    """configure_logging() refuses to boot without NAMKHA_LOG_SALT; set one for
    every test so the app lifespan (TestClient) and direct calls both pass."""
    monkeypatch.setenv("NAMKHA_LOG_SALT", "test-salt")


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fixture_form():
    """Load tests/fixtures/<name>.json into a dict. Fixtures carry a `location_name`
    label (the Photon place name); build_request reads named keys only, so the dict
    passes straight through untouched. Also mints a valid session_token (see
    main._issue_session_token), since /calculate and /download.pdf now require one
    -- routes calling this fixture are testing calculation behavior, not the gate
    itself, so a real token keeps that gate out of their way. Tokens are bound to
    the issuing client's IP; TestClient's direct peer is always "testclient"."""

    def load(name):
        data = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
        data["session_token"] = main._issue_session_token("testclient")
        return data

    return load


# --- Synthetic builders: real library dataclasses with controlled values ----------

# A plausible default harmonization sequence (length stays variable across tests).
_DEFAULT_SEQUENCE = (
    nc.Element.WOOD,
    nc.Element.FIRE,
    nc.Element.EARTH,
    nc.Element.METAL,
)

# Default center element per aspect. LIFE carries is_conflicted=None like the library.
_DEFAULT_CENTERS = {
    nc.Aspect.LIFE: nc.Element.EARTH,
    nc.Aspect.BODY: nc.Element.METAL,
    nc.Aspect.CAPACITY: nc.Element.WOOD,
    nc.Aspect.FORTUNE: nc.Element.WATER,
    nc.Aspect.MEWA_LIFE: nc.Element.WATER,
    nc.Aspect.MEWA_BODY: nc.Element.METAL,
    nc.Aspect.MEWA_CAPACITY: nc.Element.FIRE,
    nc.Aspect.MEWA_FORTUNE: nc.Element.METAL,
}

_DEFAULT_MEWA_NUMBERS = {
    nc.Aspect.MEWA_LIFE: 1,
    nc.Aspect.MEWA_BODY: 6,
    nc.Aspect.MEWA_CAPACITY: 9,
    nc.Aspect.MEWA_FORTUNE: 8,
}


def _make_aspect(
    name, center, sequence=_DEFAULT_SEQUENCE, is_conflicted: bool | None = False
):
    return nc.HarmonizedAspect(
        name=name,
        center=center,
        harmonization_seq=tuple(sequence),
        is_conflicted=is_conflicted,
    )


def _default_aspects(overrides=None):
    """All 8 aspects with default centers; `overrides` maps Aspect -> HarmonizedAspect
    to replace individual rhombi (e.g. a deep-water case)."""
    overrides = overrides or {}
    aspects = []
    for aspect, center in _DEFAULT_CENTERS.items():
        if aspect in overrides:
            aspects.append(overrides[aspect])
            continue
        is_conflicted = None if aspect == nc.Aspect.LIFE else False
        aspects.append(_make_aspect(aspect, center, is_conflicted=is_conflicted))
    return tuple(aspects)


def _make_request(
    gender=nc.Gender.MALE,
    name="Test Person",
    birth_datetime=datetime(1985, 3, 15, 14, 30),
    timezone="Europe/Berlin",
    latitude=52.52,
    longitude=13.40,
    location_name=None,
    namkha_type=nc.NamkhaType.YEAR,
    method=nc.CalculationMethod.CLASSIC,
):
    subject = nc.Subject(
        name=name,
        gender=gender,
        birth_datetime=birth_datetime,
        birth_timezone=nc.zone(timezone),
        birth_location=nc.Location(
            latitude=latitude, longitude=longitude, name=location_name
        ),
    )
    return NamkhaRequest(subject=subject, namkha_type=namkha_type, method=method)


def _make_result(
    request=None,
    aspects=None,
    mewa_numbers=None,
    birth_element=nc.Element.WOOD,
    birth_animal=nc.Animal.OX,
    birth_mewa=6,
    notes=(),
):
    """`request` supplies subject/namkha_type/calculation_method (render reads these
    off the result, not a separate request object); defaults to a plain MALE/CLASSIC
    request when the test doesn't care."""
    request = request or _make_request()
    return nc.NamkhaCalculationResult(
        subject=request.subject,
        calculation_method=request.method,
        namkha_type=request.namkha_type,
        birth_element=birth_element,
        birth_animal=birth_animal,
        birth_mewa=birth_mewa,
        harmonized_aspects=_default_aspects() if aspects is None else tuple(aspects),
        mewa_numbers=dict(
            _DEFAULT_MEWA_NUMBERS if mewa_numbers is None else mewa_numbers
        ),
        calculation_notes=tuple(notes),
    )


@pytest.fixture
def make_aspect():
    return _make_aspect


@pytest.fixture
def default_aspects():
    return _default_aspects


@pytest.fixture
def make_result():
    return _make_result


@pytest.fixture
def make_request():
    return _make_request


@pytest.fixture(autouse=True)
def _reset_timezone_state():
    """The /timezone limiter keeps process-global state; reset it around every test
    so request counts and cached lookups don't leak between tests."""
    main._timezone_hits.clear()
    main._cached_timezone.cache_clear()
    yield
    main._timezone_hits.clear()
    main._cached_timezone.cache_clear()


@pytest.fixture(autouse=True)
def _reset_compile_rate_limit_state():
    """The /calculate and /download.pdf limiter shares the same kind of
    process-global state as /timezone; reset it around every test."""
    main._compile_hits.clear()
    yield
    main._compile_hits.clear()


@pytest.fixture(autouse=True)
def _reset_result_cache():
    """The calculate_namkha result cache shared by /calculate and /download.pdf
    is also process-global; reset it around every test."""
    main._result_cache.clear()
    yield
    main._result_cache.clear()


@pytest.fixture(autouse=True)
def _reset_session_tokens():
    """Tokens minted by GET / (see main._issue_session_token) are also
    process-global state; reset them around every test."""
    main._session_tokens.clear()
    yield
    main._session_tokens.clear()
