"""Turning library notes into the text a reader sees."""

import namkha_calculator as nc

from app.notes import NOTE_MESSAGES, notes_for_display


def _note(note, note_type, message="developer-facing text"):
    return nc.CalculationNoteItem(note=note, note_type=note_type, message=message)


def test_cautions_come_before_notices():
    # Given in the opposite order, so passing cannot be an accident of input order.
    displayed = notes_for_display(
        (
            _note(nc.CalculationNote.LOCAL_MEAN_TIME, nc.CalculationNoteType.NOTICE),
            _note(nc.CalculationNote.HIGH_LATITUDE, nc.CalculationNoteType.CAUTION),
        )
    )
    assert [item["kind"] for item in displayed] == ["caution", "notice"]


def test_notes_of_one_kind_keep_their_order():
    displayed = notes_for_display(
        (
            _note(nc.CalculationNote.HIGH_LATITUDE, nc.CalculationNoteType.CAUTION),
            _note(
                nc.CalculationNote.PRE_GREGORIAN_DATE, nc.CalculationNoteType.CAUTION
            ),
        )
    )
    assert [item["message"] for item in displayed] == [
        NOTE_MESSAGES[nc.CalculationNote.HIGH_LATITUDE],
        NOTE_MESSAGES[nc.CalculationNote.PRE_GREGORIAN_DATE],
    ]


def test_a_note_we_have_no_message_for_keeps_the_library_text(monkeypatch):
    monkeypatch.delitem(NOTE_MESSAGES, nc.CalculationNote.HIGH_LATITUDE)
    displayed = notes_for_display(
        (
            _note(
                nc.CalculationNote.HIGH_LATITUDE,
                nc.CalculationNoteType.CAUTION,
                message="fallback text",
            ),
        )
    )
    assert displayed == [{"message": "fallback text", "kind": "caution"}]
