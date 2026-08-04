"""Carrying a ResolvedTimezone through the form.

The time zone is settled once, while the user is entering birth details, and
has to survive the trip back to /calculate and again to /download.pdf. It
travels as one hidden field holding the text below, so the two submit paths
cannot disagree about it and a changed birthplace cannot go unnoticed.

The text is readable and unsigned on purpose: it can be read in devtools or
pasted into a bug report. It guards against the form and the calculation
drifting apart, not against a user editing it.
"""

import datetime as dt

import namkha_calculator as nc

FIELD_NAME = "resolved_timezone"

# Bumped whenever the field list changes, so a value left in a reopened tab is
# refused rather than read as something it is not.
_VERSION = "v1"

_SEPARATOR = "|"
_ABSENT = ""
_UNSET_TRI_STATE = "_"

# Field order after the version marker. Positional, so inserting or reordering
# anything here needs a new _VERSION.
_FIELDS = (
    "provenance",
    "key",
    "offset_seconds",
    "derivation",
    "is_longitude_based",
    "on_summer_time",
    "for_latitude",
    "for_longitude",
    "for_birth_date",
    "modern_zone_key",
    "gregorian_adoption_date",
)


# The serialized time zone is malformed, or of a version we no longer read.
class ResolvedTimezoneParseError(ValueError): ...


def _write_tri_state(value: bool | None) -> str:
    if value is None:
        return _UNSET_TRI_STATE
    return "1" if value else "0"


def _read_tri_state(text: str) -> bool | None:
    if text == _UNSET_TRI_STATE:
        return None
    if text in ("0", "1"):
        return text == "1"
    raise ValueError(f"expected a tri-state, got {text!r}")


def serialize_resolved_timezone(resolved_timezone: nc.ResolvedTimezone) -> str:
    """The resolved time zone as one line of text for a hidden form field."""
    return _SEPARATOR.join(
        (
            _VERSION,
            resolved_timezone.provenance.name,
            resolved_timezone.key or _ABSENT,
            _ABSENT
            if resolved_timezone.offset_seconds is None
            else str(resolved_timezone.offset_seconds),
            resolved_timezone.derivation.name,
            "1" if resolved_timezone.is_longitude_based else "0",
            _write_tri_state(resolved_timezone.on_summer_time),
            repr(resolved_timezone.for_latitude),
            repr(resolved_timezone.for_longitude),
            resolved_timezone.for_birth_date.isoformat(),
            resolved_timezone.modern_zone_key or _ABSENT,
            resolved_timezone.gregorian_adoption_date.isoformat(),
        )
    )


def parse_resolved_timezone(field_value: str) -> nc.ResolvedTimezone:
    """Read back what serialize_resolved_timezone wrote.

    Anything unrecognized raises ResolvedTimezoneParseError, so a stale or
    edited value is refused rather than half-understood.
    """
    parts = field_value.split(_SEPARATOR)
    if len(parts) != len(_FIELDS) + 1:
        raise ResolvedTimezoneParseError(
            f"expected {len(_FIELDS) + 1} fields, got {len(parts)}"
        )
    version, *values = parts
    if version != _VERSION:
        raise ResolvedTimezoneParseError(f"unknown version {version!r}")
    field = dict(zip(_FIELDS, values, strict=True))

    try:
        return nc.ResolvedTimezone(
            key=field["key"] or None,
            offset_seconds=(
                None
                if field["offset_seconds"] == _ABSENT
                else int(field["offset_seconds"])
            ),
            provenance=nc.TimezoneProvenance[field["provenance"]],
            derivation=nc.TimezoneDerivation[field["derivation"]],
            is_longitude_based=field["is_longitude_based"] == "1",
            on_summer_time=_read_tri_state(field["on_summer_time"]),
            for_latitude=float(field["for_latitude"]),
            for_longitude=float(field["for_longitude"]),
            for_birth_date=dt.date.fromisoformat(field["for_birth_date"]),
            modern_zone_key=field["modern_zone_key"] or None,
            gregorian_adoption_date=dt.date.fromisoformat(
                field["gregorian_adoption_date"]
            ),
        )
    except (KeyError, ValueError) as error:
        raise ResolvedTimezoneParseError(str(error)) from error
