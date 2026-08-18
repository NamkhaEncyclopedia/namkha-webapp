"""The resolved time zone stays on the server; the form carries a ticket for it.

GET /timezone works the zone out and keeps it here under a random ticket. The
form submits the ticket alone, and /calculate reads the zone back out.

The store lives in this process only, so the app must run as a single worker.
Adding workers would send a form to a process that never issued its ticket.

This is its own module because main.py imports forms.py, and both of them need
to reach the store.
"""

import secrets
import time
from collections import OrderedDict

import namkha_calculator as nc

from app.constants import SESSION_TTL

FIELD_NAME = "timezone_ticket"

# Hard cap on the store. A ticket expires, but expiry does not limit how many
# unexpired ones pile up. At the cap the oldest live ticket expires, which asks
# whoever held it to resolve again.
# Larger than main.MAX_RESULT_HANDLES because tickets are issued far more often:
# /timezone allows 120 requests per minute, so one client holds at most 120 * 30 =
# 3600 live tickets over one SESSION_TTL. The cap is well above that, so no
# single client can push other visitors out on its own.
MAX_TICKETS = 16384

# ticket -> (zone, issue time), oldest first. Insertion order is issue order and
# must stay that way: issue_ticket reads the front as the oldest ticket.
_tickets: OrderedDict[str, tuple[nc.ResolvedTimezone, float]] = OrderedDict()


def _oldest_issued_at() -> float:
    _, issued_at = next(iter(_tickets.values()))
    return issued_at


def issue_ticket(resolved_timezone: nc.ResolvedTimezone) -> str:
    """Keep a resolved time zone and return the ticket the form carries for it."""
    now = time.monotonic()
    # Expired tickets are the oldest ones, so they sit at the front:
    # drop until the front is live again.
    while _tickets and now - _oldest_issued_at() >= SESSION_TTL:
        _tickets.popitem(last=False)
    while len(_tickets) >= MAX_TICKETS:
        _tickets.popitem(last=False)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (resolved_timezone, now)
    return ticket


def read_ticket(ticket) -> nc.ResolvedTimezone | None:
    """The zone a ticket stands for, or None when the ticket is unknown or expired.

    Reading does not spend the ticket. The form stays on the page after a
    calculation, so the same ticket can be submitted again.
    """
    if not ticket:
        return None
    entry = _tickets.get(ticket)
    if entry is None:
        return None
    resolved_timezone, issued_at = entry
    if time.monotonic() - issued_at >= SESSION_TTL:
        return None
    return resolved_timezone
