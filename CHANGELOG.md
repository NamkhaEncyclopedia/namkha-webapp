# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/) versioning.

## [0.1.0a3] – 2026-08-19

### Added

- The birth time zone is worked out while the form is filled, from the birth
  place, date and time, and shown under the field with the name the sheet will
  use.
- Warnings and notes about the birth details appear under the form as soon as the
  time zone is known, worded the same way as on the sheet.
- The time zone list stays open below its search field, grouped by region and
  scrolled to the zone already found. Each row shows the offset that applied on
  the birth date.

### Changed

- Requires namkha-calculator 0.1.0a5.
- The resolved time zone stays on the server. The form carries only a ticket for
  it, so the browser cannot submit a zone the server did not work out.
- The sheet names the time zone the calculation used. A birth before standard
  time reached the place reads "mean solar time" instead of a zone name.
- The birth time field is labeled "Local birth time", so it is clear which clock
  the entered time belongs to.

### Fixed

- The birth place search no longer says "no places found" before it has searched.
- Entered coordinates are rounded, and the time zone lookup waits for the entry
  to end instead of running on every keystroke.
- The PDF is built from the calculation the server kept, not from fields posted
  back by the page, so it always matches the sheet on screen. A result that has
  expired asks for a fresh calculation.

### Security

- The time zone route accepts only zone keys the picker offers.

## [0.1.0a2] – 2026-07-26

### Added

- Cloudflare Turnstile gate on the calculator form, raising the cost of automated
  submissions to the calculation and PDF routes.
- Docker image and deployment configuration.

### Changed

- Calculation notes on the result sheet are now sorted by importance rather than
  by the order the library returns them.
- Result sheet footer reworked: it now links back to the calculator, and long
  subject names are truncated instead of wrapping the footer onto a second line.
- Image lightbox rewritten, with its styles and behavior moved into dedicated
  files.
- The session token store is now bounded with per-client caps, so it cannot grow
  without limit under repeated page loads.

### Fixed

- Free-text form fields are now length-bounded, and the subject name is truncated
  at grapheme boundaries so combining marks and multi-codepoint characters are
  not split.
- Removed a Unicode symbol from the result table that did not render in the sheet
  font.

## [0.1.0a1] – 2026-07-25

Initial alpha release: web form for Namkha calculation, rendering a typeset sheet
with an inline thread-cross illustration and a downloadable PDF.

[0.1.0a3]: https://github.com/NamkhaEncyclopedia/namkha-webapp/compare/v0.1.0a2...v0.1.0a3
[0.1.0a2]: https://github.com/NamkhaEncyclopedia/namkha-webapp/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/NamkhaEncyclopedia/namkha-webapp/releases/tag/v0.1.0a1
