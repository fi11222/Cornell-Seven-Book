#set page(
  paper: "a4",
  margin: (x: 2cm, y: 2.5cm),
  header: align(right)[
    #text(8pt, weight: "light", fill: gray)[Authoring Guide • World History Manual]
  ],
  numbering: "1",
)

#set text(
  font: ("Noto Sans"),
  size: 11pt
)

#set heading(numbering: "1.1.")
#show heading: it => block(above: 1.5em, below: 1em)[
  #text(weight: "bold", fill: rgb("#1e293b"))[#it]
]

// Styling for inline code
#show raw.where(block: false): it => box(
  fill: rgb("#f1f5f9"),
  inset: (x: 3pt, y: 0pt),
  outset: (y: 3pt),
  radius: 2pt,
)[#text(font: ("IBM 3270", "DejaVu Sans Mono"), size: 9.5pt, fill: rgb("#0f172a"))[#it]]

// Styling for code blocks (syntax highlighting)
#show raw.where(block: true): it => block(
  fill: rgb("#f8fafc"),
  inset: 12pt,
  radius: 6pt,
  stroke: 1pt + rgb("#e2e8f0"),
  width: 100%,
)[#text(font: ("IBM 3270", "DejaVu Sans Mono"), size: 9.5pt)[#it]]

#let rule-box(body, title: "CRITICAL RULE") = block(
  fill: rgb("#fffbeb"),
  stroke: 1pt + rgb("#f59e0b"),
  inset: 12pt,
  radius: 4pt,
  width: 100%,
)[
  #text(weight: "bold", fill: rgb("#b45309"))[#title] \
  #v(4pt)
  #text(fill: rgb("#78350f"))[#body]
]

#align(center)[
  #block(text(weight: 700, 24pt, fill: rgb("#0f172a"))[Book Authoring Guide])
  #v(0.5em)
  #text(14pt, fill: rgb("#475569"))[Writing and Building the World History Manual]
  #v(2em)
]

This guide is the short, practical companion to `Book Code Conventions.pdf`, which documents everything `generate_book.py` does. Here: what the files are, how to work on the book day to day, and what to do with what the script tells you. Section numbers refer to the conventions document.

= The Files

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*File*], [*Role*],
  [`Chapter 01.xml` ... `Chapter 10.xml`], [*The book.* The only files whose text you write. Everything else is derived from them.],
  [`Chapter 00.xml`], [Front matter. Edit its `<teiHeader>` (title, subtitle, author, publisher, date) by hand; the table of contents below it is regenerated on every run and must not be edited.],
  [`Chapter 99 - Lexicon.xml`], [The lexicon. Generated, *except* the `<def>` definitions, which you write here; they are read back on every run. An entry reading `To be determined` is waiting for a definition. The bibliography at its end is also generated, *except* the entries of the primary sources (`corresp="#key"`), which you complete here.],
  [`Chapter 98 - Concordance.xml`], [Generated (A4 only, not part of the book). Never edit.],
  [`Chapter 99.xml`], [The old hand-written glossary, kept as a record. Ignored by the script; never rename it to the lexicon's name.],
  [`custom_dictionary.txt`], [Words the spell check must accept, one per line.],
  [`images/`], [The figures' image files (downloaded here on demand).],
  [`*.typ`, `*.pdf`], [Generated, per chapter and per format (`_a4`, `_ut`), plus the two consolidated books `World History Consolidated_a4.pdf` and `_ut.pdf`.],
)

= Building the Book

Run `python3 generate_book.py` in this directory; add `--sub` to list subsections in the table of contents too. It needs `typst`, `pdftk` and `aspell`. It skips every output that is already up to date, so a rerun with nothing changed takes a couple of seconds and a full build about 45 seconds.

#rule-box(title: "BACK UP BEFORE A RUN THAT CHANGES XML")[
  The script corrects some things directly in the XML files (a `[FIX]`, `[STRIP]`, `[QUOTE]` or `[RELOC]` line in the log), and the project is not under version control. Copy the `Chapter *.xml` files somewhere first. Never leave a copy of a chapter's XML in this directory: every `*.xml` next to the script is treated as a chapter.
]

Read the log from the top. `[WARN]` lines are the to-do list: each gives a file, a line and what is wrong (spelling, a missing id, a mis-typed name...). They never stop the build. A `(FAILED)` after a `[PDF]` line means Typst could not compile that sheet; the message above it says why.

= Editing the Text

There are two ways, and they are equivalent for ordinary prose.

- *In the XML* (any change, including structure: new sections, new tags, ids, figures, footnotes). Rerun the script.
- *In a `.typ` file* (a wording fix while proofreading the PDF). Edit the text *inside* the `/*rtN*/ ... /*rt/N*/` markers only, save, and rerun: the change is written back into the XML (`[SYNC]`), and both formats are rebuilt. Edit one format's `.typ` at a time. Anything outside the markers is overwritten (section 3).

The first letter of a chapter's opening paragraph is set as a decorative initial automatically (`#initial[X]` in the `.typ`); do nothing for it.

= Tagging Cheat-Sheet

The script finds first mentions itself; you only tag what it cannot know. Full rules: section 1.2 of the conventions.

#table(
  columns: (1.2fr, 2fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*To mark...*], [*Write*],
  [A new person, place, people or work], [`<name xml:id="Assyria" type="people">Assyria</name>` at its first mention; later mentions are `<name ref="Assyria">` (the script adds them). Name types: see section 2.2.],
  [A technical term], [`<term xml:id="Oikos" xml:lang="grc" rend="italic">oikos</term>`],
  [A foreign word that is not a term], [`<hi rend="italic" xml:lang="akk">...</hi>`],
  [An ordinary word that collides with a name], [`<name ref="Nanna-Sin" type="blocker">sin</name>`],
  [A block quotation], [`<cit><quote>...</quote></cit>` (no quote marks of its own)],
  [An inline quotation or scare quote], [`<q>` (`<q rend="italic">` for genuine quoted words)],
  [A footnote], [`<note place="foot">...</note>`],
  [A figure], [`<figure><graphic url="..."/><head>Caption</head></figure>`; numbering is automatic, per chapter, so check "see Figure 3" by hand.],
  [A modern book title], [`<title level="m" rend="italic">...</title>`],
)

Identifiers follow the conventions of section 1.2: the glossary headword with diacritics folded and `_` for spaces (`Ahura_Mazda`), `LASTNAME-Firstname` for modern people, `_-_` to tell homonyms apart.

= Common Tasks

*Add a chapter.* Create `Chapter 11.xml` following the skeleton of section 1.2 (title `Chapter 11 — Name`, one `<div type="chapter" n="11" xml:id="Chapter_11_Topic">`, sections as `<div type="section" n="1">` with the visible number typed in the `<head>`). The next run builds it, adds it to the table of contents and the lexicon, and to both consolidated books; every later sheet is renumbered.

*Write a lexicon definition.* Open `Chapter 99 - Lexicon.xml`, find the entry (its `xml:id` is the identifier of the tag), and replace `To be determined` with the text. A definition may hold `<q>` and `<hi rend="italic">`; a second meaning is a second `<sense n="2">`.

*Complete a bibliography entry.* The bibliography at the end of `Chapter 99 - Lexicon.xml` is rebuilt from the chapters' `<bibl>`s on every run, without page numbers, so a correction to a cited work goes in the chapter. A primary source (a `<name type="source">`) gets an entry with `corresp="#key"` that you write yourself and that is kept: by default it shows only the name, so add its author and title, for example `<bibl corresp="#Timaeus"><author ref="Plato">Plato</author>, <title level="m" rend="italic">Timaeus</title>.</bibl>`.

*Accept a word the spell check rejects.* Add it to `custom_dictionary.txt` (case-sensitive, one word per line).

*Change the title page.* Edit the header of `Chapter 00.xml`. A designed cover can be added in front of it: place `title page_a4.pdf` / `title page_ut.pdf` (or `title page.pdf`) in this directory and it opens each consolidated book. None exists yet.

*Change page layout or the initial.* The constants block at the top of `generate_book.py` (`PAPER_FORMATS`, `INITIAL_FONT`, ...); see the constants table in section 4.15. Changing the script rebuilds everything.
