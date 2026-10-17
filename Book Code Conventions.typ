#set page(
  paper: "a4",
  margin: (x: 2cm, y: 2.5cm),
  header: align(right)[
    #text(8pt, weight: "light", fill: gray)[Generator Behavior Reference • World History Manual]
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

// Custom warning/rule block (amber) -- a critical, easy-to-miss caveat
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

// Auto-corrected block (green) -- the script silently fixes this and logs it
#let fix-box(body, title: "AUTO-CORRECTED") = block(
  fill: rgb("#f0fdf4"),
  stroke: 1pt + rgb("#22c55e"),
  inset: 12pt,
  radius: 4pt,
  width: 100%,
)[
  #text(weight: "bold", fill: rgb("#15803d"))[#title] \
  #v(4pt)
  #text(fill: rgb("#14532d"))[#body]
]

// Report-only block (slate) -- the script only ever flags this, never touches it
#let report-box(body, title: "REPORT ONLY") = block(
  fill: rgb("#f8fafc"),
  stroke: 1pt + rgb("#64748b"),
  inset: 12pt,
  radius: 4pt,
  width: 100%,
)[
  #text(weight: "bold", fill: rgb("#334155"))[#title] \
  #v(4pt)
  #text(fill: rgb("#1e293b"))[#body]
]

// Known gap (rose) -- something the chapter files contain that the script does not handle
#let gap-box(body, title: "NOT SUPPORTED") = block(
  fill: rgb("#fff1f2"),
  stroke: 1pt + rgb("#e11d48"),
  inset: 12pt,
  radius: 4pt,
  width: 100%,
)[
  #text(weight: "bold", fill: rgb("#be123c"))[#title] \
  #v(4pt)
  #text(fill: rgb("#881337"))[#body]
]


#align(center)[
  #block(text(weight: 700, 24pt, fill: rgb("#0f172a"))[Book Code Conventions])
  #v(0.5em)
  #text(14pt, fill: rgb("#475569"))[What `generate_book.py` Actually Does]
  #v(2em)
]

= Introduction
This document describes the *current, actual behavior* of `generate_book.py`, the script that validates, corrects, renders, and round-trips the `Chapter NN.xml` files of the World History manual. Where this document and the code disagree, the code is what runs; this document should be kept in sync with it, not the other way around. It reflects the script as of 2026-09-21.

It is the book's counterpart of `Code Conventions.typ` in the `Calneh - Nimrod` project, and deliberately follows the same structure so the two can be read side by side. The *authoring* rules -- how a human writes the TEI XML -- are those of `XML_TEI conventions.typ`, which currently lives only in the `Calneh - Nimrod` project; this project has no copy of its own yet. Section 1.2 lists the book-specific conventions layered on top of it.

Four areas, matching the script's own structure:
- *XML validation* -- what gets checked against the TEI conventions (including the English spell check), and which violations the script fixes automatically versus merely reports (section 2).
- *The XML ↔ Typst round trip* -- how a hand-edit made in a `.typ` file finds its way back into the source XML (section 3).
- *Everything else* -- the rendering, styling, lexicon- and concordance-generation, and build-pipeline behavior that turns validated XML into the finished PDFs (section 4).
- *What the script does not handle* -- constructs the chapter files contain that have no rendering or validation rule (section 5).

#rule-box(title: "SOURCE OF TRUTH")[
  XML is always the source of truth for document structure. Typst files are a *rendering* of the XML, regenerated on every run, with one narrow exception: a hand-edited `.typ` file can sync bounded text edits back into the XML it came from (section 3). Nothing else about a `.typ` file's content ever survives a regeneration.
]

== Relationship to `generate_view.py`

`generate_book.py` began as a copy of `generate_view.py` (the script used by `Calneh - Nimrod` and `Kullaba - E-Anna`) and was renamed because its functionality is expected to diverge. It is *not* kept identical to those copies, so the "one script, several directories" rule of the parent document does not apply to it. As of this writing it differs from the parent script in the seventeen behaviors below; everything else in this document is inherited unchanged.

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  table.header([*Difference*], [*Detail*]),
  [Chapter divisions], [`<div type="chapter">` is validated exactly like `<div type="sheet">`: both require `n` and `xml:id`, and both are rendered as plain wrapper divisions (section 4.7). The two types are otherwise interchangeable.],
  [Non-sheet XML files], [A new per-directory constant, `NON_SHEET_XML_FILENAMES`, names `*.xml` files that are never treated as source sheets (section 4.15). Here: `Chapter 00.xml` and `Chapter 99.xml`.],
  [File-number pattern], [The pattern used to recognize a sheet's number in a file name (`_SHEET_NUM_RE`) matches `Chapter NN` as well as `Sheet NN`. It drives both PDF consolidation and the concordance's number column.],
  [Word tokenizer], [The tokenizer behind first-occurrence matching (`_WORD_TOKEN_RE`) reads Unicode letters instead of ASCII letters only, so an accented letter never splits a word (section 2.1). The spell checker already used a Unicode tokenizer.],
  [Concordance column heading], [The first column of the concordance's occurrence table is headed "Chapter" instead of "Sheet" (section 4.13).],
  [Tagging dictionary], [No identity is split at its hyphens any more. The extra strings that resolve to an identity -- the halves of `Inanna-Ishtar`, a scholar's surname -- are listed in `tagging_dictionary.json`, and a new scholar or prophet gets a surname line automatically (section 2.1).],
  [Reconstructed forms], [An asterisk is no longer flagged when it opens a language-marked `<hi>`/`<term>`/`<foreign>` -- the scholarly marker of a reconstructed form (section 2.2).],
  [Figure captions in relocation], [The search for an "earlier mention" of an identity (first-occurrence relocation) skips the contents of a `<figure>`, so a definition is never anchored in a caption (section 2.4).],
  [Line breaks and verse], [`<lb/>` renders as a line break and consecutive `<l>` lines are separated by line breaks, so verse (`<lg>`/`<l>`) and lineated text survive inside a `<cit>` quotation; a `<cit>` quote's boundary quote marks are trimmed even when whitespace surrounds the text (section 4.10).],
  [Language names], [The table of language names used in the lexicon and in `<cit>` labels also knows `egy`, `pi`, `xhu`, `he` and `ine-pro` (section 4.10).],
  [Figures], [A `<figure>` with `<graphic>` images is rendered: images are located (or downloaded) in `images/`, sized, and captioned. This is a port of the figure handling of the legacy `tei_to_typst.py` (section 4.16).],
  [Title block], [The title block is itself a floating element at the top of the first page, so that a floating figure can never land above it (section 4.6).],
  [Page numbering], [The sheets of the book are numbered continuously, as in the printed book, and `Chapter 00.xml`'s table of contents records the resulting page numbers (section 4.18). This is a port of the legacy pipeline's `start_page` mechanism.],
  [Front matter], [`Chapter 00.xml` is rendered as a title page and a table of contents, in every paper format, ahead of the first chapter (section 4.18).],
  [Landscape tables], [Only a table of six or more columns is moved to its own landscape page (the parent script does so from four columns); a narrower table flows inline (section 4.8).],
  [Illuminated initial], [The first letter of a chapter's opening paragraph is set as a large decorative initial (section 4.6). This is a port of the drop cap of the legacy `tei_to_typst.py`; its running page header was deliberately *not* ported.],
  [Paper formats], [Every sheet is built in two paper formats, A4 and US Trade (6 x 9 in), as `NAME_a4` and `NAME_ut`, and each format has its own consolidated PDF (the concordance in A4 only). This is a port of the dual-format feature of the legacy `tei_to_typst.py` (section 4.17).],
)

== Book conventions the script relies on

The chapter files follow the Calneh conventions with the following book-specific decisions. Those marked *checked* are verified by the validation phase (section 2.2); the rest are authoring policy that nothing enforces.

*File set.*
- `Chapter 01.xml` ... `Chapter 10.xml` are the source chapters. Numbers are zero-padded in file names so plain filename sort is chapter order.
- `Chapter 00.xml` and `Chapter 99.xml` are not source sheets: the script never validates or syncs them, and they take no part in first-occurrence tracking. `Chapter 00.xml` is the book's *front matter*: its `<teiHeader>` (title, subtitle, author, publisher, date, availability) is hand-maintained and never touched, and the script regenerates its table of contents on every run and renders it (section 4.18). `Chapter 99.xml` is the *legacy* hand-written glossary; its definitions were migrated into the generated lexicon (section 4.12), and the file is now only a record.
- `Chapter 99 - Lexicon.xml` and `Chapter 98 - Concordance.xml` are *generated* by the script (sections 4.12 and 4.13). They deliberately have different names from the legacy `Chapter 99.xml`; see the caution in section 4.12.

*Chapter skeleton.*
```xml
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Chapter 4 — Ancient Egypt – An Uruk Copycat Regime on the Nile</title>
      </titleStmt>
      ...
    </fileDesc>
  </teiHeader>
  <text><body>
    <div type="chapter" n="4" xml:id="Chapter_04_Ancient_Egypt">
      <p>Introductory text before the first section...</p>
      <div type="section" n="1">
        <head>4.1 The Reverse Order of Appearance of Deities</head>
        <p>...</p>
      </div>
    </div>
  </body></text>
</TEI>
```
- *Title* -- `Chapter N — Name` with exactly *one* em dash (the script splits the title there, section 4.6). En dashes inside the name are fine; a second em dash disables the split. (Not checked: nothing warns if the title is wrong.)
- *Chapter division* -- `type="chapter"`, with `n` and `xml:id` (*checked*). The `xml:id` has the form `Chapter_NN_Topic`. There is *no* `<head>` directly under the chapter division: the title block already shows the chapter title.
- *Sections* -- `type="section"` with `n` (*checked*) holding the section's *local* ordinal (`1`, `2`, ...), not `4.1`. The visible numbering is typed into the `<head>` text.
- *Header author* -- none. A header `<author>` is treated like any other `<author>` and draws a missing-`xml:id` warning, and a nested `<persName>` is not rendered (the author line reads only the `<author>` element's own text).

*Identifiers* (policy). The first occurrence of a name or term carries the `xml:id`; the script derives the rest.
- A name's id is its glossary headword with diacritics folded to ASCII and spaces replaced by underscores (`Ahura_Mazda`, `Gobekli_Tepe`). The script restores diacritics and apostrophes in the lexicon headword from the tag's displayed text, so `Ahura_Mazda` on _Ahura Mazdā_ still yields "Ahura Mazdā".
- A modern person's id is `LASTNAME-Firstname` (`DUMEZIL-Georges`, `CERVANTES-Miguel_de` -- a particle travels with the first name; a mononym is doubled, `STENDHAL-Stendhal`).
- A single name that itself contains a hyphen keeps it (`Naram-Sin`, `Amun-Re`), like the reference project. Two different things sharing one name are told apart with the `_-_` pattern (`Thebes_-_Egypt`, `Thebes_-_Greece`).
- One entity known under two names holds a single id, on whichever name occurs first; later uses are `ref` (`Nanna-Sin`, `Ashvins`, `Kingdom_of_Mitanni`).
- A name whose two halves should each also work alone, like _Inanna/Ishtar_, uses a hyphenated id (`Inanna-Ishtar`), and each half gets its own line in the tagging dictionary (section 2.1).

*Name types* (policy; the allowed values are the nine listed in section 2.2). Empires, kingdoms, states and civilizations (Assyria, Elam, Sumer, the Achaemenid Empire) are `people`; cities, regions and physical features are `place`. Modern novelists, like other religious-philosophical figures, are `prophet`. A religion or "-ism" (Zoroastrianism) is a `<term>`, not a `<name>`. The type is spelled `artifact`, not `artefact`. `xml:lang` is *not* put on names as a rule. On a `<name>` it has no effect on running text (only `rend="italic"` italicizes a name); its one visible effect is in the lexicon, where the headword is then italicized and followed by a bracketed language name. The reference project uses it on names only for the Sumerian _Inanna_/_Ishtar_ and _Hagia Sophia_.

*Terms.* A term's `xml:id` is its lemma, capitalized as in the glossary (`Civilization`, `Ver_sacrum`); a parenthetical abbreviation is left out of the id (`Early_Dynastic` on _Early Dynastic (ED)_). A term that is a lone English stop word gets a disambiguated id (`me_-_Sumerian_term`). A *foreign* term carries `xml:lang` and `rend="italic"` (`sux`, `sa`, `pi`, `grc`, `la`, `akk`, `egy`, `de`, `xhu`); an anglicized loanword (_karma_, _ziggurat_, _caste_) does not. A later use of a term is `<term ref="..." rend="italic">`.

*Foreign words that are not terms.* A foreign word or phrase that is not a lexicon term stays `<hi rend="italic" xml:lang="...">`: italic, exempt from the spell check, and outside the lexicon. Proto-Indo-European reconstructions are `<hi rend="italic" xml:lang="ine-pro">*dyēws</hi>`.

*Works and their titles.* An ancient or primary work is a `<name type="source">` (`Rigveda`, `Epic_of_Gilgamesh`), with `rend="italic"` to keep the title italic; a later mention is `<name ref="..." rend="italic">`. A modern book mentioned in running text is `<title level="m" rend="italic">` (plus `xml:lang` when not English). A `<bibl>` is built only where the text itself supplies the data -- author, publisher, year -- and never invents any (three notes at present). A footnote's `n` and `type="footnote"` attributes are not used.

*Quotations.* A block quotation is `<cit><quote>...</quote></cit>`, without its own quote marks; verse lines are `<lg>`/`<l>`, and other line breaks `<lb/>`. An inline quotation is `<q>`: `<q rend="italic">` for a genuine quotation (someone's words, a text, scripture), plain `<q>` for a scare-quoted word, a mentioned term or a translation gloss.

*Figures.* A figure is `<figure>` holding one or two `<graphic url="..."/>` (a web URL, or a path such as `./images/x.jpg`), a `<head>` that is its caption, and optionally descriptive `<p>` or `<list>` children shown in small type under the caption (section 4.16). Figures are numbered automatically, per chapter, so "see Figure 3" in the text must match the third figure of that chapter; nothing checks this.

*Blockers.* An ordinary word that merely collides with a registered name is marked `<name ref="X" type="blocker">sin</name>`, where `X` is the identity whose trigger it would otherwise be swallowed by (section 2.1).

= XML Validation Phase

Two categories of check run against every sheet. Some violations are *auto-corrected in place* (the fix is written back to the XML file, and logged as `[FIX]`); everything else is only ever *reported* (`[WARN]`, file name + line number) and left for a human to fix by hand.

== The First-Occurrence / Subsequent-Use Registry

This is the one convention with real structural teeth: a shared identity registry walks every sheet, in sheet order, and enforces the "first occurrence enters the lexicon, everything after points back to it" rule from the conventions doc. Everything in this subsection is auto-corrected.

#fix-box[
  A `<name type="...">` whose value isn't already lower-case is rewritten in place, *before* anything else reads it (so the lexicon and rendered output both see the corrected value). \
  Log: ` lowercased <name type="{orig}"> to type="{lower}"`
]

- *Legacy `key` → `xml:id` migration.* A first-occurrence `<name>`/`<term>`/`<author>` still using the deprecated `key="..."` attribute has it renamed to `xml:id`. If the value isn't a legal XML id, one is minted (illegal characters become `_`; a leading digit or other invalid start character gets a `_` prefix). Unicode letters are legal and kept as they are. \
  Log: ` renamed <{tag} key="{old}"> to xml:id="{new}" (first occurrence)`
- *Malformed `xml:id` reminting.* A first-occurrence `<name>`/`<term>` that already has an `xml:id`, but one that isn't a legal XML NCName (a space in it, say), has it reminted the same way. This is logged as a fix too, since the file is only written back to disk when at least one fix was logged. \
  Log: ` reminted invalid <{tag} xml:id="{old}"> as xml:id="{new}"`
- *Duplicate first occurrence → `ref`.* If an identity is already registered, a second "first occurrence" of it has its `key`/`xml:id`/`type` stripped and replaced with `ref="{prior_id}"`. \
  Log: ` converted duplicate first occurrence (id="{ident}") into <{tag} ref="{prior_id}">`
- *Bare-mention wrapping.* Every text run is scanned for word sequences matching an already-registered identity; a match is spliced into a new `<name ref="...">`/`<term ref="...">`. Matching is greedy and longest-first, never overlaps two matches, and is *case-insensitive* -- except for tagging-dictionary strings, which match only as written (below). \
  Log: ` wrapped bare "{text}" as <{tag} ref="{ref_id}">`
- *`<hi>` retagging.* A childless `<hi>` whose entire text content -- with nothing but surrounding whitespace -- exactly matches a registered identity is retagged in place into `<name ref="...">`/`<term ref="...">` (dropping `rend`, unless the tag is `term`). An `<hi>` that has child markup, or whose match is only part of a longer label, is left alone structurally. \
  Log: ` converted <hi> wrap of "{text}" into <{tag} ref="{ref_id}">`

*What a "word" is.* Word tokens are Unicode letters plus internal apostrophes (`[^\W\d_]+(?:['’][^\W\d_]+)*`); punctuation, digits and underscores are never absorbed into a match. Two consequences follow. An accented letter never splits a word, so a name such as _Durgā_ is one token, and a bare "Durgā" is wrapped whole. And a possessive is a single token: "Durgā's" is *not* matched by the identity "Durgā", so possessives are never wrapped automatically and must be tagged by hand, as they are in the reference project (`<name ref="Hammurabi">Hammurabi</name>'s`).

#rule-box(title: "ORDINARY WORDS THAT COLLIDE WITH NAMES")[
  Because matching is case-insensitive and blind to meaning, a registered identity swallows every ordinary word spelled like it. A first-occurrence `<name>` "Sin" would wrap the English word "sin" everywhere; an identity "Re" (_Amun-Re_'s solar god, say) would wrap the prefix in "re-establish". (Tagging-dictionary strings match case-sensitively, so the surname "White" never catches the colour "white"; a capitalized "White" opening a sentence still collides.) The remedy is a *blocker*: the colliding word is hand-tagged `<name ref="X" type="blocker">word</name>`, which stops the wrapper (a hand-written element is never re-wrapped) and is recorded as no occurrence anywhere (section 2.1, below). A blocker is not something the script can add; it needs human judgment.
]

#rule-box(title: "THE TAGGING DICTIONARY")[
  An identity's triggers are its `xml:id` and its displayed text, each matched only as a whole phrase -- nothing is ever split at a hyphen. Every other string that must resolve to an identity is listed in `tagging_dictionary.json`, next to the script: a JSON list with one entry per line, `{"string": "Ishtar", "key": "Inanna-Ishtar", "tag": "name"}`, where `key` is the identity's `xml:id` and `tag` is `name` or `term`. Several strings may share one key. A dictionary string matches *case-sensitively* -- exactly as written in the file -- so the surname "White" tags "White" but never the word "white"; list each spelling that should match on its own line. A string becomes a trigger only once its key's first occurrence has been registered, exactly like the key itself, and registration never overwrites: the identity that occurs first owns a shared trigger. Like any trigger, a dictionary string also retags a childless `<hi>` holding exactly that string (`<hi>` retagging, above). A trailing comma before the closing `]` is tolerated; any other JSON error stops the build. An entry without a valid `string`, `key` and `tag` is ignored with a warning; a `tag` that disagrees with the identity's own tag is warned about, and the identity's tag is used. A key that has no first occurrence anywhere in the corpus is reported after the sheets are processed. \
  *Surnames of scholars and prophets.* When a first-occurrence `<name type="scholar">` or `<name type="prophet">` that is _new_ -- absent from the lexicon the previous run left -- has a `LASTNAME-Firstname` id, its bare surname is added to the dictionary, spelled as the displayed text spells it (`DUMEZIL-Georges` on "Georges Dumézil" gives "Dumézil", `OBRIEN-Julia_M.` gives "O'Brien"), and takes effect at once. No line is added for an "X_OF_Y" epithet (`ISIDORE_OF_SEVILLE-Isidore`), or when the surname is the whole displayed name ("Molière", "Columbanus"), which is already a trigger. An identity already in the lexicon never gets one, so a surname line deleted by hand -- a surname that is also an ordinary word, like "West" -- stays deleted. An identity defined by an `<author>` never gets one either. A surname that the dictionary already lists for _another_ key is never added a second time (a shared string only tags the identity registered first, so the line would be dead); instead, for a scholar/prophet `<name>` and for an `<author>` alike, every run warns that its bare mentions must be tagged by hand. \
  Log: ` [WARN] {file}:{line}: surname "{surname}" of {key} not added to tagging_dictionary.json -- already listed for {other keys}; tag its bare mentions by hand` The file is rewritten, sorted by key, only when a line was added. \
  Log: ` [DICT] {file}:{line}: added "{surname}" -> {key} to tagging_dictionary.json`
]

#rule-box(title: "ENGLISH STOP WORDS NEVER BECOME AUTO-TAG TRIGGERS")[
  A `<name>`/`<term>` whose identity is deliberately disambiguated (e.g. `xml:id="mesh_-_Sumerian_term"` on the word "me") does *not* register its bare surface text as a corpus-wide trigger when that text is a lone English stop word (`a`, `an`, `the`, `me`, ...) -- otherwise every ordinary "me" in the corpus would be wrapped as that entity. This is what once let the deity "An" swallow every article "an"/"An". An identity that _is_ the bare stop word itself (no disambiguation at all) still registers as before, and is caught by the stop-word warning in section 2.2 instead.
]

- *`type="blocker"` occurrences.* `<name ref="X" type="blocker">proto</name>` is the one sanctioned use of `type` on a `ref`-pointing `<name>`: it records that this text merely _collides_ with identity X (e.g. "proto" in "proto-lugal", "sin" in "absence of sin") rather than genuinely mentioning it. It is still valid markup, but it gets no occurrence marker in the PDF, is never counted as an occurrence in the concordance, and can never supply a lexicon entry.

- *`<author>` shares the identity registry with `<name>`/`<term>`.* An `<author>` tag and a `<name type="prophet">`/`<name type="scholar">` for the same person resolve to one identity -- whichever appears first in the sheet sequence gets the `xml:id`; the other gets `ref`. An `<author>`'s first-occurrence id is registered as if it were a `name` identity (never as `author`, since `<author>` is bibl-only structural markup), together with whatever strings the tagging dictionary lists for it. Its type is `prophet` when the `<author>` carries `type="prophet"` -- which also frees its key from the `LASTNAME-Firstname` shape (`<author xml:id="Benedict_of_Nursia" type="prophet">`) -- and `scholar` otherwise: a lexicon entry backfilled from a later `<name ref="...">` (which has no type of its own) is typed accordingly, while a later `<name type="...">` that lost the identity race to the `<author>` keeps its own type. `<bibl>`/`<teiHeader>`/document-level `<title>` subtrees are otherwise not scanned for bare mentions, except that every `<author>` inside a `<bibl>` still participates in the shared registry. A `<name>`/`<term>` nested in a work's `<title>` in the running text (`<title level="m" rend="italic"><name xml:id="Mahabharata" type="source">Mahabharata</name></title>`) registers like any other, as a first occurrence or a ref; only the title's untagged text is never scanned.
- *Both the id and the displayed text are triggers.* A first-occurrence tag registers its `xml:id` (underscores read as spaces) *and*, when different, its displayed text. That is why a person's id `GIRARD-Rene` on the text "René Girard" makes a later bare "René Girard" resolve, while a bare "Girard" resolves only if the tagging dictionary lists it. Both are matched case-insensitively, except a displayed text that is nothing but the bare surname of a `LASTNAME-Firstname` key (`<name xml:id="HEAD-Ronan_James">Head</name>`): like a dictionary surname, it is matched case-sensitively, so a surname that is also an ordinary word ("Head") never catches the word itself ("head").
- *Lexicon backfill.* Every genuinely new first-occurrence registration also records a lexicon entry (lemma, language, type, source file/title) -- first writer wins. If an identity's true first occurrence was an `<author>` tag (which has no `type`/lemma of its own), a later `<name>`/`<term>` occurrence for the same identity backfills the entry (a `type="blocker"` occurrence never does).

== Everything Else in the Conventions -- Report Only

#report-box[
  Nothing in this subsection is ever touched by the script. A violation is only ever printed as `[WARN] {file}:{line}: {message}` -- correcting it is left to a human.
]

#table(
  columns: (1fr, 1.4fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Element / condition*], [*Message*],
  [`<name ref="...">` also carries `key`/`xml:id`/`type` (other than `type="blocker"`)], [`<name ref="..."> must not also carry key/xml:id/type (except type="blocker")`],
  [`<name>` (no `ref`) still uses `key`], [`<name> uses deprecated "key" -- should be "xml:id"`],
  [`<name>` (no `ref`) missing `xml:id`], [`<name> first occurrence missing "xml:id" attribute`],
  [`<name>` (no `ref`) missing `type`], [`<name> first occurrence missing "type" attribute`],
  [`<name type="...">` not one of the allowed values (`place`, `deity`, `artifact`, `people`, `event`, `ruler`, `source`, `prophet`, `scholar`)], [`<name type="{t}"> is not a recognized type`],
  [`<term ref="...">` also carries `key`/`xml:id`], [`<term ref="..."> must not also carry key/xml:id`],
  [`<term>` (no `ref`) still uses `key`], [`<term> uses deprecated "key" -- should be "xml:id"`],
  [`<term>` (no `ref`) missing `xml:id`], [`<term> first occurrence missing "xml:id" attribute`],
  [`<name>`/`<term>` wrapping nothing but an English stop word, with `ref`/`xml:id` equal to that same word], [`<{tag} ref="{id}"> wraps the English stop word "{w}" -- likely an accidental tag/word collision, not a deliberate mention`],
  [`<hi>` carries `key`/`xml:id`/`type`/`ref`], [`<hi> must not carry key/xml:id/type/ref -- use <name>/<term> instead`],
  [`<div type="sheet">` or `<div type="chapter">` missing `n` or `xml:id`], [`<div type="{type}"> missing "n" attribute` / `..."xml:id" attribute`],
  [`<div type="section">` missing `n`], [`<div type="section"> missing "n" attribute`],
  [`<author ref="...">` also carries `key`/`xml:id`], [`<author ref="..."> must not also carry key/xml:id`],
  [`<author>` (no `ref`) still uses `key`], [`<author> uses deprecated "key" -- should be "xml:id"`],
  [`<author>` (no `ref`) missing `xml:id` -- including one in the `<teiHeader>`], [`<author> missing "xml:id" attribute`],
  [`<author xml:id="...">` not `LASTNAME-Firstname` shaped (and no `type="prophet"`)], [`<author xml:id="{k}"> does not match LASTNAME-Firstname format`],
  [`<author type="...">` other than `prophet` or `scholar`], [`<author type="{t}"> must be "prophet" or "scholar"`],
  [bibliographic `<title>` (inside a `<bibl>`) with a `level` other than `m`, `j` or `a`], [`<title level="{l}"> must be "m", "j", or "a"`],
  [bibliographic `<title level="m">` (book) or `level="j"` (journal/series) missing `rend="italic"`], [`<title level="{l}"> ({kind}) missing rend="italic"`],
  [`<date>` missing `when`, or `when` neither a 4-digit year (`1998`) nor a full `YYYY-MM-DD` date (`2018-09-26`)], [`<date> missing/invalid "when" attribute ("{when}")`],
  [`<citedRange>` missing `unit="page"`], [`<citedRange> missing unit="page"`],
  [`<ptr>` missing/malformed `target="#id"`], [`<ptr target="{t}"> must be target="#id", pointing at a footnote's own xml:id`],
  [`<note place="foot" xml:id="...">` id itself starts with `#`], [`<note xml:id="{id}"> is malformed -- the id itself must not include the leading "#" (that belongs only on a <ptr target="#...">)`],
  [a literal `"`, `“` or `”` outside an existing `<q>`/`<quote>` (only reached for a unit the auto-conversion in section 2.4 had to leave alone)], [`literal quote character {ch} -- use <q> instead`],
  [a literal `*` (except a run of two or more, such as `f***`, which is a censoring mask and stands as typed)], [`literal "*" -- use <hi rend="italic"> for emphasis instead`],
  [stray `#w;`/`#y;` text], [`stray "#w;" occurrence-marker text -- a corrupted <name>/<term> from a past sync; retag it by hand`],
  [a word aspell doesn't recognize (section 2.3)], [`spelling: "{word}" not recognized ({n} occurrences) -- fix the typo, or if this is deliberate vocabulary, add it to custom_dictionary.txt`],
)

Quote characters that already appear *inside* a `<q>`/`<quote>` are legitimate nested punctuation and are not flagged; `<bibl>` contents are skipped by the quote/emphasis check entirely. A stray `#w;`/`#y;` is the signature of an occurrence marker that leaked into plain text during a sync from an older version of the script -- the element that carried it lost its `ref`/`xml:id`/`type`, and there is no safe way to re-derive them, so it is reported, never repaired. Note that whether `<q rend="italic">` (a genuine quotation) versus plain `<q>` (scare-quotes or a mentioned term) was the *correct* choice is never checked -- that call requires human judgment.

#rule-box(title: "THE ONE LEGITIMATE ASTERISK")[
  Every `*` is flagged, with a single exception: the scholarly marker of a reconstructed form (Proto-Indo-European _\*dyēws_). An asterisk is accepted when it is the very first character of a `<hi>`, `<term>` or `<foreign>` element that carries an `xml:lang`, and is followed by a letter -- `<hi rend="italic" xml:lang="ine-pro">*dyēws</hi>`. An asterisk anywhere else, or in an element with no `xml:lang`, is still reported.
]

== Spell Check

An English spell check runs as the last of the report-only checks, on every sheet. It shells out to `aspell` (`en_US`), extended with the project's own vocabulary; a missing `aspell` binary is reported once (`ERROR: aspell CLI not found on PATH -- spelling check skipped`) and the check is skipped, never aborting the run. Its findings are ordinary `[WARN]` lines -- they never block a build.

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Text that is checked*], [Every English text run in the document -- element text and the text following a child element alike -- *outside* the exemptions below.],
  [*Never checked: `<bibl>`*], [Bibliographic titles, names and publishers.],
  [*Never checked: `<name>`/`<term>`*], [Anything inside a `<name>` or `<term>`, first occurrence and `ref` mention alike: a term or name the author has deliberately tagged is vocabulary by definition, not a typo.],
  [*Never checked: foreign text*], [Any run whose nearest `xml:lang` (its own element's, else the closest ancestor's; text _after_ a child element belongs to the parent's flow) has a primary subtag other than `en` -- a `<foreign>`, a `<title xml:lang="fr">` such as _Le Rouge et le Noir_, a `<quote xml:lang="la">`. A foreign word that isn't tagged with an `xml:lang` *is* checked, and will be reported.],
  [*Never checked: fragments*], [Bare URLs; ordinal suffixes (`18th`); all-caps Roman numerals (`XIV`). Square brackets are dropped before tokenizing, so an editorial interpolation like `receiv[e]` reads as the one word "receive".],
  [*Possessives*], [A word ending in `'s`/`’s` is reported only if the part _before_ the `'s` is itself unrecognized -- `Inanna's` passes when `Inanna` is in the dictionary. `Peisistrato's` (a typo in the base) is still reported.],
  [*Reporting*], [Each distinct misspelling is reported once per file, at the line of its first occurrence, with its occurrence count -- a genuine corpus term recurs many times, and one dictionary entry clears all of them at once.],
)

*The project dictionary* is `custom_dictionary.txt`, in the same directory as the script: one word per line, exactly as it appears in the text (matching is case-sensitive, so `BDMB` does not also whitelist `bdmb`), with blank lines and `#`-led comment lines ignored. It is passed to aspell as a temporary personal word list (encoding declared as UTF-8, so accented entries survive). A missing file just means an empty project dictionary. This project's dictionary was seeded from the `Calneh - Nimrod` project's and is meant to be pruned to the words the manual actually uses.

== Pre-Validation Cleanup Passes

Three corpus-wide passes run before per-file validation, in this order. All three auto-correct and write the XML back to disk.

#fix-box[
  *Never-tagged-phrase stripping.* Any `<name>`/`<term>` outside a `<bibl>` whose key/id or displayed text matches a hand-maintained list of phrases that must never carry such a tag has its tag stripped entirely, leaving plain text. The list has these categories:
  - *Country/region names and demonyms* -- the modern ones (France/French, Germany/German, Spain/Spanish, Russia/Russian, China/Chinese, Japan/Japanese, the United Kingdom, the United States, ..., and regions such as "Middle East", "Central Asia", "Balkans", "Sub-Saharan Africa", "Europe"/"European"), plus three names a project has explicitly chosen to exclude although each could also denote an ancient polity: *Iran*, *India* and *Egypt* and *Egyptian*. Only the _bare_ name is excluded -- a more specific compound such as "Ptolemaic Egypt" stays a tracked entity. Anything else that could denote an ancient/classical polity (Greece, Rome/Italy, Persia, Christendom...) is deliberately _not_ on the list until it is named explicitly.
  - *Generic Bible-corpus divisions* -- "Old Testament", "New Testament".
  - *Generic titles* -- "king", used for dozens of individually tracked monarchs; in particular it must never resolve to an unrelated compound such as `Priest-King`.
  - *Modern events* -- World War II in each of its spellings: "World War II", "World War 2", "World War Two", "(the) Second World War", "WWII", "WW II", "WW2", "WW 2". Only the whole phrase is matched.
  \
  Log: ` [STRIP] {file}:{line}: removed <{tag}> tag around never-tagged phrase "{text}"`
]

#fix-box[
  *Literal quotes → `<q>`.* Every literal quote character (`"`, `“`, `”`) is paired up, per unit (paragraph, heading, table cell, footnote, ...), and each pair is wrapped in a new `<q>`, with the literal characters removed; any `<name>`/`<term>` sitting between them ends up inside the `<q>`. Deliberately conservative: a unit with an *odd* number of quote characters, or one whose quote sits nested inside a child element's own text, is left untouched, so the report-only check in section 2.2 can still flag it for a human rather than risk mispairing. Nothing inside a `<bibl>`, or already inside a `<q>`/`<quote>`, is touched -- so literal quotes inside a bare `<quote>` block (section 5) survive as typed. \
  Log: ` [QUOTE] {file}:{line}: wrapped literal quote "{text}" in <q>...</q>`
]

#fix-box[
  *First-occurrence relocation.* If an identity's defining tag (`xml:id`/`type`) sits at one point in the sequence but the same identity was mentioned bare, untagged, *earlier* in the sheet sequence, the definitional tag is moved to that earlier mention, and the original location is converted to `ref="..."` -- exactly as if it had been a normal duplicate. Sequence position is tracked corpus-wide across the whole file set, in file order, not by `sourceline` (a whole paragraph can share one source line). Tagging-dictionary strings are deliberately *not* used in this pass -- a partial name (a bare "Ishtar") must never be treated as a complete enough mention to relocate a definition onto. The contents of a `<figure>` (its caption) are not searched either: a caption is not running text, and a definition anchored there would be invisible. Because matching is case-insensitive, an ordinary word spelled like a name (an earlier "sin" for a deity "Sin") is a candidate for relocation too; the blocker of section 2.1 is what prevents it. \
  Logs: ` [RELOC] {file}:{line}: relocated first occurrence of "{text}" here (an earlier mention than its old tag)` and ` [RELOC] {file}:{line}: converted now-superseded first-occurrence tag (id="{ref_id}") into ref="{ref_id}"`
]

== The Build Log

Every line the script prints carries a bracketed tag, padded to a common width so the message text starts in the same column whatever the tag: `[FIX]` (an in-place correction of section 2.1), `[WARN]` (a report-only finding), `[STRIP]`, `[QUOTE]` and `[RELOC]` (the three passes above), `[SYNC]` (a `.typ` edit written back to XML), `[DICT]` (a surname added to the tagging dictionary -- section 2.1), `[SKIP]` (an output already up to date), `[TYPST]` and `[PDF]` (a `.typ` written / a PDF compiled, the latter suffixed `(FAILED)` when it isn't), `[LEXICON]` and `[CONC]` (the generated sheets), `[IMAGE]` (an image copied, downloaded, cropped or not found -- section 4.16), `[TOC]` (the table of contents of the front matter was rewritten -- section 4.18). A blank line follows a pass's output whenever it printed anything.

== Run Order

Every invocation of `python3 generate_book.py` runs, in this order:
+ Sync any hand-edited `.typ` file back into its `.xml` (see section 3) -- mtime-gated, so this only fires for files actually touched since the last generation. A sheet has one `.typ` per paper format; only one of them can be synced (section 4.17).
+ Strip never-tagged phrases.
+ Convert literal quotes to `<q>`.
+ Relocate misplaced first occurrences.
+ For each sheet, in sorted (i.e. numeric-prefix) filename order -- `Chapter 01.xml` through `Chapter 10.xml`: run the first-occurrence registry pass, then the report-only checks (spell check included), then emit a `.typ` + `.pdf` pair for *each paper format* (section 4.17) -- unless that format's PDF is already newer than its XML, its images and the script itself, in which case it is `[SKIP]`ped.
+ Write the tagging dictionary back if a surname was added to it (section 2.1).
+ Generate and emit the lexicon sheet (`Chapter 99 - Lexicon`).
+ Generate and emit the concordance sheet (`Chapter 98 - Concordance`), from the lexicon just written.
+ Merge the PDFs into the consolidated PDF (skipped when it is already newer than every input and the script).

The lexicon and concordance are _derived_ files: both are excluded from the set of source sheets, so they can never feed back into themselves as a source of first-occurrence keys or occurrences. So are the files listed in `NON_SHEET_XML_FILENAMES`, which take no part in any step above.

#rule-box(title: "THE SCRIPT REWRITES XML IN PLACE")[
  Steps 1 to 5 write their corrections straight back into the `Chapter NN.xml` files, and this project is not under version control. Before a run that will apply corrections, keep a copy of the files -- or run the script in a scratch copy of the directory first. A `[FIX]`, `[STRIP]`, `[QUOTE]` or `[RELOC]` line is the only record of what changed.
]

= XML ↔ Typst Round Trip

== The Marker Mechanism

Every leaf text unit emitted to Typst -- a paragraph, heading, table cell, quotation/transliteration/translation line, bibliography entry, lexicon headword/definition/cross-reference, or metadata field -- is wrapped in a matched pair of Typst comments:

```typst
/*rt7*/The actual rendered content goes here.../*rt/7*/
```

The number is a sequential id assigned in emission order, starting at 1. Typst strips `/* */` comments at compile time, so no marker is ever visible in the finished PDF. A parallel, in-memory list built during the same generation pass records which XML node each id's content came from -- since generation from an unchanged XML tree is fully deterministic, that id → node mapping is reproducible on demand and never needs to be persisted into the XML itself.

#rule-box(title: "MTIME IS THE ONLY SIGNAL")[
  Direction is decided purely by file modification time, checked before anything else runs. If a sheet's `.typ` file (of any paper format) is newer than its `.xml` file, the sync runs Typst → XML for that sheet *before* any validation touches it. If several formats' files hold edits, only the newest edited one is synced (section 4.17). After every regeneration, the `.typ` file's mtime is deliberately pinned back to match its `.xml` file's -- so only a hand-edit made *after* the last generation (which naturally bumps the `.typ`'s mtime forward) is ever picked up.

  A sheet whose PDF is already newer than both its XML and `generate_book.py` is not regenerated at all (`[SKIP]`): its `.typ` is neither rewritten nor recompiled. Editing the script itself therefore invalidates every sheet at once, since a script change can alter how anything renders.
]

== Typst → XML Sync Algorithm

When a `.typ` file is newer than its `.xml`:
+ Regenerate a fresh Typst rendering of the *current* XML, to get a fresh marker id → content map and a matching working copy of the tree.
+ Parse markers out of both the fresh render and the on-disk (hand-edited) `.typ` file.
+ Compare content id-by-id (whitespace-normalized); collect only the ids that actually differ.
+ If any differ, re-parse a *pristine* copy of the XML file (the working tree used for the fresh render had its `<bibl>`/`<cit>`/`<note>` whitespace collapsed for readability, and writing that back would flatten formatting for untouched nodes too) and apply each edit by XPath into that pristine copy.
+ Write the pristine, now-edited tree back to the `.xml` file.

Units whose content didn't change are never rewritten -- this is deliberate, so that repeatedly running the generator can never gradually erode a specific tag (e.g. a `<term>` or `<foreign>`) down to a generic `<hi>` just from re-running it.

== What a Hand-Edit Can Turn Into

Only the bounded set of inline constructs the forward (XML → Typst) pass itself emits is recognized when parsing an edit back into TEI markup:

#table(
  columns: (auto, auto),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Typst construct*], [*Becomes*],
  [`#emph[...]`], [`<hi rend="italic">`],
  [`#strong[...]`], [`<hi rend="bold">`],
  [`#smallcaps[...]`], [`<term>` (no `rend`)],
  [`#footnote[...]`], [`<note place="foot">` (a label trailing the closing `]` becomes its `xml:id`)],
  [`#fn_ref(<label>)`], [`<ptr target="#label">`],
  [`#w[...]` / `#y[...]` (an occurrence marker)], [a `<name>`/`<term>` -- not a new element: the call is matched, in document order, against the unit's *original* `<name>`/`<term>` descendants, and that original's tag and every attribute (`ref`, `xml:id`, `key`, `type`, `xml:lang`, `rend`) carry over onto the edited content, so a hand-edit elsewhere in the paragraph never strips a name back to bare text. A marker with no original left to match becomes a generic `<span>`.],
  [`#initial[X]` (the illuminated initial)], [the bare letter `X`: the wrapper is dropped, so the paragraph's text is unchanged. Deleting the wrapper in the `.typ` is harmless; the next build puts it back.],
  [`#text(...)[...]` with `"italic"` among its arguments], [`<hi rend="italic">`],
  [any other `#funcname[...]` call], [generic `<span>` -- structure is preserved even when the specific tag can't be recovered],
  [`"a quoted span"`], [`<q>`],
  [`\X` (a backslash-escaped character)], [the literal character `X`],
)

Everything else in an edited unit is kept as literal plain text.

#rule-box(title: "STRUCTURAL CHANGES NEVER SYNC BACK")[
  Adding or removing a section, changing an element's type or attributes, or editing anything *outside* a marker span (or adding new content with no marker at all) is invisible to the sync and simply ignored. Structural changes must still be made directly in the XML.
]

== Which Units Round-Trip

Marked (and therefore editable from the `.typ` side): title/subtitle/author/date metadata, the abstract, ordinary paragraphs, headings and `<speaker>` lines, plain list items, table cells, every line of a `<cit>` block (quote, transliteration, translation, trailing commentary), a figure's caption and each of its descriptive paragraphs, bibliography (`<listBibl>`) entries and the bibliography heading.

The generated lexicon and concordance sheets are *not* part of the round trip: their `.typ` files carry the same markers, but they are excluded from the sync pass along with the rest of the derived files, so a hand-edit made in `Chapter 99 - Lexicon.typ` or `Chapter 98 - Concordance.typ` is simply overwritten. Definitions are edited in the lexicon *XML* (section 4.12), which is where regeneration reads them back from.

Not independently marked: inline markup *inside* an already-marked unit. A `<name>`, `<term>`, or `<hi>` span inside a paragraph is captured as part of that paragraph's single marker -- it has no marker of its own, and is recovered (or not) as one of the bounded constructs listed above only when the whole paragraph is re-parsed.

Not marked at all, and so never round-tripped: anything the renderer emits *without* a `_mark` -- notably a bare `<quote>` block (section 5) and the image references of a `<figure>`. The front matter (section 4.18) is generated and not round-tripped at all.

= Everything Else

== XML Parsing

A literal `&nbsp;` (not a predefined XML entity) is rewritten to its numeric form `&#160;` before parsing, since libxml2's recovery mode would otherwise silently *drop* the entire entity reference -- deleting the intended non-breaking space rather than merely failing to resolve it. `XMLParser(recover=True)` is only invoked as a fallback, on a genuine syntax error.

== Whitespace Normalization

Every `<bibl>` -- or, if it sits inside a `<note>`/`<cit>`, the whole enclosing container -- has its internal text whitespace collapsed: runs of whitespace (including newlines) become a single space, and doubled spaces at the seam between two independently-collapsed fragments are de-duplicated. This exists purely so that pretty-printed XML indentation never leaks into the rendered PDF; it has no effect outside bibliographic structures.

== Italics and Script Rules

- A non-`<bibl>` element with a non-English `xml:lang` and no Greek/Hebrew characters in its text is treated as "roman-script foreign" -- this is what drives automatic italicization for foreign-language content written in Latin letters (Latin, French, romanized Sumerian, ...) without double-italicizing genuinely non-Latin scripts. This automatic italicization applies to `<hi>`, `<foreign>` and `<span>`; a `<name>` is italicized only by its own `rend="italic"`, whatever its `xml:lang` (section 1.2).
- A `<term>` renders small-caps when its `xml:lang` primary subtag is `ru`, `chu`, or `cu` (Russian / Old Church Slavonic), regardless of script. Otherwise it renders italic only when it explicitly carries `rend="italic"` *and* contains no Greek, Hebrew, or Cyrillic characters; everything else renders plain.
- `<foreign>` renders italic, accent-colored text when it's roman-script-foreign -- except inside a `<cit>`, where that automatic italicization is deliberately suppressed. The `<cit>` renderer italicizes its own lines by hand instead (see "Block Quotations" below): every `<quote>` and the translation, but never the transliteration.
- A bibliographic `<title>` renders as a quoted string when `level="a"` (article); anything else (book) renders `#emph[...]`. A `<title>` *outside* a `<bibl>` -- as the chapters use it for the title of a work in running text -- is not validated, and renders italic.

== Occurrence Markers

Every `<name>`/`<term>` is preceded by a tiny 4×4pt superscript square: accent-blue-bordered for a first/defining occurrence (it carries `xml:id`/`key`), light gray for a `ref`-pointing subsequent occurrence. A `<name ref="..." type="blocker">` -- text that merely collides with an identity rather than mentioning it -- gets *no* marker at all. The marker is followed by a Typst word-joiner (`#sym.wj`) so it can never be separated from its word by a line break -- earlier attempts to achieve this with an extra `box()` wrapper were rejected because *any* additional inline box, regardless of its own explicit size, was found to inflate the surrounding paragraph's line leading.

== Footnote Cross-References (`<ptr>`)

To cite the same footnote a second time elsewhere in the document -- reusing its existing number rather than duplicating its content under a new one -- give the defining `<note place="foot">` an `xml:id`, then point at it with `<ptr target="#id"/>` wherever it needs citing again. `<note xml:id="...">` renders as `#footnote[...]<id>` (a labeled Typst footnote); `<ptr target="#id"/>` renders as `#fn_ref(<id>)`, a preamble helper (`#let fn_ref(lbl) = context counter(footnote).at(query(lbl).first().location()).first()`) that looks up that footnote's number and prints it as a plain, normal-baseline number with ordinary word-spacing on both sides -- unlike an actual footnote mark, which sits superscript and flush against the preceding character. The id on the `<note>` itself must never carry the leading `"#"` -- that belongs only on the `<ptr>`'s `target`.

Footnotes are numbered automatically by Typst. A `<note>`'s own `n` and `type="footnote"` attributes, which the chapter files carry, are ignored -- and never flagged.

== Typst Preamble and Styling

The page size and margins come from the paper format being built (section 4.17), with page numbering `"1"`. Body text is "New Computer Modern" 11pt, justified, `leading: 0.75em`, `spacing: 1.2em`.

#table(
  columns: (auto, auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Name*], [*Value*], [*Used for*],
  [`accent`], [`#1e4d8c`], [headings, title block],
  [`q-bg`], [`#dbeeff`], [exchange-cycle question box fill],
  [`q-border`], [`#a9d7ff`], [rules, question box stroke, first-occurrence marker],
  [`muted`], [`#6b7280`], [subtitle, meta line, secondary captions],
  [`occurrence-gray`], [`#d1d5db`], [subsequent-occurrence marker],
)

Heading levels 1-4 each get their own weak `#v()` spacing and size/weight; levels 1 and 4 additionally get a `q-border`-colored rule underneath (full width for level 1, 35% width for level 4). In the shape the chapters use (section 4.7), a section heading is level 2 and every other heading level 3.

*The title block* is centered. A sheet's title is written `Project N — Sheet Name` in the XML -- for the book, `Chapter N — Chapter Name`. When it is plain text (no inline markup) containing exactly one em dash, it is split at the dash, which is dropped, into two lines: `Chapter N` at 12pt in the accent colour, then the chapter's name at 16pt bold. A title with inline markup, or without a dash, or with a second em dash, falls back to a single 16pt bold line. Below come the optional subtitle (12pt italic, muted) and the author · date line (9pt, muted), then a full-width rule. The block is not justified, so a long title that wraps is not stretched, and the whole block is a floating element placed at the top of the page (with 1.5em of clearance): floating figures go to the top of their page ahead of ordinary content, and without this a chapter's first figure would appear above its title. The author line is read from the text of `titleStmt/author` and the date line from `publicationStmt/date`; the chapters have neither, so both are empty. The whole title stays one round-trip unit, so only its plain content -- never the styling wrapped around it -- is what a sync sees.

*The illuminated initial.* The first letter of a chapter's opening paragraph is set in the decorative font `INITIAL_FONT` (ZallmanCaps) at `INITIAL_SIZE` (3.5em), through a `#initial[X]` wrapper defined in the preamble (falling back to New Computer Modern if the font is missing). The paragraph is chosen by `_choose_initial_paragraph`: in the first `<div>` under `<body>` whose `type` is in `INITIAL_DIV_TYPES`, the first `<p>` that comes before any nested `<div>` (that is, before the first section) and whose text starts with a letter. If that paragraph starts with markup, a quotation mark or a digit -- or the division has no introductory paragraph, as in the lexicon -- no initial is produced. The initial is a plain inline glyph, not a Typst drop cap: the letter simply sits on the first line, which grows taller to fit it (in the current chapters this changes no page count). At most one initial is produced per sheet. The wrapper is transparent to the round trip (section 3): an edit of the paragraph in the `.typ` reaches the XML without any trace of it. The running page header of the legacy pipeline (author on the left, title on the right, from the second page) is *not* reproduced, on purpose: the book's pages carry only a centered folio.

#rule-box(title: "SHEET NUMBERS DISPLAY WITHOUT THEIR LEADING ZERO")[
  A sheet's name is displayed without the zero padding its number may have in the XML title: `Chapter 04 — ...` reads *Chapter 4*, `Chapter 00` reads *Chapter 0*, and `Chapter 10` is unchanged (the chapter titles are already written unpadded, so this is usually a no-op). This applies wherever the _name_ is shown -- the title block, the PDF's document-title metadata, and the lexicon's "First defined in" line. It never applies to a _file_ name: `.xml`/`.typ`/`.pdf` names, the lexicon and concordance file names, and a `<ref target="...">` all keep their zero-padded `NN`, which is what keeps plain filename sort in chapter order.
]

A generated *concordance* sheet switches the whole document to landscape pages (`flipped: true`) rather than flipping each wide table individually (a wide table is one of `LANDSCAPE_TABLE_MIN_COLUMNS` or more columns, section 4.8).

== Document-Shape Dispatch

The document body renders in one of three mutually exclusive shapes, chosen by what's actually present *directly under `<body>`*:
- *Cycle format* -- `<div type="cycle">` children render as labeled "EXCHANGE N" question/answer exchanges, the question in an italicized, rounded, `q-bg`-filled box, the answer plain below it.
- *Report format* -- `<div type="section">` children (no cycles) render as a heading-plus-subsections research report: each section head at level 1 (`=`) and each `<div type="subsection">`'s head at level 2 (`==`).
- *Fallback* -- neither shape present directly under `<body>`: the body's block children render directly, recursively, with every `<head>` at *level 3* (`===`), except that the `<head>` of a `<div type="section">` is *level 2* (`==`).

*The chapters use the fallback.* A `<div type="chapter">` (like a `<div type="sheet">`) is a plain wrapper: it is not a cycle and not a section, so the sections inside it are never seen by the report format. The wrapper renders its block children in order -- its own introductory paragraphs, then each `<div type="section">` as another plain nested division -- and a section's `<head>` becomes a level-2 heading (14pt bold), while every other `<head>`, at whatever depth, becomes a level-3 heading (12pt regular). A `<div type="subsection">` inside a chapter's section is therefore a nested division whose heading is level 3, visibly subordinate to its section's.

A `<front><div type="abstract">`, if present, always renders first (centered, italicized) regardless of which shape follows; a `<back><div type="bibliography">`, if present, always renders last.

A `<div type="objection">` (the debate-cycle alias) renders its optional `<head>` one level deeper than its context, then each `<sp>` child in turn -- only the first (the question side) is italicized as a block; every later `<sp>` renders plain. `<speaker>` itself always renders as a level-3 heading.

Any element without a dedicated block rule (section 5) is flattened by the inline renderer into a single paragraph, *without* a round-trip marker; `<note>`, `<measure>` and `<teiHeader>` are skipped as blocks.

== Tables

Column count is the maximum number of cells in any row. A header row (`role="label"`) renders dark-blue-filled, white, bold text; in a table of more than 3 columns it is a repeating `table.header`, so it reappears when the table continues on the next page. A table with `LANDSCAPE_TABLE_MIN_COLUMNS` or more columns (6) gets its own landscape page at 9pt, non-justified; a narrower table renders inline in a plain block at the same 9pt, non-justified settings, and may then continue over a page break between two rows (Chapter 1's four-column table of cities does). In both, a row's cells are `breakable: false`, so a single cell never splits across a page. Cell stroke is `#bfd7f0` at 0.5pt. A `<table>`'s own `rows`, `cols` and `type` attributes, which the chapters carry, are ignored: the shape is derived from the cells. A table's `<head>` is not rendered as a caption.

== Lists

`type="ordered"` renders numbered (`+`); anything else renders bulleted (`-`). An `<item>` containing block-level children (`<head>`/`<p>`/`<list>`/`<table>`/`<figure>`) gets its own structured rendering -- a `<head>` child becomes a bold lead-in line, each `<p>` its own paragraph, with every continuation line re-indented so Typst's list parser doesn't leak raw source whitespace into the item. A plain `<item>` (no block children) renders as one inline-marked line. Attributes on an `<item>` are ignored.

Every list is closed by an empty `#[]` line at column 0. Typst measures a line's indentation *after* any leading comment, so without it the block following the list -- which opens with its `/*rtN*/` round-trip marker -- would count as indented and be absorbed into the list's last item (rendered indented, with no paragraph break). `#[]` renders nothing and lies outside every marker, so the `.typ` → XML sync ignores it.

== Block Quotations (`<cit>`)

A `<cit>` with no non-empty `<quote>` child is skipped. Otherwise it renders as a block with a 2em left inset, which never splits across a page. Its lines, in order:
+ every `<quote>`, in source order -- there may be one, or *two* (e.g. a French original followed by its English rendering, each in its own `<quote xml:lang="...">`); surrounding straight quote marks are trimmed, and *each is italicized*. Any `<quote>` whose `xml:lang` isn't English is preceded by its language name in a small muted bracketed label -- `[French]` -- so a reader can tell at a glance which line is the original. The label comes from a hand-maintained code table (`ae`, `akk`, `ar`, `de`, `egy`, `fr`, `grc`, `he`, `ine-pro`, `la`, `pi`, `pro`, `sa`, `sux`, `xhu`), falling back to the upper-cased raw code for a language not in it;
+ the transliteration `<note type="transliteration">`, if any, not italicized;
+ the translation `<note type="translation">`, if any, italicized.

A `<cit>` holding a single `<quote>` and nothing else reads as a plain block quotation: no hanging indent. Otherwise every line is its own paragraph with a 1em hanging indent, so a wrapped continuation indents but each line's own start stays flush. Between two consecutive `<quote>` lines the paragraph gap is widened from the block's default 0.3em to 0.5em, so an original-plus-translation pair doesn't read as tightly as same-language lines.

A `<bibl>` child becomes a footnote attached to the *first* `<quote>` line -- the original, never the translation. Any trailing `<p>` (commentary) renders below, smaller and non-italic, with no hanging indent.

A `<quote>` line is trimmed of surrounding whitespace before its boundary quote marks are removed, so a quote written with leading indentation is still recognized. Inside a quote, `<lb/>` renders as a line break, and consecutive `<l>` elements (the lines of an `<lg>`) are separated by line breaks: the lines of a verse quotation stay on their own lines inside the block. The line breaks are not among the constructs a hand-edit in the `.typ` is parsed back into (section 3.3), so a unit containing verse is best edited in the XML.

The block treatment applies *only* to a `<quote>` that is a child of a `<cit>`. A `<quote>` standing alone in a paragraph's place is not a block quotation to the script (section 5).

== Bibliography

`<listBibl>` entries render with a 1.5em hanging indent, non-justified, 0.65em leading, 10pt text. A `<back><div type="bibliography">`'s `<head>` renders as a level-1 heading directly above its `<listBibl>`. The chapters contain three `<bibl>` elements, all inside footnotes, and no bibliography.

== Lexicon Generation (`Chapter 99 - Lexicon`)

The lexicon is built from every *other* sheet's first-occurrence registrations. Entries are numbered from 1 (`n="..."`) and alphabetized into a single "All Entries" category by `xml:id`, so that a person's `LASTNAME-Firstname` key puts the last name first: _René Girard_ (`GIRARD-Rene`) files under G. The comparison ignores case, reads `_` and `-` as word breaks (`WEST-Martin_L` before `WESTENHOLZ-Aage`) and folds diacritics away -- _Männerbund_ sorts as _Mannerbund_, between "Mann..." and "Mao...", not after every plain-ASCII "M" word; the raw id breaks ties. The concordance uses the same order.

Each `<entry>` carries `n`, `xml:id`, `type` (the originating `<name>`'s `type`, or `"term"`), `xml:lang` when non-English, a `<form type="lemma"><orth>` (the lemma), one or more `<sense n="..."><def>`, and an `<xr type="first-occurrence">` whose `<ref target="...">` points back to the sheet where it was first defined, its text being that sheet's title (with the sheet number's leading zero stripped, see section 4.6).

#fix-box(title: "DEFINITIONS ARE NEVER CLOBBERED")[
  Every `<sense>`/`<def>` is read back from any *previous* version of the lexicon file before regeneration, and carried forward verbatim -- including a hand-added second or third `<sense>` disambiguating a word with several meanings, and any inline markup inside a `<def>` (a `<hi rend="italic">`, say), not just its bare text. Only a genuinely new entry gets the placeholder text "To be determined". Regenerating the lexicon never overwrites hand-authored definitions. The file is also left untouched on disk when its regenerated content is byte-identical to what is already there, so its modification time only moves when something actually changed.
]

#rule-box(title: "THE LEGACY GLOSSARY HAS BEEN MIGRATED, NOT LINKED")[
  Carry-forward only works on the lexicon's *own* `<entry>/<sense>/<def>` format, read from `Chapter 99 - Lexicon.xml`. The legacy `Chapter 99.xml` -- a `<list type="gloss">` of `<label>`/`<item>` pairs with hand-written definitions -- is a different format in a different file, and the script never reads it. Its definitions were therefore *migrated once* into the lexicon file, keyed by the `xml:id`s of the first-occurrence tags: 357 of its 361 entries, matched by name (345) or by decision (12, with Nanna and Sin merged into one entry with two senses), the three aliases and the never-tagged "Egypt" being dropped. Straight quotes in a definition became `<q>`, and `*italic*` markers `<hi rend="italic">`. From then on the lexicon file is the source of the definitions, and the legacy file is only a record. The generated lexicon has a different file name for a reason: pointing `LEXICON_FILENAME` at the legacy file would let a run overwrite every definition in it with the placeholder.
]

The lemma is the identity itself (underscores restored to spaces), *with any diacritic the ASCII-only `xml:id` had to drop put back from the tag's displayed text* wherever both spell the same word letter for letter: `xml:id="Mannerbund"` on _Männerbund_ gives the lemma "Männerbund", and `xml:id="Ahura_Mazda"` on _Ahura Mazdā_ gives "Ahura Mazdā". Apostrophes and other characters the id had to replace by `_` are restored the same way (`Narmer_s_Palette` on _Narmer's Palette_). A genuinely different spelling (an inflected form, a different phrase) is never "restored": an id `Kingdom_of_Mitanni` on the displayed text "Mitanni empire" gives the lemma "Kingdom of Mitanni", and a disambiguating id `Thebes_-_Egypt` gives "Thebes - Egypt". For an author-key-shaped identity the lemma is instead the person's *full name* as the book spells it: the longest displayed text of any `<name>` or `<author>` carrying the identity (blockers excepted) that contains the key's last name as a whole word (`GIRARD-Rene` → "René Girard"; `WESTENHOLZ-Aage` → "Aage Westenholz", although the entry is created by a later `<name>` reading only "Westenholz"). When no spelling contains the last name ("Diana", "Rousseauian"), the name is rebuilt from the key -- first name(s), then the last name capitalized: "Diana Broughton", "Jean-Jacques Rousseau", "Haimo of Auxerre" for the epithet `HAIMO_OF_AUXERRE-Haimo`, "Pliny" for the one-name `PLINY-Pliny`. A one-word name such as "Molière" or "Plutarch" is kept.

*Rendering.* A bold headword, additionally italicized and followed by a bracketed language name when non-English; then, in small caps, a *category label* derived from the entry's `type`. A single sense renders as `headword --- definition`, several as a numbered list; a "First defined in: _{source title}_" line follows when that cross-reference is available. A `<def>` is rendered through the normal inline renderer, so markup inside it survives into the PDF.

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*`type`*], [*Category label*],
  [`term`], [Term],
  [`place`], [Places and Physical Features],
  [`deity`], [Deities and Heroes],
  [`artifact`], [Artifacts and Buildings],
  [`people`], [Ethnic Groups and Sects],
  [`event`], [Historical Events and periods],
  [`ruler`], [Rulers and Dynasties],
  [`source`], [Primary Sources],
  [`prophet`], [Religious-Philosophical Figures],
  [`scholar`], [Scholars],
)

Note that the label for `people` is "Ethnic Groups and Sects", which is where the book's empires and states (section 1.2) are listed.

=== The Bibliography

The lexicon sheet ends with the book's bibliography: a `<div type="bibliography">` after the lexicon's `<div>`, holding a `<head>` and a `<listBibl>` of `<bibl>` entries. It is rebuilt on every run by `build_bibliography` from two sources:

- *Every `<bibl>` of the chapters that has a `<title>`.* A nested `<bibl>` (the work a quotation was taken from: `<bibl>... (1239), quoted in <bibl>Kantorowicz, ...</bibl>.</bibl>`) counts as a citation of its own. A `<bibl>` with no `<title>` (a scripture reference such as `1 Samuel 8:10-18`) cites no work and is left out.
- *Every `type="source"` identity of the lexicon.* Its entry carries `corresp="#key"` and is *hand-authored*: like a `<def>`, it is read back from the previous file and kept verbatim. A new source gets a default entry: its lemma as the `<title>` of an anonymous work or, for an author-shaped key (a person: `PROCOPIUS-Procopius`), as the `<author>`, with no title yet. Complete it in the XML (`<author ref="Plato">Plato</author>, <title level="m" rend="italic">Timaeus</title>.`). A source whose key is the author of another entry has no entry of its own: Herodotus is listed through his _Histories_, and Sîn-lēqi-unninni as the author of the _Epic of Gilgamesh_.

*Entries.* A chapter citation becomes an entry with no page reference: every `<citedRange>` goes, with the "p."/"pp."/"lines" in front of it, and so does any locator after the publication data (book and chapter numbers, a section title, a verse): the entry ends at the closing parenthesis of the imprint or, with no imprint, at its last title, volume, date or author. Only a `<ref>` link right after the imprint is kept. `type` attributes are dropped from `<author>`, and an `xml:id` becomes a `ref`, since the bibliography defines no identity. The first author's name is written last name first, as TEI `<surname>`/`<forename>` children (`<author ref="GIRARD-Rene"><surname>Girard</surname>, <forename>René</forename></author>`). The surname runs from where the key's last name starts to the end of the name (`DONIGER-Wendy` on "Wendy Doniger O'Flaherty" gives "Doniger O'Flaherty, Wendy"), and a "Jr." moves after the forename. A comma is added before a following "and" or "et al.". A key that is not `LASTNAME-Firstname`-shaped (`Augustine`, `Ibn_Fadlan`), a one-word name, or a name the key's last name cannot be found in ("Pliny the Elder" under `PLINY`) is left as written.

*One entry per work.* Two citations are the same work when they have the same first author and one title equals the other or is its short form (_Frederick II_ and _Frederick II: A Medieval Emperor_; "Vol. 1" and "Volume I" compare equal). They are also the same work when they have the same title, year and publisher, whatever the author's key: `LIVIUS-Titus` and `LIVY-Titus` cite one edition of _Ab Urbe Condita_. The author's last name is compared with underscores ignored, so `MAC_CANA` and `MACCANA` are one person. Of the duplicates, the most complete entry is kept: one with an imprint first, then with a date, then the longest. It inherits a source's `corresp`.

*Order.* By the first author's key, last name first, diacritics folded (`DE_WAAL-Frans` under D, `Ibn_Fadlan` under I). The works of one first author come alone first, then with each list of co-authors (by their keys, an "et al." group last), so works with exactly the same authors stay together. Within a group, entries go by title without a leading article. An anonymous work sorts by its title, and an institutional author (plain text before the first title, such as "U.S. Census Bureau") by its name.

*Rendering.* The bibliography starts on a new page, under a level-1 heading, as hanging-indent entries in 10pt. When an entry has exactly the same authors as the one before it (the same author keys, and an "et al." in both or neither), a 3-em dash (`———`) replaces their names, which are printed only in the first entry of the series: "Augustine of Hippo, _The City of God..._" then "———, _Confessions_". This is done only when rendering; the XML keeps every `<author>`. Its entries carry no round-trip marker: they are rebuilt from the chapters on every run, so a correction belongs in the chapter's `<bibl>`, or in the lexicon XML for a source entry.

== Concordance Generation (`Chapter 98 - Concordance`)

The concordance lists *every* occurrence of every lexicon identity -- the first, `xml:id`-bearing one and each `ref`-pointing later mention alike -- where the lexicon lists only the first. It is generated after the lexicon on every run, over the same entry set, in the same order and with the same category heading.

*What counts as an occurrence.* Every `<name>`/`<term>` in the (already validated, on-disk) sheets, in sheet order and then document order, whose id or `ref` is a lexicon identity -- except anything inside a `<bibl>`, `<teiHeader>` or `<title>`, and any `<name type="blocker">`.

*The context window.* Up to 5 words on each side of the occurrence, drawn from its nearest enclosing flow container (a paragraph, heading, list item, table cell, footnote, ...). If the container runs out first, the window is simply shorter. The exact original spacing and punctuation gluing the words to the occurrence are preserved -- an occurrence right after an opening parenthesis stays glued to it. A footnote is a floating aside, not part of the sentence it is anchored to: its own text never contributes context words, though the text continuing right after its marker still does.

Each occurrence is an `<xr type="occurrence">` holding `<ref target="{sheet file}.xml" n="{line}">` whose text is the context, the occurrence itself wrapped in `<hi rend="bold">`.

*Definitions are copied, not authored.* An entry's `<def>`s are copied from the lexicon file (`Chapter 99 - Lexicon.xml`) on every run, so the two sheets never drift apart. To change a definition, edit it in the lexicon; it flows into the concordance automatically.

*Rendering.* The whole document is set in landscape. Each entry shows its headword, category label and definition(s) exactly as in the lexicon, followed by a three-column table -- *Chapter* (the number from the target file name, leading zeros stripped from each part of a range, so `Chapter 04.xml` reads "4"), *Line*, and *Context* (occurrence in bold) -- with a blue header row that repeats across pages and non-breakable cells. The concordance is a cross-reference index rather than part of the series text, so it is *left out of the consolidated PDF*, and it is built in A4 only (section 4.17).

== PDF Compilation and Consolidation

Each sheet's `.typ` is compiled with the `typst` CLI; a missing binary or a compile error is reported without aborting the run.

*Even page counts.* Every compiled PDF that ends on an odd page count gets one blank page appended -- of the same page size as the sheet, so a landscape sheet pads with a landscape blank -- using `pdfinfo` to read the page count and size, `typst` to make the blank, and `pdftk` to append it. This keeps every sheet starting on a right-hand page once they are merged in sequence. A missing tool is reported, and the sheet's PDF is flagged `(FAILED)`.

*The consolidated PDFs*, one per paper format (`World History Consolidated_a4.pdf`, `World History Consolidated_ut.pdf`), are each assembled with `pdftk`, in this order:
+ the *title page* when it exists -- a hand-authored cover, never generated and never itself padded (it is distinct from the generated title page of the front matter, which comes next). It is looked up for the format first (`title page_a4.pdf`, `title page_ut.pdf`) and then under the plain name `title page.pdf`; a cover of the wrong size still merges, but a 6 x 9 in book wants its own;
+ each PDF named in `EXTRA_CONSOLIDATED_PDFS` (currently none), in the order listed, looked up the same way;
+ every remaining `*_a4.pdf` (or `*_ut.pdf`) in the directory whose name contains `Sheet NN` *or `Chapter NN`* (or `NN-MM` for a multi-sheet file), in plain lexicographic order -- which puts the generated front matter (`Chapter 00_a4.pdf`) first, then the chapters, and includes the generated lexicon, and excludes the concordance and the consolidated PDF itself. A format's book therefore never contains another format's pages.

Any other PDF lying in the directory is ignored unless it is listed as an extra -- which covers this document's own PDF, whose name contains neither pattern. Each merge is skipped (`Consolidated PDF up to date`) when the consolidated PDF is already newer than every input and than the script itself; a missing `pdftk` or a merge error is reported without aborting.

== File Naming and Invocation

Any `*.xml` file in the script's own directory is treated as a source sheet, *except* the two generated ones -- the lexicon and the concordance (`Chapter 99 - Lexicon.xml`, `Chapter 98 - Concordance.xml`) -- and any file named in `NON_SHEET_XML_FILENAMES` (`Chapter 00.xml`, `Chapter 99.xml`). Each sheet's `.typ`/`.pdf` share its XML file's stem, followed by the paper format: `Chapter 04.xml` gives `Chapter 04_a4.typ`, `Chapter 04_a4.pdf`, `Chapter 04_ut.typ` and `Chapter 04_ut.pdf`. Sheet ordering -- for both first-occurrence tracking and PDF consolidation -- is plain lexicographic filename sort, which is why chapter numbers are zero-padded in *file* names ("Chapter 04", not "Chapter 4"), even though the *displayed* name drops the padding (section 4.6). "First occurrence" means first in this order: chapter 1 before chapter 2, and so on.

The script takes one optional flag:
```sh
python3 generate_book.py [--sub]
```
`--sub` also lists each section's subsections in the table of contents (section 4.18); without it, only chapters and sections are listed.
It must be run from the directory containing the XML files. It needs `typst`, `pdftk` and `pdfinfo` on the `PATH`, plus `aspell` for the spell check (each reported, not fatal, when missing), and, optionally, Pillow (`PIL`) for image cropping and sizing. Because every `*.xml` beside the script is a source sheet, a stray copy or backup left in the directory (`backup.xml`) is validated, rewritten and put in the lexicon like any other sheet -- keep copies in a sub-folder.

=== Constants

The block between the `PER-DIRECTORY CONSTANTS` banners at the top of the file holds everything specific to this project. The banners are inherited from the parent script; since `generate_book.py` is no longer kept identical to other copies, the block is simply where the project's settings live.

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Constant*], [*Value*],
  [`CONSOLIDATED_PDF_NAME`], [`World History Consolidated.pdf` (the base name: each format's book is `World History Consolidated_a4.pdf`, ... `_ut.pdf`)],
  [`TITLE_PAGE_PDF_NAME`], [`title page.pdf` (base name; `title page_a4.pdf`, `title page_ut.pdf` are tried first)],
  [`PAPER_FORMATS`], [`a4` and `ut` (section 4.17)],
  [`CONCORDANCE_FORMATS`], [`a4` only (section 4.17)],
  [`EXTRA_CONSOLIDATED_PDFS`], [none],
  [`LEXICON_FILENAME`], [`Chapter 99 - Lexicon.xml`],
  [`CONCORDANCE_FILENAME`], [`Chapter 98 - Concordance.xml`],
  [`_LEXICON_TITLE`], [`Chapter 99 — Lexicon and Bibliography`],
  [`_CONCORDANCE_TITLE`], [`Chapter 98 — Concordance`],
  [`NON_SHEET_XML_FILENAMES`], [`Chapter 00.xml`, `Chapter 99.xml`],
  [`FRONT_MATTER_FILENAME`], [`Chapter 00.xml` (section 4.18)],
  [`LANDSCAPE_TABLE_MIN_COLUMNS`], [`6`: tables with at least this many columns get their own landscape page (section 4.8)],
  [`INITIAL_FONT`], [`ZallmanCaps`, the decorative font of the illuminated initial; an empty string switches the feature off (section 4.6)],
  [`INITIAL_SIZE`], [`3.5em`, the size of the initial],
  [`INITIAL_DIV_TYPES`], [`("chapter",)`, the kinds of division whose opening paragraph receives an initial],
)

The dictionary paths (`custom_dictionary.txt`, `tagging_dictionary.json`) are not constants: both are always resolved next to the script.

== Figures and Images

A `<figure>` is rendered as a Typst `#figure`: the image, an automatically numbered caption ("_Figure 3: ..._", in 9.5pt italic, centered), and any descriptive text. Figures are numbered per document, so each chapter starts again at 1.

*Content.* The images are the `<graphic>` children; the caption is the `<head>` (falling back to a `<figDesc>`); each `<p>` or `<list>` child is a descriptive block, set in 9.5pt justified text directly under the caption, outside the `#figure` so that it can break across a page. The caption and every descriptive paragraph are round-trip units (section 3.4); occurrence markers appear in them like anywhere else. A `<figure>` with no `<graphic>` is drawn as a bare caption from its `<figDesc>`, as before.

*Layout.*
- A single image is centered, its width the largest percentage (at most 90%) that keeps its height within *33% of the text height* -- so if the figure does not fit in what is left of a page, the gap left behind is at most a third of a page. The text area is that of the paper format being built: 155mm by 237mm on A4, 114.4mm by 183.6mm on US Trade.
- *Two* images go side by side, at one common height, when each would be at most half the column wide at that height cap (and the pair's total width fits); otherwise they are stacked at the narrower of their two widths.
- An image that is missing is drawn as a labelled grey box ("Image not available"), so the layout is unaffected.
- A figure floats to the top of a page (`placement: auto`) unless it has descriptive text, which must stay glued to it. Pillow reads image sizes; for an SVG, or without Pillow, the width is simply 90%.

*Where the images come from.* Images live in the `images/` folder beside the script; a page's Typst refers to them as `images/NAME`. For each `<graphic url>` the script resolves the file in this order, stopping at the first success:
+ a *local path* (`./images/x.jpg`) -- used as it is, and copied into `images/` first if it lies elsewhere;
+ the *registry* `images/.download_registry.json` (URL → file name, shared with the legacy pipeline), or the file name derivable from the URL, when that file is already on disk -- no network;
+ for a Wikimedia URL, the *canonical URL* from the Commons API, if that file is already on disk;
+ a *download* of the URL, then of the canonical URL;
+ a Commons *search by the file-name stem*, for a URL whose file name is outdated or shortened;
+ a Commons *search by the caption* -- a *substitute* image, which is always announced in the log, and should be checked by eye.

A downloaded or copied image is cropped in place when its four corners share a uniform colour and cropping saves at least 5% of its area (Pillow required), and its URL is recorded in the registry. A stale registry entry (the file is gone) is dropped and the image fetched again. Within one run an image is looked up once: a failure is not retried.

The build log reports only *actions* -- `[IMAGE]` lines for a copy, a download, a crop, a search, a substitute, or a failure (marked `(FAILED)`). A cache hit is silent, so a build with every image already present prints no `[IMAGE]` line at all and makes no network call.

#rule-box(title: "IDENTIFY YOURSELF TO WIKIMEDIA")[
  Downloads send the `User-Agent` in `_IMG_HEADERS`. Wikimedia asks every client to include a contact address; the script ships with a generic value and none. Put one there (for example `generate_book/1.0 (educational use; contact: you@example.org)`) before downloading many images, or requests may be throttled.
]

An image file that changes makes every sheet using it stale, so the sheet is rebuilt (section 2.6).

== Paper Formats

Every sheet is built in each format of `PAPER_FORMATS`, a per-directory constant. Two are defined, in this build order:

#table(
  columns: (auto, 1fr, 1fr, 1fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Key*], [*Page*], [*Margins (mm)*], [*Text area (mm)*],
  [`a4`], [A4, 210 x 297], [top 30, bottom 30, left 30, right 25], [155 x 237],
  [`ut`], [US Trade, 6 x 9 in (152.4 x 228.6)], [top 20, bottom 25, left 22, right 16], [114.4 x 183.6],
)

The margins are those of the legacy pipeline: a wider left than right margin on A4 and on US Trade alike, for binding. To change a format, edit its entry; to stop building one, delete it (a sheet's other format, its consolidated book and the round trip then follow). All measures live in the table, in mm: the Typst page setup, the sizing of figures and the padding of the PDFs are computed from it.

*Files.* Each format has its own `.typ` and `.pdf` per sheet -- the chapters and the lexicon -- named `NAME_a4` and `NAME_ut`, and its own consolidated book (section 4.14). The one exception is the concordance: a reference index that is not part of the book, it is built only in the formats listed in the constant `CONCORDANCE_FORMATS`, currently `a4` alone, so there is no `Chapter 98 - Concordance_ut` file. (A file left over from an earlier build is not deleted.) Only the page setup differs between the two `.typ` files of a sheet: their content, and so their round-trip markers, are identical.

*What follows the format.*
- *The page setup*: size, margins and, for the concordance, a landscape orientation. A wide table (`LANDSCAPE_TABLE_MIN_COLUMNS`, six, or more columns) is set on its own landscape page, which on US Trade is 9 x 6 in.
- *Figures* are sized against the format's text area (section 4.16), so an image that fits an A4 column is not too wide or too tall for a 6 x 9 in one, and a side-by-side pair is only formed when both images fit that narrower column.
- *The even-page padding* uses each PDF's own page size, so a blank page appended to a US Trade sheet is 6 x 9 in.
- *Page numbers* are continuous across the book in each format (section 4.18); the concordance, which is not part of the book, is numbered from 1.

Fonts, type sizes, spacing and hyphenation are the same in both formats; only the page changes.

*The round trip with two `.typ` files.* Either format's `.typ` may be hand-edited, and an edit made in the US Trade file reaches the XML, and through it the A4 file, exactly as one made in the A4 file does. But the two files of a sheet cannot both be synced in one run: the second would be compared with the already-updated XML and would undo the first's edits. So only a `.typ` that actually holds an edit counts -- merely touching a file changes nothing -- and, if several do, the *newest* is synced and each of the others is reported (`[WARN] ... was also edited but is not synced back`). Its edits are then lost, because the sheet is regenerated from the updated XML. Edit one format's `.typ` at a time, or make the change in the XML.

== Page Numbering and the Front Matter

*Book-wide numbering.* The sheets of the book -- the chapters in order, then the lexicon -- are numbered continuously in each paper format, as in the printed book. The first sheet starts at page 1, and each next one on the page after the previous one's last, its blank padding page included, so every sheet starts on a right-hand page (the front matter is even in length, see below). The start page is written into each sheet's `.typ` as `#counter(page).update(N)`, together with a comment `// book-start-page: N`. The concordance is not part of the book and is numbered from 1.

A sheet's start page depends on how long all the sheets before it are. The sheets are therefore built in order, each one after the one it follows, and a sheet is rebuilt not only when its own inputs change but also when its `.typ` was made for a different start page (the marker comment says which). If one chapter grows by a page, that chapter and every later one are rebuilt and renumbered in a single run; the earlier ones are skipped.

*Section pages.* Every level-2 heading -- in the chapters, exactly the section headings (subsection headings are level 3 and carry a `<subsection-page>` marker instead, see below) -- carries an invisible `<section-page>` marker, placed in the same paragraph as the heading text so it can never be left on the previous page when the heading moves to the next. After a sheet is built, the script asks `typst query` for the page of each marker and matches them, in order, to the sheet's `<div type="section">` divisions. A sheet that is not rebuilt keeps the section pages recorded in the table of contents by the previous run, so `typst query` runs only for rebuilt sheets. If the number of markers and of sections disagree, a `[WARN]` says so and the missing sections are given the sheet's first page.

*Lexicon and bibliography pages.* The table of contents lists `Chapter 99 - Lexicon` with two sections, its lexicon and its bibliography. Their level-1 headings carry a `<part-page>` marker instead of a `<section-page>` one, so the lexicon's "All Entries" heading is not listed; the script queries that label for a sheet that has a `<div type="lexicon">`.

*Subsection pages (`--sub`).* Every subsection heading -- the `<head>` of a `<div type="subsection">` -- always carries an invisible `<subsection-page>` marker inside its body, outside the round-trip marker, so the `.typ` is the same with or without the flag and the sync never sees it. With `--sub`, the script also queries those markers and matches them, in order, to the subsections of each section; the table of contents then lists them under their section, indented and in a smaller size, and `Chapter 00.xml` records them as a `<list type="bulleted">` nested in the section's `<item>`, with the same `page_<format>` attributes. A recorded table of contents is reused for a sheet that is not rebuilt only if its subsections match too, so switching the flag on or off re-queries every sheet. A count mismatch is reported by a `[WARN]`, and the missing subsections are given their section's page.

*The table of contents.* On every run, after all the sheets are built, the script rewrites the `<div type="toc">` of `Chapter 00.xml` (the constant `FRONT_MATTER_FILENAME`) from the recorded layout: one `<item>` per sheet -- the chapters, then the lexicon -- holding its title, and a nested list of its section titles, each with one attribute per paper format (`page_a4="31"`, `page_ut="47"`). Only the `<div type="toc">` block is replaced: the `<teiHeader>` and every other division of the `<body>` (a `<div type="foreword">`, ...) are preserved exactly as they are, and the table of contents keeps the file's indentation (chapter items at 10 spaces, their section lists at 12, section items at 14). The file is rewritten, and `[TOC]` logged, only when the content changed. A section title in the table of contents is its heading's plain text, with the quotation marks of a `<q>` kept.

*The front matter.* `Chapter 00.xml` is then rendered, in each format, as `Chapter 00_a4.pdf` and `Chapter 00_ut.pdf`: an unnumbered *title page*, the *table of contents* and, when the `<body>` has a `<div type="foreword">`, the *foreword*, padded to an even number of pages. The table of contents and the foreword are numbered in small-cap roman numerals (the title page counts as page i but shows no number). The foreword is set like a chapter, on the next right-hand page after the table of contents: the chapter title block with its `<head>` as the title, the chapter styles, and the illuminated initial on its first paragraph. It is listed first in the table of contents, with its roman page number, which Typst reads from the `<foreword-start>` label at layout time (it is not recorded in the XML table of contents). `Chapter 00.xml` is not a source sheet, so names and terms in the foreword are not tracked by the lexicon. The title page has the title (scaled to fill 90% of the text width, in the accent colour), the subtitle, the author -- the `<persName>` of the header's `<author>`, without its `<roleName>` -- and, at the foot, one line for each child of the header's `<publicationStmt>` (publisher, place, date, availability). The table of contents lists each sheet's title split at its first dash into a bold line with the page number (`Chapter 4`) and an italic subtitle (`Ancient Egypt – ...`), then its sections, indented, each with its page number; a wrapped section title hangs under its first line. The lexicon appears as "Chapter 99, Lexicon".

The front matter sorts first among the chapters' PDFs, so it opens each consolidated book, after the hand-authored cover if there is one (section 4.14). It is generated only: a hand-edit of `Chapter 00_a4.typ` is overwritten, and changes are made in `Chapter 00.xml`'s header. It is rebuilt when `Chapter 00.xml` or the script changes.

= What the Script Does Not Handle

The chapter files were written for a different renderer, the legacy print pipeline `tei_to_typst.py`, which also produced the drop caps and a running page header. Its image handling, paper formats, page numbering, table of contents and illuminated initial have been ported (sections 4.6 and 4.16 to 4.18); its running header was deliberately left out. `generate_book.py` inherits its vocabulary from the sheet-based project, so several constructs the chapters use have no rule of their own. Nothing here is an error: the script neither warns about these nor stops. Counts are for Chapters 1-10 as of 2026-09-21. (Figures used to be on this list; they are now rendered, see section 4.16.)


#table(
  columns: (1.15fr, 1fr, 2.85fr),
  stroke: 0.5pt + rgb("#cbd5e1"),
  inset: 6pt,
  [*Construct*], [*Where*], [*What the script does*],
  [`<quote>` outside a `<cit>`], [none at present (the 15 that existed are now `<cit>` blocks)], [Not a recognized block. Its content is flattened by the inline renderer into an ordinary, unstyled, *unmarked* paragraph: no indent, no block-quote treatment, and no round-trip marker, so a hand-edit in the `.typ` is not synced back. Names, terms and spelling inside it are still processed like any other text; literal quote characters inside it are left as typed (section 2.4).],
  [`<lg>` / `<l>` (verse lines), `<lb/>`], [Chapters 2 and 4, inside `<cit>` quotations], [Supported as line breaks inside running text and block quotations (section 4.10). Outside a quotation they are not given a block of their own.],
  [`<div type="subsection">`], [Chapter 5 (one per section of the source sheets)], [Inside a chapter wrapper, its heading is level 3, under its section's level-2 heading, and is not listed in the table of contents; in the report format it is level 2 (section 4.7).],
  [`<list type="gloss">`, `<label>`], [`Chapter 99.xml` only], [No rule; the file is excluded from the source set.],
  [`<div type="toc">`], [`Chapter 00.xml` only], [No rule in the sheet renderer: the file is not a sheet. It is rendered by the front-matter step (section 4.18).],
  [`<note n="..." type="footnote">`, `<table rows cols type>`, `<item chapter page>`], [Chapters 1-10, and the excluded files], [The extra attributes are ignored and never flagged (sections 4.5, 4.8, 4.9).],
  [Page-number cross-references, running page header], [--], [Features of `tei_to_typst.py` only (its paper formats, page numbering, table of contents and drop cap have been ported, sections 4.6, 4.17 and 4.18). Text cannot refer to a page number: the numbers exist only in the table of contents. The running header (author left, title right, pages after the first) is not reproduced by decision; pages carry a centered folio only.],
)

The table of contents and the glossary that the legacy pipeline generates (`Chapter 00.xml`, `Chapter 99.xml`) are not reproduced by this script: the lexicon of section 4.12 is its replacement for the glossary, and the concordance of section 4.13 replaces the glossary's page references with occurrence lists.
