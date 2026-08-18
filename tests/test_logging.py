"""Calculation/error logging.

Pure functions asserted with synthetic inputs (the repo convention): the name is
hashed at a single choke-point and the result summary is astro-only, so the guard
that matters -- the plaintext birth name never reaching a log -- is checked on the
serialized payload. One interface-drift guard pushes a real `calculate_namkha`
result through `summarize_result` so the summary can't silently diverge from the
library shape.
"""

import json
import logging
from logging.handlers import QueueHandler

import namkha_calculator as nc
import pytest

from app.event_log import (
    build_log_payload,
    configure_logging,
    hash_name,
    summarize_result,
)
from app.forms import FIELDS, build_request
from app.timezone_tickets import FIELD_NAME as TIMEZONE_TICKET_FIELD
from tests.support import timezone_ticket_field

_PLAINTEXT_NAME = "Tenzin Norgay"


def _form(**overrides):
    """A form mapping keyed like app.forms.FIELDS, with a real name by default."""
    base = {
        "name": _PLAINTEXT_NAME,
        "gender": "MALE",
        "birth_date": "1985-03-15",
        "birth_time": "14:30",
        "location_name": "Berlin",
        "timezone": "Europe/Berlin",
        "utc_offset": "",
        "on_summer_time": "",
        "latitude": "52.52",
        "longitude": "13.40",
        "namkha_type": "YEAR",
        "method": "CLASSIC",
    }
    base.update(overrides)
    return base


# --- hash_name --------------------------------------------------------------------


def test_hash_name_is_stable_and_not_the_value():
    hashed = hash_name(_PLAINTEXT_NAME)
    assert hashed == hash_name(_PLAINTEXT_NAME)  # stable across calls
    assert hashed != _PLAINTEXT_NAME
    assert _PLAINTEXT_NAME not in hashed
    assert len(hashed) == 12


def test_hash_name_blank_or_none_is_sentinel():
    assert hash_name(None) == "-"
    assert hash_name("") == "-"
    assert hash_name("   ") == "-"


def test_hash_name_ignores_surrounding_whitespace():
    assert hash_name(f"  {_PLAINTEXT_NAME}  ") == hash_name(_PLAINTEXT_NAME)


def test_hash_name_changes_with_salt(monkeypatch):
    monkeypatch.setattr("app.event_log._NAME_HASH_SALT", "pepper")
    salted = hash_name(_PLAINTEXT_NAME)
    monkeypatch.setattr("app.event_log._NAME_HASH_SALT", "")
    assert salted != hash_name(_PLAINTEXT_NAME)


# --- build_log_payload / summarize_result (privacy guard) -------------------------


def test_payload_never_contains_plaintext_name(make_result, make_request):
    # The result's subject carries the real name; the payload must not leak it.
    request = make_request(name=_PLAINTEXT_NAME)
    result = make_result(request=request)
    payload = build_log_payload("/calculate", _form(), outcome="ok", result=result)
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    assert _PLAINTEXT_NAME not in serialized
    assert payload["name"] == hash_name(_PLAINTEXT_NAME)
    assert "name" not in payload["inputs"]  # the raw field is never carried through


def test_payload_carries_the_non_sensitive_inputs():
    payload = build_log_payload("/calculate", _form(), outcome="reject", error="bad")
    inputs = payload["inputs"]
    for field in FIELDS:
        if field in ("name", TIMEZONE_TICKET_FIELD):
            continue
        assert field in inputs
    assert inputs["birth_date"] == "1985-03-15"
    assert inputs["latitude"] == "52.52"
    assert inputs["location_name"] == "Berlin"
    assert TIMEZONE_TICKET_FIELD not in inputs
    assert inputs["resolved_timezone"] is None
    assert payload["outcome"] == "reject"
    assert payload["error"] == "bad"
    assert payload["result"] is None


def test_payload_records_the_zone_a_ticket_stands_for():
    """A ticket means nothing in a log. This is the only place the record says
    which zone the sheet used: the other fields give the user's choice, which is
    blank whenever the zone came from the birth place."""
    form = _form()
    form[TIMEZONE_TICKET_FIELD] = timezone_ticket_field(form)
    payload = build_log_payload("/calculate", form, outcome="ok")
    zone = payload["inputs"]["resolved_timezone"]
    assert set(zone) == {"key", "offset_seconds", "provenance", "derivation"}
    assert zone["key"] == "Europe/Berlin"
    assert zone["provenance"] == "USER_ZONE"


def test_download_logs_the_inputs_that_produced_the_sheet(
    client, fixture_form, download_form, caplog
):
    """/download.pdf reads no form, so its inputs come from the stored handle.
    Without them the download's log line would be blank where /calculate's is
    full, and the two could no longer be matched."""
    form = fixture_form("year_classic_berlin")
    form["name"] = _PLAINTEXT_NAME
    payload = download_form(form)
    with caplog.at_level(logging.INFO, logger="app.main"):
        assert client.post("/download.pdf", data=payload).status_code == 200
    payloads = []
    for record in caplog.records:
        try:
            payloads.append(json.loads(record.getMessage()))
        except ValueError:
            continue  # only log_event writes JSON; other lines are plain text
    logged = [payload for payload in payloads if payload["route"] == "/download.pdf"]
    assert len(logged) == 1
    assert logged[0]["inputs"]["birth_date"] == form["birth_date"]
    assert logged[0]["inputs"]["latitude"] == form["latitude"]
    assert logged[0]["name"] == hash_name(_PLAINTEXT_NAME)


def test_summarize_result_is_astro_only(make_result):
    summary = summarize_result(make_result())
    assert set(summary) == {
        "birth_element",
        "birth_animal",
        "birth_mewa",
        "namkha_type",
        "method",
        "aspects",
        "notes",
    }
    assert "subject" not in summary
    assert len(summary["aspects"]) == 8


# --- interface-drift guard (real library result) ----------------------------------


def test_summarize_real_result(fixture_form):
    request = build_request(fixture_form("year_classic_berlin"))
    result = nc.calculate_namkha(request.namkha_type, request.subject, request.method)
    summary = summarize_result(result)
    # Structure + validity only, never specific astrological values.
    assert len(summary["aspects"]) == 8
    for aspect in summary["aspects"]:
        assert aspect["center"]  # a center element was resolved
        assert isinstance(aspect["sequence"], list) and aspect["sequence"]
    json.dumps(summary)  # fully JSON-serializable


# --- async wiring -----------------------------------------------------------------


def test_configure_logging_requires_salt(monkeypatch):
    monkeypatch.delenv("NAMKHA_LOG_SALT", raising=False)
    with pytest.raises(RuntimeError, match="NAMKHA_LOG_SALT"):
        configure_logging()


def test_configure_logging_wires_a_queue_handler():
    app_logger = logging.getLogger("app")
    # Snapshot the process-global logger state and restore it after, so this test
    # can't leak a stopped listener's handler or propagate=False into other tests.
    saved_handlers = list(app_logger.handlers)
    saved_propagate = app_logger.propagate
    saved_level = app_logger.level
    listener = configure_logging()
    try:
        queue_handlers = [h for h in app_logger.handlers if isinstance(h, QueueHandler)]
        # Exactly one QueueHandler (idempotent across calls), and it is the only
        # handler on the app logger -- no blocking StreamHandler in the hot path.
        assert len(queue_handlers) == 1
        assert app_logger.handlers == queue_handlers
        assert app_logger.propagate is False
    finally:
        listener.stop()
        app_logger.handlers = saved_handlers
        app_logger.propagate = saved_propagate
        app_logger.setLevel(saved_level)
