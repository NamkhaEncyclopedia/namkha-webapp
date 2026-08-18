"""Non-blocking calculation/error logging with the birth name hashed.

Every calculation attempt on /calculate and /download.pdf is logged: the form
inputs plus either a summary of the library result (success) or the error
(failure). The birth `name` is the one sensitive field -- it is hashed once, in
build_log_payload, and never appears in clear text. The result summary is built
from astro outputs only (it never reads result.subject), so it structurally
cannot carry the name.

Logging is async in the event-loop sense: a QueueHandler on the "app" logger only
enqueues records (queue.put_nowait), and a background QueueListener thread does
the actual stderr write, so logger calls in the routes never block the loop.
"""

import hashlib
import json
import logging
import os
import queue
import sys
from logging.handlers import QueueHandler, QueueListener

from app.forms import FIELDS
from app.timezone_tickets import FIELD_NAME as TIMEZONE_TICKET_FIELD
from app.timezone_tickets import read_ticket

# The one sensitive form field; hashed everywhere, never logged in clear text.
_SENSITIVE_FIELD = "name"

# Optional salt for the name hash: without it, a plain sha256 of a common name is
# open to a dictionary lookup. Read once at import; empty by default.
_NAME_HASH_SALT = os.getenv("NAMKHA_LOG_SALT", "")

# App-wide logger. Route/render loggers ("app.main", "app.calculation_render")
# are children and propagate up to here, where the QueueHandler lives.
_APP_LOGGER_NAME = "app"


def hash_name(raw: str | None) -> str:
    """Short, stable, non-reversible stand-in for the birth name. Same input maps
    to the same hash across runs (so repeat submissions correlate); a blank/absent
    name maps to a sentinel. Never returns the value itself."""
    text = (raw or "").strip()
    if not text:
        return "-"
    return hashlib.sha256((_NAME_HASH_SALT + text).encode("utf-8")).hexdigest()[:12]


def _zone_for_log(ticket) -> dict | None:
    """The time zone a ticket stands for, as a few readable fields.

    The ticket itself means nothing in a log. This is the only place the record
    says which zone was used: the form fields give the user's choice, which is
    blank in automatic mode. None when the server no longer holds the zone.
    """
    resolved_timezone = read_ticket(ticket)
    if resolved_timezone is None:
        return None
    return {
        "key": resolved_timezone.key,
        "offset_seconds": resolved_timezone.offset_seconds,
        "provenance": resolved_timezone.provenance.name,
        "derivation": resolved_timezone.derivation.name,
    }


def summarize_result(result) -> dict:
    """Compact summary of a NamkhaCalculationResult, from astro outputs only.

    Deliberately never touches result.subject: that carries the plaintext name,
    so keeping it out of here makes leaking the name through the result summary
    impossible rather than a rule to remember."""
    aspects = []
    for harmonized_aspect in result.harmonized_aspects:
        aspect = harmonized_aspect.name
        aspects.append(
            {
                "aspect": aspect.name,
                "center": harmonized_aspect.center.value,
                "mewa": result.mewa_numbers.get(aspect),
                "sequence": [
                    element.value for element in harmonized_aspect.harmonization_seq
                ],
                "conflicted": harmonized_aspect.is_conflicted,
            }
        )
    return {
        "birth_element": result.birth_element.value,
        "birth_animal": result.birth_animal.value,
        "birth_mewa": result.birth_mewa,
        "namkha_type": result.namkha_type.name,
        "method": result.calculation_method.name,
        "aspects": aspects,
        "notes": [note.note.name for note in result.calculation_notes],
    }


def build_log_payload(
    route: str,
    form,
    *,
    outcome: str,
    result=None,
    error: str | None = None,
) -> dict:
    """Assemble one log record: route, outcome, hashed name, the remaining form
    inputs verbatim, the resolved time zone, and either a result summary or an
    error string. This is the single place the name is read and hashed, so `name`
    never reaches a log raw."""
    inputs = {
        field: form.get(field)
        for field in FIELDS
        if field not in (_SENSITIVE_FIELD, TIMEZONE_TICKET_FIELD)
    }
    # The ticket is left out and the zone it stands for takes its place.
    inputs["resolved_timezone"] = _zone_for_log(form.get(TIMEZONE_TICKET_FIELD))
    return {
        "route": route,
        "outcome": outcome,
        "name": hash_name(form.get(_SENSITIVE_FIELD)),
        "inputs": inputs,
        "result": summarize_result(result) if result is not None else None,
        "error": error,
    }


def log_event(
    logger: logging.Logger,
    route: str,
    form,
    *,
    outcome: str,
    result=None,
    error: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Log one calculation outcome as a JSON payload. JSON keeps the record
    greppable and machine-parseable without a structured-logging dependency;
    `default=str` covers any stray non-JSON value defensively."""
    payload = build_log_payload(
        route, form, outcome=outcome, result=result, error=error
    )
    logger.log(level, "%s", json.dumps(payload, ensure_ascii=False, default=str))


def configure_logging() -> QueueListener:
    """Wire non-blocking logging on the "app" logger and return the started
    listener (the caller stops it on shutdown).

    The QueueHandler is the only handler on the app logger, so logger calls just
    enqueue; the real StreamHandler(sys.stderr) lives on the listener's thread.
    propagate=False keeps these records from also going through uvicorn's root
    handlers (which would double-log). Idempotent, so uvicorn --reload doesn't
    stack duplicate handlers.

    Every calculation is logged with the birth name hashed (see hash_name), so
    an unsalted hash would be a dictionary-lookup risk for any name in common
    use; refuse to boot rather than silently log unsalted hashes. Read live
    (not the module-level _NAME_HASH_SALT) so this can't be satisfied by an
    env var set after import."""
    if not os.getenv("NAMKHA_LOG_SALT"):
        raise RuntimeError(
            "NAMKHA_LOG_SALT must be set: calculation logging hashes the birth "
            "name, and an unsalted hash is not safe to log."
        )

    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    for handler in list(app_logger.handlers):
        if isinstance(handler, QueueHandler):
            app_logger.removeHandler(handler)

    log_queue: queue.Queue = queue.Queue(-1)
    app_logger.addHandler(QueueHandler(log_queue))
    app_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    app_logger.propagate = False

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    listener = QueueListener(log_queue, stream_handler, respect_handler_level=True)
    listener.start()
    return listener
