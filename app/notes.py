"""Calculation notes as the reader sees them.

The library's own messages are written for developers: they name internal
settings and use jargon. These are the texts shown instead, on the sheet and
under the form.
"""

import namkha_calculator as nc

NOTE_MESSAGES: dict[nc.CalculationNote, str] = {
    nc.CalculationNote.HIGH_LATITUDE: (
        "The birth place lies far north or south, so standard sunrise and "
        "sunset times were used."
    ),
    nc.CalculationNote.PERIOD_BOUNDARY: (
        "The birth time falls right at the turning point between two periods, "
        "where even a small error could change the result. Please make sure the time "
        "is exact."
    ),
    nc.CalculationNote.AMBIGUOUS_LOCAL_TIME: (
        "On this date the clocks were set back, so this hour happened twice, and "
        "the later one was assumed. If you know whether summer time was in effect "
        "at birth, please specify it."
    ),
    nc.CalculationNote.AMBIGUOUS_LOCAL_TIME_RESOLVED: (
        "On this date the clocks were set back, so this hour happened twice. It "
        "was resolved using the summer-time answer you gave."
    ),
    nc.CalculationNote.LOCAL_MEAN_TIME: (
        "The birth time was treated as sun-based local time rather than a "
        "standard clock time. This happens for births before standard clocks "
        "reached the region, or at sea and other places with no official time."
    ),
    nc.CalculationNote.PRE_GREGORIAN_DATE: (
        "On this date the birth place had not yet adopted Gregorian calendar. If "
        "the original record used Julian calendar, be sure to convert the date to "
        "Gregorian first."
    ),
    nc.CalculationNote.TIMEZONE_ESTIMATED: (
        "The exact time zone for this place and date could not be confirmed, so "
        "the best available historical local time was used. If you know the "
        "official local time, please enter it."
    ),
    nc.CalculationNote.TIMEZONE_BORDERS_UNCERTAIN: (
        "Borders near the birth place shifted around the birth year, so even "
        "which country's time applied is unclear. The best available historical "
        "local time was used. If you know the official local time, please enter it."
    ),
}


def notes_for_display(
    notes: tuple[nc.CalculationNoteItem, ...],
) -> list[dict[str, str]]:
    """Library notes as reader-facing text, cautions first.

    Used by both places a note is shown: the sheet and the form. A note with no
    message of ours keeps the library's own text, so a note added later still
    reaches the reader.
    """
    caution = nc.CalculationNoteType.CAUTION
    displayed = []
    for note in sorted(notes, key=lambda note: note.note_type != caution):
        displayed.append(
            {
                "message": NOTE_MESSAGES.get(note.note, note.message),
                # Severity picks the icon the sheet draws: caution vs. info.
                "kind": "caution" if note.note_type == caution else "notice",
            }
        )
    return displayed
