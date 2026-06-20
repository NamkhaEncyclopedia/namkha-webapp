"""Write sample data.json + namkha.svg into templates/, then compile sheet.typ.

Run after any change to rendering code (Python render pipeline, sheet.typ, illustration.svg):

    poetry run python gen_preview_data.py

Outputs:
  templates/data.json      — for IDE Typst preview
  templates/namkha.svg     — for IDE Typst preview
  templates/preview.pdf    — compiled sheet for visual verification
"""

# TODO: remove before release

import json

import namkha_calculator as nc
import typst

from app import constants
from app.calculation_render import (FONTS, TEMPLATES, _build_data,
                                    fill_illustration)


class _SampleRequest:
    subject = constants.SAMPLE_SUBJECT
    namkha_type = constants.SAMPLE_NAMKHA_TYPE
    method = constants.SAMPLE_METHOD


result = nc.calculate_namkha(
    constants.SAMPLE_NAMKHA_TYPE, constants.SAMPLE_SUBJECT, constants.SAMPLE_METHOD
)

_sample_request = _SampleRequest()
(TEMPLATES / "namkha.svg").write_text(
    fill_illustration(result, _sample_request), encoding="utf-8"
)
(TEMPLATES / "data.json").write_text(
    json.dumps(_build_data(result, _sample_request), indent=2), encoding="utf-8"
)

compiled_pdf = typst.compile(
    str(TEMPLATES / "sheet.typ"),
    root=str(TEMPLATES),
    font_paths=[str(FONTS)],
)
(TEMPLATES / "preview.pdf").write_bytes(compiled_pdf)

print(f"Written to {TEMPLATES}/")
print("  namkha.svg")
print("  data.json")
print("  preview.pdf")
