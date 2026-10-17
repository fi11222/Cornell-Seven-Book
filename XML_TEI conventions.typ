#set page(
  paper: "a4",
  margin: (x: 2cm, y: 2.5cm),
  header: align(right)[
    #text(8pt, weight: "light", fill: gray)[TEI Encoding Guidelines • Sovereignty Engine Project]
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

// Custom warning/rule block
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
  #block(text(weight: 700, 24pt, fill: rgb("#0f172a"))[TEI XML Encoding Conventions])
  #v(0.5em)
  #text(14pt, fill: rgb("#475569"))[Sovereignty Engine Framework]
  #v(2em)
]

= Introduction
This document defines the strict TEI (Text Encoding Initiative) XML conventions used for digitizing the analytical summary sheets of the Sovereignty Engine framework. It serves as a reference for structuring metadata, hierarchical divisions, bibliographic citations, and semantic inline tagging.

= The "First Occurrence" Paradigm
The most important convention in this project governs how domain-specific vocabulary and proper nouns are tagged. 

#rule-box(title: "THE FIRST OCCURRENCE RULE")[
  Semantic tags such as `<term>` and `<name>` must be used DIFFERENTLY for the first occurrence of a term or name in the entire sheet sequence (from 1 to 24), compared to the subsequent ones. 
  
  The *first occurrence*, which determines at what point a name or term "enters the lexicon" must possess the `xml:id`, `xml:lang` (if any) and `type` attributes associated with the definition (see below for details). All subsequent use must possess the `ref` attribute, pointing to the `xml:id` of the first occurrence, and no `xml:lang` or `type` attributes.
]
The differentiated use of `xml:id` vs. `ref` also applies to `<author>` tags (see section 5 below)

= Semantic Inline Tagging
== The `<name>` Tag
Used for the first occurrence of proper nouns and know artifacts like the "Uruk Vase", "Kheops Pyramid", etc. 
- *Attributes required:* 
  - `xml:id`: The normalized, capitalized name of the entity except for modern scholars ID which are of the form: "LAST_NAME-First_Name" (spaces replaced by underscore, last name in all-caps, first name capitalized)
  
  - `type`: Must be one of:
    - `place`: Places and physical features (Rivers, Mountains, Seas, etc). Label: "_Places and Physical Features_"
    - `deity` : Deities and heroes. Label: "_Deities and Heroes_"
    - `artifact`: Artifacts and buildings. Label: "_Artifacts and Buildings_"
    - `people`: All kinds of ethnic groups, e.g. "Sythians", "Gutians", etc. Label: "_Peoples and Ethnic Groups_"
    - `event`: Battles, revolutions, coronations, etc. Label: "_Historical Events_"
    - `ruler`: Rulers, nobles and their kin. Includes dynasties like "Ur III" or "the Capetians". Label: "_Rulers and Dynasties_"
    - `source`: Either the name of a primary source (e.g. "Enuma Elish") or an ancient, possibly mythical author (e.g. "Homer"). Label: "_Primary Sources_" 
    - `prophet`: Prophets and other religious-philosophical figures (Including Plato, Socrates, or even Karl Marx). Label: "_Prophets and Religious-Philosophical Figures_"
    - `scholar`: Modern Academic scholars. Label: "_Scholars_"
  - `xml:lang`: Included if the name is explicitly foreign (e.g., Sumerian `sux`, Akkadian `akk`, etc.)

```xml
<!-- First occurrence of Uruk (Place) -->
<name key="Uruk" type="place">Uruk</name>

<!-- First occurrence of a foreign deity name -->
<name xml:lang="sux" key="Inanna-Ishtar" type="deity">Inanna</name>

<!-- First occurrence of an artifact/building -->
<name key="Anu Ziggurat" type="artifact">Anu Ziggurat</name>
```

#rule-box(title: "NEVER-TAGGED GENERIC WORDS")[
  Certain broad, generic words must never be wrapped in a `<name>` or `<term>` tag, even when they clearly denote the right semantic category (`type="place"`, etc.) or happen to share a hyphen-segment with a legitimately tracked compound entity. They are left as plain text throughout, with no `xml:id`/`ref` entity tracking.

  *Countries and regions:* *West* (as in "the West"/"Western"), *India*, *Europe* / *European*, *Iran*, *Egypt* / *Egyptian*.

  *Religions:* *Islam*.

  Note: this does not apply to a more specific compound entity built on the same word, which remains a tracked entity of its own -- e.g. "Indo-European" (`ref="Proto-Indo-European"`), "Indo-Iranian" (`ref="Indo-Iranians"`), the place "Iranian Plateau", "Ptolemaic Egypt" (`ref="Ptolemaic_Egypt"`), or "Islamic Kingship" (`ref="Islamic_Kingship"`). Only a bare, standalone use of the excluded word itself (e.g. plain "Egypt"/"Iran" as the country, "European" as a plain regional adjective, plain "Islam" as the religion) is excluded.

  *Generic titles/common nouns:* *King* -- a common royal title used for dozens of distinct, individually-tracked monarchs throughout the manuscript, not itself a trackable entity (e.g. it must never resolve to some other compound term like "Priest-King" just because they share a hyphen-segment).
]

== The `<term>` Tag
Used for the first occurrence of domain-specific concepts, theoretical framework vocabulary, or important foreign nouns.
- *Attributes required:*
  - `xml:id`: The normalized concept name (e.g., "Hieros Gamos"). Should be the lemma of the word if a conjugated or declined form is inside the tag
  - `rend="italic"`: If the term is foreign or requires emphasis.
  - `xml:lang`: If the term is foreign (e.g., `la`, `grc`, `sux`, etc.).

```xml
<!-- Framework concept -->
<term key="Overt Ishtarism">Overt Ishtarism</term>

<!-- Foreign concept requiring italics -->
<term xml:lang="grc" key="Hieros Gamos" rend="italic">hieros gamos</term>

<!-- Latin concept -->
<term xml:lang="la" key="Deus Otiosus" rend="italic">deus otiosus</term>
```

== The `<hi>` tag

use `<hi rend="italic">` for emphasis, never a star or any other non TEI marker. The title of a work (book, article, journal, film, etc.) is never a `<hi>`, not even in running text: use `<title>` with its `level` and `rend` attributes (see the Bibliographic Structure section).

= Structural Tags
== Divisions (`<div>`)
The body of the document is divided into sheets and sections using the `<div>` tag.
- *Sheet Level:* The root container for a single analytical topic.
  - Attributes: `type="sheet"`, `n="[Number]"`, `xml:id="[Normalized_Title_String]"`.
- *Section Level:* Sub-sections within a sheet.
  - Attributes: `type="section"`, `n="[Number]"`.

```xml
<div type="sheet" n="1" xml:id="Ziggurat_E-Anna_Sheet_01_Sumer-Akkad">
  <head>Sumer/Akkad (Original Uruk & Sacred Precincts)</head>
  <head type="sub">How it fits into the Anu Ziggurat/E-Anna Model</head>
  
  <p>Introductory text...</p>
  
  <div type="section" n="1">
    <head>The Anu Ziggurat</head>
    <p>Section text...</p>
  </div>
</div>
```

= Citations and Quotations

All `<author>`tags described in this section must include an `xml:id` (or `ref`, see section 2) attribute with a key of the form: `LAST_NAME-First_Name` (spaces replaced by underscore, last name in all-caps, first name capitalized). The one exception is a prophet, flagged `type="prophet"` on the `<author>`, whose key may follow the ordinary capitalized-name form instead (`<author xml:id="Benedict_of_Nursia" type="prophet">`). Multiple authors get their own tags each. If an individual appears in a `<name type="Prophet">` or in a `<name type="Scholar">`, as well as in an `<author>` tag, the keys used in `xml:id` and `ref` must be kept consistent throughout and `xml:id` must be used only for the first occurrence, regardless of whether it is a `<name>` or `<author>` tag that appears first.

== Block Quotations (`<cit>`)
For long quotes accompanied by a bibliographic reference, wrap the entire structure in a `<cit>` (citation) tag. Inside, use `<quote>` for the text and `<bibl>` for the source (see below).

```xml
<cit>
  <quote>Anu is the father and king of the gods...</quote>
  <bibl>
    <author xml:id="JACOBSEN-Thorkild">Thorkild Jacobsen</author>, 
    <title level="m" rend="italic">The Treasures of Darkness</title> 
    (<pubPlace>New Haven</pubPlace>: 
    <publisher>Yale University Press</publisher>, 
    <date when="1976">1976</date>), 
    p. <citedRange unit="page">95</citedRange>.
  </bibl>
</cit>
```

For a quote originally in another language, give the original and its English rendering as two `<quote>` tags in source order (original first), each carrying its own `xml:lang`. The non-English `<quote>` is automatically labeled with its language in the rendered view, so no extra markup is needed beyond the `xml:lang` attribute.

```xml
<cit>
  <quote xml:lang="fr">C'est comme un guet que Dieu a mis par-dessus l'homme...</quote>
  <quote xml:lang="en">It is like a watchman that God has set over man...</quote>
  <bibl>
    <author xml:id="CALVIN-John">John Calvin</author>,
    <title level="m" rend="italic">Institution de la religion chrestienne</title>
    (<date when="1560">1560</date>), IV.10.3.
  </bibl>
</cit>
```

== Inline Quotations (and other uses of`<q>`)
For short, inline quotes without an immediate, integrated `<bibl>` block, use the `<q>` tag with the `rend="italic"` attribute.
```xml
Leick notes, <q rend="italic">Inanna is the only deity...</q>
```
`<q>` must also always be used outside of quotations instead of single or double quotes (without the `rend="italic"` attribute).

== Footnotes (`<note>`)
Use `<note place="foot">` for footnotes. They usually contain text explaining the reference, accompanied by a `<bibl>` tag for the structural bibliographic data (see below).

```xml
<note place="foot">
  <bibl>
    <author key="LEICK-Gwendolyn">Gwendolyn Leick</author>, 
    <title level="m" rend="italic">Sex and Eroticism...</title> 
    (London: Routledge, 1994), p. 58.
  </bibl> 
  Leick highlights how Inanna operates outside domestic fertility...
</note>
```

= Bibliographic Structure (`<bibl>`)
Whenever a source is cited, the `<bibl>` tag should be strictly structured with the following child tags (where applicable):
- `<author>`: Must include an `xml:id` (or `ref`, see section 2) attribute in the format `LASTNAME-Firstname`, unless it carries `type="prophet"`, in which case any ordinary key is allowed. Multiple authors get their own tags each.
- `<title>`: Must include `level="m"` (for monograph/book) or `level="a"` (for article), and `rend="italic"` (for books).
- `<pubPlace>`: City of publication.
- `<publisher>`: Name of the publishing house.
- `<date>`: Must include the `when="YYYY"` attribute, with `YYYY` as four-digit year.
- `<citedRange>`: Must include the `unit="page"` attribute.

A citation written inline in a footnote (or in running text) gets the same `<bibl>` structure; the people and places in it are never `<name>` tags, since `<bibl>` content is not scanned for mentions. Editors and translators (`ed.`, `trans.`) and institutional authors (e.g. a government agency) stay plain text inside the `<bibl>`. `level="j"` (with `rend="italic"`) marks a journal or series. Scriptural references (`<bibl>Matthew 14:19-21</bibl>`) need no `<title>`.

```xml
<bibl>
  <author xml:id="ASHER-GREVE-Julia">Julia M. Asher-Greve</author> and 
  <author xml:id="GOODNICK_WESTENHOLZ-Joan">Joan Goodnick Westenholz</author>, 
  <title level="m" rend="italic">Goddesses in Context</title> 
  (<pubPlace>Fribourg</pubPlace>: 
  <publisher>Fribourg Academic Press</publisher>, 
  <date when="2013">2013</date>), 
  pp. <citedRange unit="page">45-50</citedRange>.
</bibl>

```