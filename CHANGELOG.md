# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/) versioning.

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

[0.1.0a2]: https://github.com/NamkhaEncyclopedia/namkha-webapp/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/NamkhaEncyclopedia/namkha-webapp/releases/tag/v0.1.0a1
