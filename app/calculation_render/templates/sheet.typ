// Calculation sheet layout. Reads text data from data.json and the
// color-only illustration from namkha.svg; image()/json() resolve relative to
// wherever this .typ file is compiled from, so a copy of it is placed next to
// freshly generated data.json/namkha.svg at compile time (per-request tmpdir
// for rendering, templates/ for the IDE preview script). Typst controls
// layout + all text; the SVG controls only the graphic.

#let data = json("data.json")

// Bounds the subject name in the footer/title so a long free-text entry
// can't push the footer line past the page margins. Counted in grapheme
// clusters, not bytes: str.len() and str.slice() are byte-based, and a cut
// landing mid-character is a hard error ("not a character boundary") that
// accented, Cyrillic and Tibetan names hit routinely.
#let footer-name-limit = 22
#let footer-name-clusters = data.subject.name.clusters()
#let footer-name = if footer-name-clusters.len() > footer-name-limit {
  footer-name-clusters.slice(0, footer-name-limit).join() + "…"
} else { data.subject.name }
#let name-suffix = if data.subject.name != "—" {
  ": " + footer-name
} else { "" }
#let sheet-title = (
  data.meta.type
    + " Namkha Calculation ("
    + data.meta.method
    + ")"
    + name-suffix
)
// Footer omits "Calculation" to save horizontal space for the subject name.
#let footer-title = (
  data.meta.type + " Namkha (" + data.meta.method + ")" + name-suffix
)
#let sheet-author = (
  "Namkha Calculator (lib: "
    + data.meta.version
    + ", app: "
    + data.meta.app_version
    + ")"
)

// PDF metadata (Title/Author), read by PDF viewers and OS file search.
#set document(
  title: [#sheet-title],
  author: sheet-author,
)

#set page(
  paper: "a4",
  margin: (x: 1.5cm, top: 1.5cm, bottom: 1.8cm),
  footer: context {
    set text(size: 8pt, fill: rgb("#666666"))
    align(
      center,
      [#footer-title | #underline(link("https://calculator.namkha-encyclopedia.com/")[Namkha Calculator]) (lib: #data.meta.version, app: #data.meta.app_version) | Page #counter(page).display() of #counter(page).final().first()],
    )
  },
)
#set text(size: 12pt, font: "Inclusive Sans")
#set par(leading: 1.2em)
#show table: set text(font: "Glacial Indifference")
#show table: set par(leading: 0.65em)

#grid(
  columns: (auto, 1fr),
  column-gutter: 8pt,
  align: horizon,
  image("logo.svg", height: 28pt),
  text(
    18pt,
    weight: "bold",
    font: "Alegreya SC",
  )[#data.meta.type Namkha Calculation],
)
#v(4pt)
#line(length: 100%, stroke: 0.5pt)
#v(6pt)

#grid(
  columns: (3fr, 1fr),
  row-gutter: 10pt,
  align: (left, right),
  [*Name:* #data.subject.name], [*Type:* #data.meta.type],
  [*Gender:* #data.subject.gender], [*Method:* #data.meta.method],
  [*Birth:* #data.subject.birth], [],
  [*Location:* #data.subject.location], [],
)

#v(4pt)
#line(length: 100%, stroke: 0.5pt)
#v(20pt)

#par(leading: 0.65em)[
  *#data.meta.type element:* #data.meta.birth_element \
  *#data.meta.type animal:* #data.meta.birth_animal \
  *#data.meta.type mewa:* #data.meta.birth_mewa
]

#v(6pt)

#table(
  columns: (auto, auto, auto, auto, auto, auto, 1fr, auto),
  inset: 6pt,
  align: (x, y) => {
    let horizontal_alignment = (
      left,
      center,
      center,
      left,
      center,
      left,
      left,
      center,
    ).at(x)
    if y == 0 { horizontal_alignment } else { horizontal_alignment + horizon }
  },
  fill: (x, y) => if y == 0 {
    rgb("#F0EDE8")
  } else if x == 0 {
    rgb("#F8F6F3")
  } else { none },
  stroke: (x, y) => {
    let outer = 1pt + black
    let inner = 0.5pt + rgb("#666666")
    (
      left: if x == 0 or x == 1 { outer } else { inner },
      right: if x == 0 or x == 7 { outer } else { inner },
      top: if y == 0 or y == 1 { outer } else { inner },
      bottom: if y == 0 or y == 8 { outer } else { inner },
    )
  },
  table.header(
    [*Aspect*],
    [*Element*],
    [*Mewa \ No.*],
    [*Syllable \ colour*],
    [*Syllable*],
    [*Center \ colour*],
    [*Harmonization \ sequence*],
    [*Conf- \ lict*],
  ),
  ..data
    .aspects
    .map(aspect => (
      text(weight: "bold", style: "italic")[#aspect.label],
      [#aspect.element],
      if aspect.mewa == none [—] else [#aspect.mewa],
      [#aspect.syllable_color],
      stack(
        dir: ttb,
        spacing: 2pt,
        text(
          size: 22pt,
          font: "Noto Serif Tibetan",
          top-edge: "bounds",
          bottom-edge: "baseline",
        )[#aspect.syllable_tibetan],
        text(size: 9pt)[#aspect.syllable_roman],
      ),
      [#aspect.center_color],
      [#aspect.sequence],
      if aspect.conflicted == none [–] else if aspect.conflicted [#text(
        size: 12pt,
        fill: rgb("#C0392B"),
      )[●]] else [],
    ))
    .flatten(),
)

#pagebreak()
#align(center, image("namkha.svg", width: 100%))

// One note stays under the illustration; two or more get their own page so the
// section is never split across the page break.
#if data.notes.len() >= 2 { pagebreak() } else { v(8pt) }
== Notes
#let note-icon(kind) = move(
  dy: 0.5pt,
  image(
    if kind == "caution" { "note_caution.svg" } else { "note_notice.svg" },
    width: 12pt,
  ),
)
#if data.notes.len() == 0 [
  —
] else {
  set par(leading: 0.55em)
  grid(
    columns: (auto, 1fr),
    column-gutter: 6pt,
    row-gutter: 11pt,
    align: (center + top, left + top),
    ..data.notes.map(note => (note-icon(note.kind), [#note.message])).flatten(),
  )
}
