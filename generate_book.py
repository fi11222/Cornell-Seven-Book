#!/usr/bin/env python3
"""
generate_book.py
Generate Typst source files from TEI XML conversation files, compile each
to PDF, and consolidate all PDFs into one "Ur-Nimrod.pdf".

Run from the directory containing the XML files:
    python3 generate_book.py

XML is the source of truth for document structure (elements, attributes,
xml:lang, etc.). Typst files live alongside the XML in the same directory
and are regenerated from it. Round trips are also possible: each editable
text unit (paragraphs, headings, table cells, quotes, lexicon entries...)
is wrapped in a hidden Typst comment marker tying it back to its XML node.
If a .typ file's mtime is newer than its .xml file's, any unit whose marker
content was hand-edited is parsed back and written into the XML (plain text
edits, plus the bounded set of inline constructs this script itself emits:
bold/italic/small-caps/quotes/footnotes). A <name>/<term>'s occurrence
marker (#w[...]/#y[...]) is matched back to the specific original element
it came from -- in document order -- so its ref/xml:id/type/xml:lang carry
over onto the edited text rather than being lost. Structural changes --
adding or removing sections, changing element types or attributes -- must
still be made in the XML; Typst-side edits outside a marker, or additions
with no marker, are invisible to the sync and simply ignored.
"""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from lxml import etree

try:  # optional: image cropping and sizing degrade gracefully without it
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

_SCRIPT_PATH = Path(__file__)
DIR = _SCRIPT_PATH.parent
SPELL_DICT_PATH = DIR / "custom_dictionary.txt"
TAGGING_DICT_PATH = DIR / "tagging_dictionary.json"

# ══════════════════════════════════════════════════════════════════════════════
# PER-DIRECTORY CONSTANTS
#
# The only part of this script that differs between project directories: the
# rest of the file is kept byte-for-byte identical across them, so a change
# made anywhere else is simply copied over. When syncing, leave this block
# alone in each copy.
# ══════════════════════════════════════════════════════════════════════════════

CONSOLIDATED_PDF_NAME = "McThelema Consolidated.pdf"
# A hand-authored cover page, prepended to the consolidated PDF ahead of
# every sheet when present. Not a generated sheet itself -- never fed
# through _emit_typst_and_pdf -- so it's only ever picked up here, at
# consolidation time.
TITLE_PAGE_PDF_NAME = "title page.pdf"
# Hand-authored PDFs (file names, relative to this directory) merged into the
# consolidated PDF after the title page and ahead of the generated sheets, in
# the order listed. (The title page and each extra are looked up per format
# first -- "title page_a4.pdf" -- then under the plain name.) Anything else in the directory that isn't a "Sheet NN"
# PDF is left out of the consolidated PDF. Silently skipped when missing.
EXTRA_CONSOLIDATED_PDFS = []
# The paper formats every sheet is built in, in build order. Each sheet gets
# one .typ/.pdf pair per format, named "<sheet>_<key>.typ/.pdf", and each
# format gets its own consolidated PDF ("<name>_<key>.pdf"). "page" is the
# Typst page-size fragment; the mm figures give the page size and margins, and
# are what the Typst page setup and the sizing of figures are computed from.
# Remove an entry to stop building that format.
PAPER_FORMATS = {
    "a4": {
        "label": "A4",
        "page": 'paper: "a4"',
        "width_mm": 210.0, "height_mm": 297.0,
        "margin_mm": {"top": 30, "bottom": 30, "left": 30, "right": 25},
    },
    "ut": {
        "label": "US Trade (6 x 9 in)",
        "page": "width: 6in, height: 9in",
        "width_mm": 152.4, "height_mm": 228.6,
        "margin_mm": {"top": 20, "bottom": 25, "left": 22, "right": 16},
    },
}
# The concordance is a reference index, not part of the book: it is built only
# in the formats listed here (keys of PAPER_FORMATS), not in every one.
CONCORDANCE_FORMATS = ("a4",)
LEXICON_FILENAME = "Chapter 99 - Lexicon.xml"
CONCORDANCE_FILENAME = "Chapter 98 - Concordance.xml"
_LEXICON_TITLE = "Chapter 99 — Lexicon and Bibliography"
_CONCORDANCE_TITLE = "Chapter 98 — Concordance"
# Hand-maintained or otherwise non-sheet *.xml files in this directory that
# must never be treated as a source sheet (never validated, synced, scanned
# for first occurrences, or fed into the lexicon/concordance). Empty in a
# directory where every *.xml other than the generated lexicon/concordance
# is a real sheet.
NON_SHEET_XML_FILENAMES = ["Chapter 00.xml", "Chapter 99.xml", "Lexicon_additional.xml", "Lexicon_extra.xml"]
# The book's front matter: a hand-maintained <teiHeader> (title, subtitle,
# author, publisher, date...) plus a table of contents that this script
# regenerates on every run. Rendered as a title page followed by the table of
# contents, in every paper format, ahead of the first chapter.
FRONT_MATTER_FILENAME = "Chapter 00.xml"
# Illuminated initial: the first letter of a chapter's first paragraph is set
# large, in a decorative font (the legacy pipeline's "raised initial cap").
# INITIAL_DIV_TYPES lists the wrapper divisions whose opening paragraph gets
# one; set INITIAL_FONT to None to switch the feature off. The font must be
# installed (see `typst fonts`); without it the initial falls back to the
# body font, still enlarged.
INITIAL_FONT = "ZallmanCaps"
INITIAL_SIZE = "3.5em"
INITIAL_DIV_TYPES = ("chapter",)
# A <table> with at least this many columns is "too wide" for the portrait
# text and is set on its own landscape page; a narrower one flows inline.
# 4 matches the Kullaba - E-Anna generator's criterion (more than 3 columns).
LANDSCAPE_TABLE_MIN_COLUMNS = 4

# ══════════════════════════════════════════════════════════════════════════════
# END OF PER-DIRECTORY CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════


# Every "[TAG]" build-log prefix main()/validate_xml() print, widest first --
# used only to compute the padding width below, so the text following each
# tag lines up in the same column regardless of the tag's own length.
_LOG_TAG_WIDTH = max(len(t) for t in (
    "FIX", "WARN", "ERROR", "SKIP", "TYPST", "PDF", "SYNC", "STRIP", "QUOTE", "RELOC", "PROMOTE", "DICT", "LEXICON", "CONC", "IMAGE", "TOC",
)) + 3  # +2 for the brackets, +1 so even the widest tag gets a trailing gap


def _tag(name: str) -> str:
    """A "[NAME]" build-log prefix, padded so every tag's message text
    starts in the same column regardless of the tag's own length."""
    return f"[{name}]".ljust(_LOG_TAG_WIDTH)


# ──────────────────────────────────────────────
# PAPER FORMATS
# ──────────────────────────────────────────────
# The format a document is being rendered for. Set by generate_typst_doc()
# for the duration of one render, so code deep in the block renderer (figure
# sizing) can ask for the text area without a parameter threaded through
# every call.
_current_format = next(iter(PAPER_FORMATS))


# Book-wide page numbering. The sheets of the book (the chapters, then the
# lexicon) are numbered continuously, as in the printed book: the first sheet
# starts at page 1 and each next one at the page after the previous one's
# last (its blank padding page included, so every sheet starts on a
# right-hand page). The front matter is unnumbered.
_book_next_page = {}   # format key -> page number the next sheet of the book starts at
_book_layout = {}      # format key -> {xml file name: {"start": n, "pages": n, "sections": [(title, page, subs), ...]}}
# Whether the table of contents lists each section's subsections too (the
# --sub command-line flag); `subs` above is then [(title, page), ...], else [].
TOC_SUBSECTIONS = False
_START_MARK_RE = re.compile(r"^// book-start-page: (\d+)\s*$", re.MULTILINE)


def _pdf_pages(pdf_path: Path):
    """The page count of a PDF (via pdfinfo), or None if it can't be read."""
    try:
        info = subprocess.run(["pdfinfo", str(pdf_path)], cwd=str(DIR), capture_output=True, text=True)
    except FileNotFoundError:
        return None
    m = re.search(r"Pages:\s*(\d+)", info.stdout)
    return int(m.group(1)) if info.returncode == 0 and m else None


def _typ_start_page(typ_path: Path):
    """The book start page a .typ was generated for (its marker comment), or None."""
    try:
        head = typ_path.read_text(encoding="utf-8")[:400]
    except OSError:
        return None
    m = _START_MARK_RE.search(head)
    return int(m.group(1)) if m else None


def _query_section_pages(typ_path: Path, label: str = "section-page"):
    """The page number of every section heading of a .typ, in document
    order (`typst query` on the <section-page> markers the preamble adds to
    each level-2 heading -- or, with label "subsection-page", on the markers
    every subsection heading carries); an empty list on any failure."""
    try:
        r = subprocess.run(["typst", "query", str(typ_path), f"<{label}>"],
                           cwd=str(DIR), capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return []
        pages = []
        for item in json.loads(r.stdout):
            v = item.get("value")
            if isinstance(v, int):
                pages.append(v)
            elif isinstance(v, list) and v:
                pages.append(v[0])
        return pages
    except Exception:
        return []


def _fmt_name(filename: str, key: str) -> str:
    """"Name.pdf" -> "Name_a4.pdf": the file name of `filename` for format `key`."""
    p = Path(filename)
    return f"{p.stem}_{key}{p.suffix}"


def _sheet_typ(xml_path: Path, key: str) -> Path:
    """The .typ file of a sheet for format `key`."""
    return DIR / f"{xml_path.stem}_{key}.typ"


def _text_area_mm(key=None):
    """(width, height) in mm of the text area of a portrait page of format
    `key` (default: the format being rendered): the page minus its margins."""
    f = PAPER_FORMATS[key or _current_format]
    m = f["margin_mm"]
    return f["width_mm"] - m["left"] - m["right"], f["height_mm"] - m["top"] - m["bottom"]


def _page_setup(key: str, flipped: bool = False) -> str:
    """The arguments of Typst's #set page(...) for format `key`."""
    f = PAPER_FORMATS[key]
    m = f["margin_mm"]
    margin = (f"margin: (top: {m['top']:g}mm, bottom: {m['bottom']:g}mm, "
              f"left: {m['left']:g}mm, right: {m['right']:g}mm)")
    return f"{f['page']}{', flipped: true' if flipped else ''}, {margin}"


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
# Unicode ranges for scripts that should NOT be auto-italicized even when
# tagged with a non-English xml:lang (Greek and Hebrew alphabets)
_GREEK_SCRIPT_RE = re.compile(r"[Ͱ-Ͽἀ-῿]")
_HEBREW_SCRIPT_RE = re.compile(r"[֐-׿]")
_CYRILLIC_SCRIPT_RE = re.compile(r"[Ѐ-ӿ]")
# xml:lang primary subtags that keep the small-caps <term> rendering
# regardless of script (Russian, Old Church Slavonic)
_SMALLCAPS_TERM_LANGS = {"ru", "chu", "cu"}

# Inserted between italicized content and a directly-following footnote
# marker or a "?"/"!" that would otherwise sit flush against the italic
# slant with no separating space -- both read as visually colliding (the
# footnote's superscript digit looks like it's touching the last italic
# letter; a "?"/"!" right after an italic run looks kerned into it). A
# tiny #h(1pt) gap fixes the collision without a visible extra space, and
# #sym.wj (word joiner) keeps it from becoming a line-break point, matching
# the project's existing w()/y() marker convention below.
_ITALIC_GAP = "#h(1pt)#sym.wj"

# Display names for xml:lang codes, shown in brackets next to non-English
# text: after a lexicon <entry> headword, and before a non-English <quote>
# in a multi-<quote> <cit> block.
_LANG_DISPLAY_NAMES = {
    "ae": "Avestan",
    "akk": "Akkadian",
    "ar": "Arabic",
    "de": "German",
    "fr": "French",
    "grc": "Ancient Greek",
    "la": "Latin",
    "pro": "Old Provençal",
    "sa": "Sanskrit",
    "sux": "Sumerian",
    "egy": "Egyptian",
    "pi": "Pali",
    "xhu": "Hurrian",
    "he": "Hebrew",
    "ine-pro": "Proto-Indo-European",
}


def _lang_display_name(code: str) -> str:
    """Return a human-readable language name for an xml:lang code, falling
    back to the raw code (uppercased) if it isn't in the table."""
    return _LANG_DISPLAY_NAMES.get(code, code.upper())


# Small-caps category label shown after each lexicon headword, keyed by the
# entry's `type` attribute. `term` has no category system of its own in the
# TEI conventions (see "XML_TEI conventions.pdf" section "The <term> Tag"),
# so it just reads "Term"; every other value is one of the `<name>` `type`
# labels defined in that same document, section "The <name> Tag".
_LEXICON_CATEGORY_LABELS = {
    "term": "Term",
    "place": "Places and Physical Features",
    "deity": "Deities and Heroes",
    "artifact": "Artifacts and Buildings",
    "people": "Ethnic Groups and Sects",
    "event": "Historical Events and periods",
    "ruler": "Rulers and Dynasties",
    "source": "Primary Sources",
    "prophet": "Religious-Philosophical Figures",
    "scholar": "Scholars",
}


def _lexicon_category_label(entry_type):
    """Return the small-caps category label for a lexicon entry's `type`
    attribute, or None if it has none / an unrecognized one."""
    if not entry_type:
        return None
    return _LEXICON_CATEGORY_LABELS.get(entry_type)


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _parse_tei_xml(xml_path: Path):
    """Parse a TEI XML file, tolerating the literal HTML entity &nbsp;.

    &nbsp; is not a predefined XML entity, so libxml2 treats it as a fatal
    undeclared-entity error; XMLParser(recover=True) then recovers by
    silently DROPPING the entity reference entirely (not just failing to
    resolve it), which deletes the intended non-breaking space from the
    text. Replacing the literal entity with its numeric equivalent
    (&#160;, valid in any XML document) before parsing turns it into a
    real U+00A0 character, which Typst renders correctly without further
    help: Typst's line breaker follows Unicode UAX #14, which classifies
    U+00A0 as non-breaking (verified via local typst compile).
    """
    raw = xml_path.read_text(encoding="utf-8")
    raw = raw.replace("&nbsp;", "&#160;")
    try:
        return etree.fromstring(raw.encode("utf-8")).getroottree()
    except etree.XMLSyntaxError as e:
        # No recover=True fallback: on a missing end tag libxml2 "recovers"
        # by letting every later end tag close one level too shallow, and
        # the passes that write the tree back would save that silently
        # re-nested structure over the author's file. Stop instead.
        sys.exit(f"  {_tag('ERROR')}{xml_path.name} is not well-formed XML: {e}\n"
                 f"  Fix it by hand and rerun; no file was modified.")


def _is_roman_foreign(el) -> bool:
    """True if el carries a non-English xml:lang and its text is written in
    roman script (i.e. contains no Greek or Hebrew characters)."""
    lang = el.get(f"{{{XML_NS}}}lang")
    if not lang or lang == "en":
        return False
    text = "".join(el.itertext())
    if _GREEK_SCRIPT_RE.search(text) or _HEBREW_SCRIPT_RE.search(text):
        return False
    return True


def _term_style(el) -> str:
    """Return 'smallcaps', 'italic', or 'plain' for a <term> element.

    Russian/OCS terms (xml:lang "ru"/"chu"/"cu") keep the small-caps
    rendering regardless of script. Otherwise, italic is applied only when
    the term explicitly carries rend="italic" (content in a non-Latin
    script such as Greek, Hebrew, or Cyrillic is still never italicized,
    even if rend="italic" is set); everything else gets no font effect."""
    lang = (el.get(f"{{{XML_NS}}}lang") or "").split("-")[0].lower()
    if lang in _SMALLCAPS_TERM_LANGS:
        return "smallcaps"
    if el.get("rend") == "italic":
        text = "".join(el.itertext())
        if not (_GREEK_SCRIPT_RE.search(text) or _HEBREW_SCRIPT_RE.search(text)
                or _CYRILLIC_SCRIPT_RE.search(text)):
            return "italic"
    return "plain"


def _in_cit(el) -> bool:
    """True if el is a descendant of a <cit> element."""
    p = el.getparent()
    while p is not None:
        tag = p.tag
        if isinstance(tag, str) and tag.endswith("}cit"):
            return True
        p = p.getparent()
    return False


_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _iter_text_holders(el):
    """Yield (holder, attr) for every text position in document order within
    el's subtree: el.text, then each child's own subtree, then that child's
    tail. Mutating via setattr(holder, attr, ...) edits the live tree."""
    yield (el, "text")
    for child in el:
        yield from _iter_text_holders(child)
        yield (child, "tail")


def _collapse_ws_subtree(container) -> None:
    """Normalize whitespace across an entire element subtree so pretty-print
    indentation never leaks into rendered output, however deeply it's
    nested: every text/tail fragment has its whitespace runs (incl.
    newlines) collapsed to a single space — including fragments that are
    pure indentation between tags, which collapse to a lone space rather
    than being stripped to nothing. A second pass then dedupes the seam
    wherever two independently collapsed fragments meet back to back (e.g.
    a child's own leading whitespace right after its parent's trailing
    whitespace) — otherwise each contributes its own single space and two
    end up adjacent."""
    holders = list(_iter_text_holders(container))
    for holder, attr in holders:
        s = getattr(holder, attr)
        if not s:
            continue
        setattr(holder, attr, _WHITESPACE_RUN_RE.sub(" ", s))

    prev = None
    for holder, attr in holders:
        s = getattr(holder, attr)
        if not s:
            continue
        if prev is not None:
            p_holder, p_attr = prev
            p_val = getattr(p_holder, p_attr)
            if p_val.endswith(" ") and s.startswith(" "):
                s = s[1:]
                setattr(holder, attr, s)
        if s:
            prev = (holder, attr)


def _local(node):
    """Return the local (namespace-stripped) tag name of an lxml element."""
    tag = node.tag
    if not isinstance(tag, str):
        return ""
    brace = tag.find("}")
    return tag[brace + 1:] if brace != -1 else tag


def _normalize_bibl_whitespace(root) -> None:
    """Clean up whitespace around every <bibl> so pretty-printed subtags
    (<author>/<title>/<pubPlace>/<publisher>/<date>/<citedRange>, etc.)
    never leak a source newline or a run of spaces into the Typst output.
    Normalizes the whole enclosing <note place="foot">/<cit> (bibl plus
    any surrounding citation commentary), since that's the span that gets
    pretty-printed together; falls back to just <bibl> itself when it isn't
    inside one of those (e.g. a <listBibl> bibliography entry)."""
    for bibl in root.iter(f"{{{TEI_NS}}}bibl"):
        container = bibl
        p = bibl.getparent()
        while p is not None:
            if _local(p) in ("note", "cit"):
                container = p
                break
            p = p.getparent()
        _collapse_ws_subtree(container)


# ──────────────────────────────────────────────────────────────────────────────
# VALIDATION (TEI convention compliance -- see "XML_TEI conventions")
# ──────────────────────────────────────────────────────────────────────────────
# Two kinds of check run here:
#
# 1. The "first occurrence" / "subsequent use" rule (conventions doc, section
#    2): the first <name>/<term> tagging of an entity's key "enters it into
#    the lexicon"; any subsequent use -- including a bare, untagged mention
#    of one of its triggers, matched as whole words only (never a mere text
#    prefix like "Apollo" inside "Apollonian") -- must point back to it via
#    a ref="..." attribute instead of redefining it. Violations of this
#    specific rule are fixed in place, directly in the XML tree.
#
#    An identity's triggers are its key and its first occurrence's
#    displayed text, each matched only as the complete phrase, plus every
#    string the tagging dictionary (tagging_dictionary.json) associates
#    with that key -- e.g. "Inanna" and "Ishtar" for key "Inanna-Ishtar",
#    or a scholar's bare surname. Nothing is ever split automatically.
#
# 2. Every other convention in the document (required attributes, allowed
#    <name type=...> values, <author>/<title>/<date>/<citedRange> structure,
#    the &nbsp;-before-?/! rule, <q> instead of literal quotes, no "*" for
#    emphasis...) is only ever reported -- file name + line number -- never
#    auto-corrected. The one exception: a <name type="..."> value is always
#    lower-cased in place (and the fix logged) -- type values are policy-
#    mandated to be lower-case (e.g. "prophet", not "Prophet"), and casing
#    slips are mechanical enough to just fix rather than merely flag.

_ALLOWED_NAME_TYPES = {
    "place", "deity", "artifact", "people", "event", "ruler", "source", "prophet", "scholar",
}
# Common English function words. A <name>/<term> whose entire text is one of
# these -- and whose own ref/xml:id is nothing more than that same word --
# is almost always an accidental collision between a short proper noun and
# ordinary grammar (e.g. the deity "An" swallowing every bare article
# "an"/"An" corpus-wide) rather than a deliberate mention. See
# _check_stopword_tags.
_ENGLISH_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "for", "so", "yet",
    "in", "on", "at", "by", "to", "of", "as", "is", "are", "was", "were",
    "be", "been", "being", "with", "from", "into", "onto", "upon", "over",
    "under", "about", "above", "below", "between", "among", "through",
    "during", "before", "after", "since", "until", "while", "if", "than",
    "then", "that", "this", "these", "those", "it", "its", "he", "she",
    "they", "we", "you", "i", "do", "does", "did", "has", "have", "had",
    "not", "no", "such", "some", "any", "all", "each", "every", "both",
    "few", "more", "most", "other", "own", "same", "too", "very", "can",
    "will", "just", "should", "now", "his", "her", "him", "them", "their",
    "our", "us", "my", "me", "your", "yours", "mine", "ours", "theirs",
    "who", "whom", "whose", "which", "what", "when", "where", "why", "how",
})
_AUTHOR_FIRSTNAME_RE = re.compile(
    r"^(?:[^\W\d_]+|[^\W\d_]\.)(?:[_\-](?:[^\W\d_]+|[^\W\d_]\.))*$", re.UNICODE
)


def _is_author_key(s):
    """True if `s` is shaped like the LASTNAME-Firstname author/scholar key
    format (see _split_author_key)."""
    return _split_author_key(s) is not None


def _split_author_key(s):
    """(LASTNAME, Firstname) if `s` is shaped like the LASTNAME-Firstname
    author/scholar key format, else None -- e.g. "GIRARD-René",
    "ZÓLYOMI-Gábor", "ASHER-GREVE-Julia", "GLASSNER-Jean-Jacques",
    "NISSEN-Hans_J.", "EVANS-R_J_W".

    A bare hyphen doesn't by itself mark the last-name/first-name boundary:
    either section can contain one on its own ("ASHER-GREVE" a compound
    surname, "Jean-Jacques" a compound first name; underscore plays the
    same role as a space within either section, e.g. "GOODNICK_WESTENHOLZ"
    or a middle initial "Hans_J."). So every hyphen in `s` is tried in turn
    as the candidate boundary, last to first, and the first one that splits
    it into a last name with no lowercase letter anywhere (Python's Unicode-aware
    str.isupper() check per character, not an ASCII-only [A-Z] class, so an
    accented letter doesn't fail the match) and a first name that is itself
    one or more "-"/"_"-joined components -- each either a plain name or a
    single capital letter plus a period for an abbreviated middle name --
    starting with an uppercase letter wins -- so the longest possible last
    name ("ASHER-GREVE", not "ASHER", out of "ASHER-GREVE-Julia").
    Follow the same conventions when minting a new author key by hand."""
    s = s or ""
    parts = s.split("-")
    for i in range(len(parts) - 1, 0, -1):
        last = "-".join(parts[:i])
        if any(ch.isalpha() and not ch.isupper() for ch in last):
            continue
        first = "-".join(parts[i:])
        if _AUTHOR_FIRSTNAME_RE.match(first) and first[0].isupper():
            return last, first
    return None
# <date when="..."> accepts a bare year ("1998") or a full ISO date ("2018-09-26").
_DATE_WHEN_RE = re.compile(r"^\d{4}(-\d{2}-\d{2})?$")
# Word tokens are Unicode letters (so an accented letter never splits a word: "Durgā" is one
# token, not "Durg" + a stray "ā"), plus internal apostrophes. Digits belong to a token that
# has at least one letter ("MI5" is one token, not "MI" + a stray "5"); a bare number never is one.
_WORD_TOKEN_RE = re.compile(r"[^\W_]*[^\W\d_][^\W_]*(?:['’][^\W_]+)*")
_QUOTE_CHARS = "\"“”"
# Elements whose own flow is a fully self-contained scope for pairing up
# literal quote characters into a new <q> -- a quote's open/close marks
# are never expected to span across two different paragraphs, headings,
# cells, etc. Also doubles (unioned with q/quote/bibl below) as the set a
# unit's own flow-scan must never descend into: a nested one of these is
# its own separate scope, and q/quote/bibl are already-quoted or
# bibliographic content that must be left exactly as-is.
_QUOTE_SCOPE_TAGS = {"p", "head", "item", "cell", "note", "def", "orth", "form", "sense", "title"}
_QUOTE_STOP_TAGS = _QUOTE_SCOPE_TAGS | {"q", "quote", "bibl"}
_XML_LANG = f"{{{XML_NS}}}lang"
_XML_ID = f"{{{XML_NS}}}id"
_ID_FIRST_CHAR_RE = re.compile(r"[^\W\d]", re.UNICODE)  # a letter (any script) or "_"
_VALID_ID_RE = re.compile(r"^[^\W\d][\w.\-]*$", re.UNICODE)
_ID_STRIP_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def _mint_xml_id(s):
    """Turn an arbitrary key/phrase into a valid, space-free xml:id, e.g.
    "Sargon of Akkad" -> "Sargon_of_Akkad" (mirrors the one xml:id the
    conventions doc itself demonstrates, "Inanna-Ishtar" -- no spaces).
    Unicode letters (accented, non-Latin scripts, ...) are valid NCName
    characters and are kept as-is -- e.g. "maîtresse-en-titre" needs no
    minting at all, and only genuinely illegal characters (spaces,
    apostrophes, punctuation) become "_"."""
    s = _ID_STRIP_RE.sub("_", s.strip())
    if not s or not _ID_FIRST_CHAR_RE.match(s[0]):
        s = "_" + s
    return s


def _in_tag(holder, attr, tags):
    """True if the text position (holder, attr) -- as yielded by
    _iter_text_holders -- sits inside an element whose local name is in
    `tags`. For .text, that's holder itself (and its ancestors); for
    .tail, the text lives in holder's parent's flow, so start there."""
    node = holder if attr == "text" else holder.getparent()
    while node is not None:
        if _local(node) in tags:
            return True
        node = node.getparent()
    return False


def _stopword_trigger_blocked(key_tuple, ref_id):
    """True if `key_tuple` is nothing but a single English stop word (see
    _ENGLISH_STOPWORDS) and the entity's own id doesn't equal that same
    word -- i.e. a deliberately disambiguated id (e.g. xml:id=
    "mesh_-_Sumerian_term" on the word "me") whose bare surface text must
    never become a corpus-wide auto-tag trigger, since every ordinary
    English "me" elsewhere would then be swept up and wrapped as this
    entity (exactly what happened with the deity "An" swallowing every
    bare article "an"/"An", and again when the Sumerian term "me" was
    re-anchored under a disambiguated id but still registered its own
    bare text).

    An id that IS the bare stop word itself (no disambiguation at all --
    the accidental-collision case _check_stopword_tags warns about) is
    left alone and still registers as before: that case is meant to be
    caught by the warning and fixed by disambiguating the id, not
    silently masked here."""
    if len(key_tuple) != 1 or key_tuple[0] not in _ENGLISH_STOPWORDS:
        return False
    return (ref_id or "").strip().lower() != key_tuple[0]


def _registry_set(registry, key_tuple, ref_id, tag, xml_lang, rend):
    """registry.setdefault, but refusing to ever install a bare English
    stop word as a trigger for a deliberately disambiguated id (see
    _stopword_trigger_blocked)."""
    if _stopword_trigger_blocked(key_tuple, ref_id):
        return
    registry.setdefault(key_tuple, (ref_id, tag, xml_lang, rend))


# The tagging dictionary (TAGGING_DICT_PATH): every extra string that must
# resolve to an identity besides its own key and displayed text -- e.g.
# "Inanna" and "Ishtar" for key "Inanna-Ishtar", or a scholar's bare
# surname. A JSON list of {"string": ..., "key": ..., "tag": "name"|"term"}
# objects, one per line; several strings may share one key. Loaded once by
# main() (see _load_tagging_dictionary). An entry becomes a trigger only
# once its key's first occurrence has been registered, exactly like the
# key itself, so it never fires earlier in the sheet sequence.
_TAGGING_DICT_ENTRIES = []   # every entry, in file order
_TAGGING_DICT_BY_KEY = {}    # key -> [entry, ...]
_tagging_dict_used_keys = set()
_tagging_dict_changed = [False]

# Every lexicon xml:id of the previous run (see _load_previous_lexicon_ids):
# a scholar/prophet <name> NOT among them is new, and gets its bare surname
# added to the tagging dictionary (see _add_surname_entry). An identity that
# already was in the lexicon never does, so a surname line deleted by hand
# stays deleted.
_PREVIOUS_LEXICON_IDS = set()

# Every identity whose first occurrence is an <author>, mapped to its type
# (filled by _process_first_occurrence, cleared by main() along with the
# registry): the <author>'s own type="prophet" if it has one, else
# "scholar". A lexicon entry backfilled from a later <name ref="..."> --
# which carries no type of its own -- gets that type.
_AUTHOR_DEFINED_IDS = {}


def _load_tagging_dictionary():
    """Fill _TAGGING_DICT_ENTRIES/_TAGGING_DICT_BY_KEY from TAGGING_DICT_PATH
    (empty if the file doesn't exist). A trailing comma before the closing
    "]" -- the usual leftover of deleting the last line by hand -- is
    tolerated; any other syntax error stops the build, since silently
    running without the dictionary would untag every string it holds."""
    _TAGGING_DICT_ENTRIES.clear()
    _TAGGING_DICT_BY_KEY.clear()
    _tagging_dict_used_keys.clear()
    _tagging_dict_changed[0] = False
    if not TAGGING_DICT_PATH.exists():
        return
    raw = TAGGING_DICT_PATH.read_text(encoding="utf-8")
    try:
        data = json.loads(re.sub(r",\s*\]\s*$", "]", raw.strip()))
    except json.JSONDecodeError as e:
        print(f"  {_tag('ERROR')}{TAGGING_DICT_PATH.name}: {e}")
        sys.exit(1)
    for i, entry in enumerate(data if isinstance(data, list) else [], start=1):
        if not (isinstance(entry, dict)
                and isinstance(entry.get("string"), str) and entry["string"].strip()
                and isinstance(entry.get("key"), str) and entry["key"].strip()
                and entry.get("tag") in ("name", "term")):
            print(f"  {_tag('WARN')}{TAGGING_DICT_PATH.name}: entry {i} ignored "
                  f'(needs a "string", a "key" and a "tag" of "name" or "term"): {entry!r}')
            continue
        _add_tagging_entry(entry["string"].strip(), entry["key"].strip(), entry["tag"])
    _tagging_dict_changed[0] = False


def _add_tagging_entry(string, key, tag):
    """Add one dictionary entry unless the same string/key pair is already
    there. Returns True if it was added."""
    if any(e["string"] == string for e in _TAGGING_DICT_BY_KEY.get(key, ())):
        return False
    entry = {"string": string, "key": key, "tag": tag}
    _TAGGING_DICT_ENTRIES.append(entry)
    _TAGGING_DICT_BY_KEY.setdefault(key, []).append(entry)
    _tagging_dict_changed[0] = True
    return True


def _save_tagging_dictionary():
    """Write the dictionary back to TAGGING_DICT_PATH, sorted by key then
    string, one entry per line -- only if this run added something."""
    if not _tagging_dict_changed[0]:
        return
    entries = sorted(_TAGGING_DICT_ENTRIES, key=lambda e: (e["key"].lower(), e["string"].lower()))
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    TAGGING_DICT_PATH.write_text("[\n  " + ",\n  ".join(lines) + "\n]\n", encoding="utf-8")
    _tagging_dict_changed[0] = False


def _load_previous_lexicon_ids(path: Path):
    """Fill _PREVIOUS_LEXICON_IDS with every entry xml:id of the lexicon
    file at `path`, as the previous run left it (empty if there is none)."""
    _PREVIOUS_LEXICON_IDS.clear()
    if not path.exists():
        return
    try:
        root = _parse_tei_xml(path).getroot()
    except etree.XMLSyntaxError:
        return
    for entry in root.iter(f"{{{TEI_NS}}}entry"):
        if entry.get(_XML_ID):
            _PREVIOUS_LEXICON_IDS.add(entry.get(_XML_ID))


def _name_skeleton(s):
    """Lowercased base letters only -- what a key's surname and the same
    surname in the displayed text still share once the id's ASCII-only,
    "_"-for-space spelling is set aside (e.g. "OBRIEN" and "O'Brien",
    "VANDER_STICHELE" and "Vander Stichele", "DUMEZIL" and "Dumézil")."""
    return "".join(c for c in _strip_diacritics(s).lower() if c.isalpha())


def _scholar_surname(ident, text_content):
    """The bare surname of a LASTNAME-Firstname key, spelled the way the
    displayed text spells it (accents, apostrophe, spaces), or else
    rebuilt from the key in title case. None when there's no recognizable
    first/last-name structure: not an author-shaped key at all; an
    "X_OF_Y" epithet rather than a surname ("ISIDORE_OF_SEVILLE-Isidore");
    or a "surname" that is the whole displayed name ("Molière",
    "Columbanus"), already a trigger on its own."""
    split = _split_author_key(ident or "")
    if split is None:
        return None
    last = split[0]
    if "OF" in re.split(r"[_\-]", last):
        return None
    skel = _name_skeleton(last)
    if not skel or skel == _name_skeleton(text_content):
        return None
    toks = list(_WORD_TOKEN_RE.finditer(text_content))
    found = None
    for i in range(len(toks)):
        for j in range(i, len(toks)):
            span = text_content[toks[i].start():toks[j].end()]
            if _name_skeleton(span) == skel:
                found = span  # keep the last match: the surname comes last
    if found:
        return found
    return "-".join(" ".join(w.capitalize() for w in seg.split("_") if w)
                    for seg in last.split("-"))


def _add_surname_entry(ident, ref_id, text_content, where, add=True):
    """For a NEW scholar/prophet <name> (one the previous lexicon didn't
    have), add its bare surname to the tagging dictionary; `where` is the
    "file:line" the log lines name. With add=False (an <author>-defined
    scholar) nothing is ever added -- only the duplicate check below runs.

    A surname some other key already holds in the dictionary (e.g. "Miller"
    for MILLER-Chris when MILLER-James comes along) is never added a second
    time: a shared string only ever tags the identity registered first, so
    the line would be dead. Reported as a warning instead, on every run,
    since the bare surname keeps going to the other person."""
    surname = _scholar_surname(ident, text_content)
    if not surname:
        return
    others = [e["key"] for e in _TAGGING_DICT_ENTRIES if e["string"] == surname and e["key"] != ref_id]
    if others:
        print(f"  {_tag('WARN')}{where}: surname \"{surname}\" of {ref_id} not added to "
              f"{TAGGING_DICT_PATH.name} -- already listed for {', '.join(others)}; "
              f"tag its bare mentions by hand")
        return
    if add and ref_id not in _PREVIOUS_LEXICON_IDS and _add_tagging_entry(surname, ref_id, "name"):
        print(f"  {_tag('DICT')}{where}: added \"{surname}\" -> {ref_id} to {TAGGING_DICT_PATH.name}")


def _name_entry_type(el, ident):
    """The lexicon type of a <name> occurrence of `ident`: its own type, or
    the defining <author>'s type ("prophet" or the default "scholar") when
    it has none and `ident` was defined by an <author>."""
    return el.get("type") or _AUTHOR_DEFINED_IDS.get(ident)


def _register_dictionary_strings(registry, ident, ref_id, tag, xml_lang, rend):
    """Register every tagging-dictionary string associated with this
    identity (by its key as written, or its minted xml:id) as one more
    whole-phrase trigger for it -- matched case-sensitively, so a surname
    that is also an ordinary word ("White") never catches the word itself
    ("white")."""
    for key in dict.fromkeys((ident, ref_id)):
        entries = _TAGGING_DICT_BY_KEY.get(key)
        if not entries:
            continue
        _tagging_dict_used_keys.add(key)
        for entry in entries:
            if entry["tag"] != tag:
                print(f"  {_tag('WARN')}{TAGGING_DICT_PATH.name}: \"{entry['string']}\" is listed as "
                      f"<{entry['tag']}> but {key} is a <{tag}>; tagged as <{tag}>")
            _register_candidate(registry, entry["string"], ref_id, tag, xml_lang, rend,
                                case_sensitive=True)


def _case_sensitive_key(tokens):
    """The registry key of a case-sensitive trigger: its tokens as written,
    behind a leading "" -- never a token itself, so such a key can't
    collide with a case-insensitive one (all-lowercase tokens)."""
    return ("",) + tuple(tokens)


def _registry_lookup(registry, tokens):
    """The registry hit for this exact token sequence, if any: a
    case-sensitive trigger spelled exactly like it first, else a
    case-insensitive one."""
    return (registry.get(_case_sensitive_key(tokens))
            or registry.get(tuple(t.lower() for t in tokens)))


def _is_bare_surname_display(ident, text_content):
    """True if a first occurrence's displayed text is nothing but the bare
    surname of its LASTNAME-Firstname key (<name xml:id="HEAD-Ronan_James">
    Head</name>). Such a display trigger is registered case-sensitively,
    like a tagging-dictionary surname: a surname that is also an ordinary
    word ("Head", "White") must never catch the word itself ("head")."""
    split = _split_author_key(ident or "")
    if split is None:
        return False
    toks = list(_WORD_TOKEN_RE.finditer(text_content or ""))
    return len(toks) == 1 and _name_skeleton(toks[0].group()) == _name_skeleton(split[0])


def _register_candidate(registry, phrase, ref_id, tag, xml_lang, rend, case_sensitive=False):
    """Register `phrase` as a subsequent-use trigger for ref_id/tag -- as
    the whole phrase only: a part of it is never a trigger on its own
    unless the tagging dictionary says so. Matched case-insensitively
    unless `case_sensitive`. Never overwrites an existing registration:
    the first occurrence wins."""
    phrase = (phrase or "").strip()
    if not phrase:
        return
    toks = list(_WORD_TOKEN_RE.finditer(phrase))
    if not toks:
        return
    if case_sensitive:
        if _stopword_trigger_blocked(tuple(m.group().lower() for m in toks), ref_id):
            return
        registry.setdefault(_case_sensitive_key(m.group() for m in toks),
                            (ref_id, tag, xml_lang, rend))
        return
    whole_key = tuple(m.group().lower() for m in toks)
    _registry_set(registry, whole_key, ref_id, tag, xml_lang, rend)


def _find_matches(text, registry):
    """Scan `text` left to right for registry hits, greedily preferring the
    longest matching phrase at each position, non-overlapping. Returns a
    list of (start, end, ref_id, tag, xml_lang, rend, matched_text)."""
    toks = list(_WORD_TOKEN_RE.finditer(text))
    if not toks:
        return []
    max_len = max((len(k) - (k[:1] == ("",)) for k in registry), default=1)
    matches = []
    i, n = 0, len(toks)
    while i < n:
        matched = False
        for L in range(min(max_len, n - i), 0, -1):
            hit = _registry_lookup(registry, [toks[i + j].group() for j in range(L)])
            if hit:
                ref_id, tag, xml_lang, rend = hit
                start, end = toks[i].start(), toks[i + L - 1].end()
                matches.append((start, end, ref_id, tag, xml_lang, rend, text[start:end]))
                i += L
                matched = True
                break
        if not matched:
            i += 1
    return matches


def _splice_elements(holder, attr, text, spans):
    """Splice pre-built elements into the tree in place of the text spans
    they cover. `spans` is a left-to-right, non-overlapping list of
    (start, end, element) against the current content of holder.attr; each
    element already has its final tag/attributes/text set. Everything
    outside the spans -- and the exact surrounding characters -- is left
    untouched."""
    if attr == "text":
        parent, base_index = holder, 0
    else:
        parent = holder.getparent()
        base_index = list(parent).index(holder) + 1

    cursor = 0
    new_elems = []
    for idx, (start, end, elem) in enumerate(spans):
        prefix = text[cursor:start]
        if idx == 0:
            setattr(holder, attr, prefix)
        else:
            new_elems[-1].tail = prefix
        parent.insert(base_index + idx, elem)
        new_elems.append(elem)
        cursor = end
    new_elems[-1].tail = text[cursor:]


def _apply_matches(holder, attr, text, matches):
    """Splice new ref-pointing <name>/<term> elements into the tree for each
    match found in text (the current content of holder.attr), replacing
    that bare text with tagged spans while leaving everything else -- and
    the exact surrounding characters -- untouched."""
    spans = []
    for (start, end, ref_id, tag, xml_lang, rend, matched_str) in matches:
        elem = etree.Element(f"{{{TEI_NS}}}{tag}")
        elem.set("ref", ref_id)
        if xml_lang:
            elem.set(_XML_LANG, xml_lang)
        if tag == "term" and rend:
            elem.set("rend", rend)
        elem.text = matched_str
        spans.append((start, end, elem))
    _splice_elements(holder, attr, text, spans)


def _strip_diacritics(s):
    """Fold s to its base-letter ASCII skeleton (NFD-decompose, drop
    combining marks). Used only to detect whether an xml:id-derived lemma
    and the tag's actual displayed text spell the same word -- see
    _restore_diacritics."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _restore_diacritics(id_lemma, text_content):
    """xml:id is ASCII-only by convention, so id_lemma can differ from
    text_content (the tag's actual displayed text) in two id-minting-only
    ways -- restored here, letter/character-for-character, wherever they
    line up (different length bails out unchanged: a genuinely different
    inflected form/phrase, not a minting artifact):

    - A diacritic gets stripped from a letter (e.g. "Mannerbund" for
      <term xml:id="Mannerbund">Männerbund</term>, "Kali" for <name
      xml:id="Kali">Kālī</name>) -- restored from text_content's letter,
      keeping id_lemma's own capitalization there (so a lowercase mid-
      sentence occurrence doesn't undo the headword's Title Case).
    - A character _mint_xml_id doesn't allow in an id (an apostrophe, a
      modifier letter, ...) becomes "_" -> " " (e.g. "Fin amor" for <term
      xml:id="Fin_amor">finʼamor</term>) -- restored verbatim from
      text_content at that position, since _mint_xml_id only ever turns a
      non-word/./- character into "_", never a genuine letter.

    Any other mismatch (different base letters, or a non-alpha id
    character -- a hyphen, a period -- that doesn't match text_content
    exactly) means a real difference, not a minting artifact, so id_lemma
    is returned unchanged at that point."""
    if len(id_lemma) != len(text_content):
        return id_lemma
    out = []
    for i, t in zip(id_lemma, text_content):
        if i == " ":
            out.append(t)
        elif i.isalpha():
            if _strip_diacritics(t).lower() != i.lower():
                return id_lemma
            out.append(t.upper() if i.isupper() else t.lower())
        elif t != i:
            return id_lemma
        else:
            out.append(i)
    return "".join(out)


def _compute_lemma(ident, text_content):
    """The lexicon headword for a first-occurrence <name>/<term>: the
    identity itself (already "the normalized...name" / lemma, per
    convention), EXCEPT when it follows the special LASTNAME-Firstname
    *author*-key format -- there, it's an id, not a spellable headword, so
    fall back to the element's actual displayed text (e.g. "GIRARD-Rene" ->
    "René Girard"). `ident` may be a minted xml:id (see _mint_xml_id) rather
    than the original phrase -- e.g. "Washington_DC" on a re-run, once no
    key="..." survives to read the un-minted "Washington DC" from -- so
    underscores are turned back into spaces; a non-author identity never
    has a legitimate underscore of its own. Any diacritic the id's ASCII
    form dropped is restored from the tag's displayed text wherever the
    two still spell the same word (see _restore_diacritics)."""
    if ident and _is_author_key(ident):
        return text_content.strip()
    if ident:
        return _restore_diacritics(ident.replace("_", " "), text_content.strip())
    return text_content.strip()


def _process_first_occurrence(root, registry=None, lexicon=None, source_file="", source_title=""):
    """Walk the document in reading order, maintaining a registry of every
    <name>/<term>/<author> identity encountered so far. Converts a
    duplicate re-definition of an already-registered identity into a
    ref="..." pointer, and wraps any later bare mention of a registered
    identity (its key, its displayed text, or one of its tagging-dictionary
    strings -- see _TAGGING_DICT_ENTRIES) the same way. A legacy
    key="..." attribute is migrated to xml:id="..." wherever found (the
    conventions doc's current attribute for a first occurrence). Mutates
    `root` in place; returns the list of fixes made, as (line, message)
    pairs.

    <author> tags share this same registry with <name>/<term> -- an author
    and a <name type="Prophet">/<name type="Scholar"> for the same person
    must resolve to one identity, whichever comes first in the sheet
    sequence, per the conventions doc's section 5.

    `registry` may be shared across multiple calls (one per sheet file, in
    sheet order) so that "first occurrence" is tracked across the entire
    sheet sequence, not reset per file -- pass the same dict in each time.
    When `lexicon` is given, every genuinely new <name>/<term> registration
    also records a lexicon entry (source_file/source_title identify where
    it happened), keyed by ref_id, first writer wins."""
    if registry is None:
        registry = {}
    fixes = []

    def scan_fragment(holder, attr, text):
        if not text:
            return
        matches = _find_matches(text, registry)
        if not matches:
            return
        line = holder.sourceline
        for (_s, _e, ref_id, tag, _l, _r, matched_str) in matches:
            fixes.append((line, f'wrapped bare "{matched_str}" as <{tag} ref="{ref_id}">'))
        _apply_matches(holder, attr, text, matches)

    def process_author(el):
        if el.get("ref"):
            return  # already a compliant subsequent occurrence
        key_attr = el.get("key")
        ident = el.get(_XML_ID) or key_attr
        if ident is None:
            return  # missing id entirely -- reported separately as an "other" violation
        whole_key = tuple(m.group().lower() for m in _WORD_TOKEN_RE.finditer(ident))
        existing = registry.get(whole_key) if whole_key else None
        if existing is not None:
            prior_id = existing[0]
            el.attrib.pop("key", None)
            el.attrib.pop(_XML_ID, None)
            el.set("ref", prior_id)
            fixes.append((el.sourceline,
                          f'converted duplicate <author> occurrence (id="{ident}") '
                          f'into <author ref="{prior_id}">'))
        else:
            ref_id = ident if _VALID_ID_RE.match(ident) else _mint_xml_id(ident)
            if el.get(_XML_ID) != ref_id:
                el.set(_XML_ID, ref_id)
            if key_attr:
                del el.attrib["key"]
                fixes.append((el.sourceline,
                              f'renamed <author key="{key_attr}"> to xml:id="{ref_id}" (first occurrence)'))
            # Registered as tag "name" -- a <name> tag for the same person
            # elsewhere must resolve to this identity via <name ref="...">,
            # never <author ref="..."> (author is a bibl-only structural
            # tag). Only the full id, as a whole phrase, plus whatever the
            # tagging dictionary lists for it; an <author> never adds a
            # surname line of its own (it has no scholar/prophet type).
            registry.setdefault(whole_key, (ref_id, "name", None, None))
            _AUTHOR_DEFINED_IDS[ref_id] = "prophet" if el.get("type") == "prophet" else "scholar"
            _add_surname_entry(ident, ref_id, "".join(el.itertext()),
                               f"{source_file}:{el.sourceline}", add=False)
            _register_dictionary_strings(registry, ident, ref_id, "name", None, None)

    def process_label_subtree(el):
        """Everything inside a <name>/<term> is that entity's own label --
        its display text -- never a separate bare mention, even when split
        across a nested inline wrapper like <q> (e.g. <name ref="X"><q>
        Rashidun</q> Caliphs</name>: "Rashidun" there is part of the
        label, not a fresh occurrence to auto-tag). So descend structurally
        to let any further <name>/<term> nested inside register/resolve
        itself as usual, but never call scan_fragment against any text or
        tail anywhere else in this subtree."""
        for child in el:
            if _local(child) in ("name", "term"):
                process(child)
            else:
                process_label_subtree(child)

    def process(el):
        local = _local(el)

        if local in ("bibl", "teiHeader", "title"):
            # Document/bibliographic metadata (incl. a cited work's own
            # <title>) is never scanned for bare mentions -- but inside a
            # <bibl>, every <author> still participates in the shared
            # first-occurrence/ref registry (conventions doc, section 5).
            for author_el in el.iter(f"{{{TEI_NS}}}author"):
                process_author(author_el)
            # A work's title in the running text (outside a <bibl>/<teiHeader>)
            # may itself be a tagged entity -- <title><name type="source">
            # Mahabharata</name></title> -- which registers like any other
            # <name>/<term>; only the title's untagged text is never scanned.
            if local == "title" and not _in_any_tag(el, ("bibl", "teiHeader")):
                process_label_subtree(el)
            return

        if local == "author":
            # An <author> outside a <bibl> (straight in a footnote or a
            # paragraph) is the same bibliographic tag: it registers as
            # usual, but its text is a person's name as cited and is never
            # scanned for bare mentions ("Eleanor Robson" must not become
            # <name ref="Eleanor_of_Aquitaine">Eleanor</name> Robson). A
            # ref-pointing <name>/<term> an earlier run left nested inside
            # it is unwrapped back to plain text.
            for inner in list(el.iter(f"{{{TEI_NS}}}name", f"{{{TEI_NS}}}term")):
                if inner.get("ref") and len(inner) == 0 and inner.getparent() is not None:
                    fixes.append((inner.sourceline,
                                  f'unwrapped auto-tag <{_local(inner)} ref="{inner.get("ref")}"> '
                                  f'inside <author>'))
                    _unwrap_element(inner)
            process_author(el)
            return

        if local in ("name", "term"):
            if local == "name":
                t_raw = el.get("type")
                if t_raw is not None and t_raw != t_raw.lower():
                    el.set("type", t_raw.lower())
                    fixes.append((el.sourceline,
                                  f'lowercased <name type="{t_raw}"> to type="{t_raw.lower()}"'))
            if el.get("ref"):
                # Already a compliant subsequent occurrence -- nothing to
                # fix, but still backfill the lexicon if a *previous* run
                # (before this backfill existed, or because its true first
                # occurrence is an <author> with no type/lemma of its own)
                # left this identity without an entry: this element is the
                # only remaining place in the document that could supply
                # one.
                prior_id = el.get("ref")
                # A type="blocker" occurrence isn't a genuine mention of
                # prior_id (see _occurrence_marker/build_concordance) -- it
                # must never supply the lexicon's fallback entry either.
                if el.get("type") != "blocker" and lexicon is not None and prior_id not in lexicon:
                    text_content = "".join(el.itertext())
                    lexicon[prior_id] = {
                        "lemma": _compute_lemma(prior_id, text_content),
                        "xml_lang": el.get(_XML_LANG),
                        "entry_type": _name_entry_type(el, prior_id) if local == "name" else "term",
                        "source_file": source_file,
                        "source_title": source_title,
                    }
            else:
                key_attr = el.get("key")
                ident = el.get(_XML_ID) or key_attr
                if ident is not None:
                    whole_key = tuple(m.group().lower() for m in _WORD_TOKEN_RE.finditer(ident))
                    existing = registry.get(whole_key) if whole_key else None
                    if existing is not None:
                        prior_id = existing[0]
                        # This <name>/<term> lost the identity race (e.g. an
                        # <author> citation for the same person appeared
                        # earlier) -- but it's still the entity's one real
                        # lexicon-worthy occurrence (type + lemma), so
                        # backfill the lexicon entry from it if nothing
                        # claimed that slot yet.
                        if lexicon is not None and prior_id not in lexicon:
                            xml_lang = el.get(_XML_LANG)
                            text_content = "".join(el.itertext())
                            lexicon[prior_id] = {
                                "lemma": _compute_lemma(ident, text_content),
                                "xml_lang": xml_lang,
                                "entry_type": _name_entry_type(el, prior_id) if local == "name" else "term",
                                "source_file": source_file,
                                "source_title": source_title,
                            }
                        el.attrib.pop("key", None)
                        el.attrib.pop(_XML_ID, None)
                        el.attrib.pop("type", None)
                        el.set("ref", prior_id)
                        fixes.append((el.sourceline,
                                      f'converted duplicate first occurrence (id="{ident}") '
                                      f'into <{local} ref="{prior_id}">'))
                    else:
                        old_xml_id = el.get(_XML_ID)
                        ref_id = ident if _VALID_ID_RE.match(ident) else _mint_xml_id(ident)
                        if old_xml_id != ref_id:
                            el.set(_XML_ID, ref_id)
                        if key_attr:
                            del el.attrib["key"]
                            fixes.append((el.sourceline,
                                          f'renamed <{local} key="{key_attr}"> to xml:id="{ref_id}" '
                                          f'(first occurrence)'))
                        elif old_xml_id and old_xml_id != ref_id:
                            # xml:id itself was malformed (e.g. contains a
                            # space) -- reminted into a valid NCName. Must
                            # still be logged as a `fixes` entry, not just
                            # applied to the in-memory tree: validate_xml()
                            # only writes the file back to disk `if fixes`,
                            # so an unlogged mutation here would be silently
                            # lost the moment this tree object is discarded.
                            fixes.append((el.sourceline,
                                          f'reminted invalid <{local} xml:id="{old_xml_id}"> '
                                          f'as xml:id="{ref_id}"'))
                        xml_lang = el.get(_XML_LANG)
                        rend = el.get("rend") if local == "term" else None
                        text_content = "".join(el.itertext())
                        _register_candidate(registry, ident, ref_id, local, xml_lang, rend)
                        if text_content.strip().lower() != ident.strip().lower():
                            _register_candidate(registry, text_content, ref_id, local, xml_lang, rend,
                                                case_sensitive=_is_bare_surname_display(ident, text_content))
                        # A new scholar/prophet gets its bare surname into
                        # the tagging dictionary before the dictionary's
                        # strings are registered, so it takes effect at once.
                        if local == "name" and el.get("type") in ("scholar", "prophet"):
                            _add_surname_entry(ident, ref_id, text_content,
                                               f"{source_file}:{el.sourceline}")
                        _register_dictionary_strings(registry, ident, ref_id, local, xml_lang, rend)
                        if lexicon is not None and ref_id not in lexicon:
                            lexicon[ref_id] = {
                                "lemma": _compute_lemma(ident, text_content),
                                "xml_lang": xml_lang,
                                "entry_type": el.get("type") if local == "name" else "term",
                                "source_file": source_file,
                                "source_title": source_title,
                            }
            # A name/term's own text is its label, not a bare mention --
            # never rescan it, nor anything nested inside it (see
            # process_label_subtree) -- only a further <name>/<term>
            # nested inside still needs its own registration.
            process_label_subtree(el)
            return

        if local == "hi" and len(el) == 0:
            # No carve-out for <hi> anymore (conventions doc, section 3.3 +
            # the unchanged first-occurrence rule box): if its whole text is
            # exactly a registered identity, retag the element itself into
            # a proper ref-pointing <name>/<term> rather than leaving it as
            # untracked typographic markup. <hi> remains valid as-is for
            # everything else (plain emphasis, bold table cells, ...).
            text_content = "".join(el.itertext())
            toks = list(_WORD_TOKEN_RE.finditer(text_content))
            hit = _registry_lookup(registry, [m.group() for m in toks]) if toks else None
            # Reject a token-tuple match straddled by other real content --
            # e.g. a table row's "<hi>13. Islamic Kingship</hi>" label must
            # not swallow the "13." into the retagged <term>. Only bare
            # whitespace may surround the matched span.
            if hit is not None and toks and (
                text_content[:toks[0].start()].strip()
                or text_content[toks[-1].end():].strip()
            ):
                hit = None
            if hit is not None:
                ref_id, tag, _reg_lang, _reg_rend = hit
                el.tag = f"{{{TEI_NS}}}{tag}"
                if tag != "term":
                    el.attrib.pop("rend", None)
                el.set("ref", ref_id)
                fixes.append((el.sourceline,
                              f'converted <hi> wrap of "{text_content}" into <{tag} ref="{ref_id}">'))
                return
            scan_fragment(el, "text", el.text)
            return

        if local == "hi":
            # Has child markup -- leave structure alone, but still scan its
            # own text and descend (mirrors the generic element case below).
            scan_fragment(el, "text", el.text)
            for child in list(el):
                process(child)
                scan_fragment(child, "tail", child.tail)
            return

        scan_fragment(el, "text", el.text)
        for child in list(el):
            process(child)
            scan_fragment(child, "tail", child.tail)

    process(root)
    return fixes


def _check_other_rules(root):
    """Report every other TEI-convention violation as (line, message) pairs.
    Read-only: nothing here is ever auto-corrected."""
    errors = []

    for el in root.iter():
        local = _local(el)
        if local == "name":
            if el.get("ref"):
                t = el.get("type")
                # type="blocker" is the one exception: it marks this
                # ref-pointing <name> as NOT a genuine occurrence of the
                # identity it points to -- e.g. <name ref="Proto-Indo-
                # European" type="blocker">proto</name> in "proto-lugal",
                # where "proto" merely collides with the registered id
                # rather than actually referring to it. Still valid
                # markup; see _occurrence_marker and build_concordance for
                # how it's excluded from rendering/indexing.
                if el.get("key") or el.get(_XML_ID) or (t is not None and t != "blocker"):
                    errors.append((el.sourceline,
                                    '<name ref="..."> must not also carry key/xml:id/type '
                                    '(except type="blocker")'))
            else:
                if el.get("key"):
                    errors.append((el.sourceline, '<name> uses deprecated "key" -- should be "xml:id"'))
                elif not el.get(_XML_ID):
                    errors.append((el.sourceline, '<name> first occurrence missing "xml:id" attribute'))
                t = el.get("type")
                if t is None:
                    errors.append((el.sourceline, '<name> first occurrence missing "type" attribute'))
                elif t not in _ALLOWED_NAME_TYPES:
                    errors.append((el.sourceline, f'<name type="{t}"> is not a recognized type'))
        elif local == "term":
            if el.get("ref"):
                if el.get("key") or el.get(_XML_ID):
                    errors.append((el.sourceline, '<term ref="..."> must not also carry key/xml:id'))
            else:
                if el.get("key"):
                    errors.append((el.sourceline, '<term> uses deprecated "key" -- should be "xml:id"'))
                elif not el.get(_XML_ID):
                    errors.append((el.sourceline, '<term> first occurrence missing "xml:id" attribute'))
        elif local == "hi":
            if el.get("key") or el.get(_XML_ID) or el.get("type") or el.get("ref"):
                errors.append((el.sourceline,
                               '<hi> must not carry key/xml:id/type/ref -- use <name>/<term> instead'))
        elif local == "div":
            dtype = el.get("type")
            if dtype in ("sheet", "chapter"):
                # A chapter is the sheet-level container in this project;
                # the two types are validated (and rendered) identically.
                if not el.get("n"):
                    errors.append((el.sourceline, f'<div type="{dtype}"> missing "n" attribute'))
                if not el.get(_XML_ID):
                    errors.append((el.sourceline, f'<div type="{dtype}"> missing "xml:id" attribute'))
            elif dtype == "section":
                if not el.get("n"):
                    errors.append((el.sourceline, '<div type="section"> missing "n" attribute'))
        elif local == "author":
            if el.get("ref"):
                if el.get("key") or el.get(_XML_ID):
                    errors.append((el.sourceline, '<author ref="..."> must not also carry key/xml:id'))
            else:
                if el.get("key"):
                    errors.append((el.sourceline, '<author> uses deprecated "key" -- should be "xml:id"'))
                else:
                    k = el.get(_XML_ID)
                    if not k:
                        errors.append((el.sourceline, '<author> missing "xml:id" attribute'))
                    elif not _is_author_key(k) and el.get("type") != "prophet":
                        # A prophet (Benedict_of_Nursia, Augustine) may keep
                        # an ordinary key; only a scholar must be
                        # LASTNAME-Firstname shaped.
                        errors.append((el.sourceline,
                                       f'<author xml:id="{k}"> does not match LASTNAME-Firstname format'))
            if el.get("type") not in (None, "prophet", "scholar"):
                errors.append((el.sourceline,
                               f'<author type="{el.get("type")}"> must be "prophet" or "scholar"'))
        elif local == "title" and _in_tag(el, "text", ("bibl",)):
            # The level="m"/"j"/"a" + rend="italic" rule is specific to a
            # bibliographic citation's <title>; the document's own
            # <titleStmt>/<title> metadata is a different thing entirely.
            # "m" (monograph/book) and "j" (journal/series) both name a
            # published work as a whole and are italicized; "a" (article/
            # chapter) is cited within the enclosing work and is not.
            level = el.get("level")
            if level not in ("m", "j", "a"):
                errors.append((el.sourceline, f'<title level="{level}"> must be "m", "j", or "a"'))
            elif level in ("m", "j") and el.get("rend") != "italic":
                kind = "book" if level == "m" else "journal/series"
                errors.append((el.sourceline, f'<title level="{level}"> ({kind}) missing rend="italic"'))
        elif local == "date":
            when = el.get("when")
            if not when or not _DATE_WHEN_RE.match(when):
                errors.append((el.sourceline, f'<date> missing/invalid "when" attribute ("{when}")'))
        elif local == "citedRange":
            if el.get("unit") != "page":
                errors.append((el.sourceline, '<citedRange> missing unit="page"'))
        elif local == "ptr":
            target = el.get("target") or ""
            if not target.startswith("#") or not _VALID_ID_RE.match(target[1:]):
                errors.append((el.sourceline,
                               f'<ptr target="{target}"> must be target="#id", pointing at a '
                               f'footnote\'s own xml:id'))
        elif local == "note" and el.get("place") == "foot":
            note_id = el.get(_XML_ID)
            if note_id and not _VALID_ID_RE.match(note_id):
                errors.append((el.sourceline,
                               f'<note xml:id="{note_id}"> is malformed -- the id itself must not '
                               f'include the leading "#" (that belongs only on a <ptr target="#...">)'))

    errors.extend(_check_punctuation_and_quotes(root))
    errors.extend(_check_stopword_tags(root))
    errors.extend(_check_spelling(root))
    return errors


def _check_stopword_tags(root):
    """Warn when a <name>/<term> wraps text that is, once lowercased,
    nothing but a common English stop word (see _ENGLISH_STOPWORDS) --
    the tell-tale sign of a tagger mistaking an ordinary function word for
    a proper-noun mention (this is exactly how the deity "An" ended up
    swallowing every bare article "an"/"An" across the whole corpus).

    Silent whenever the tag's own ref/xml:id is something other than that
    bare stop word (e.g. ref="An_(deity)" on the word "An"): a differing
    id shows the tag was deliberately disambiguated, not an accidental
    collision."""
    errors = []
    for el in root.iter():
        local = _local(el)
        if local not in ("name", "term"):
            continue
        text = "".join(el.itertext()).strip().lower()
        if text not in _ENGLISH_STOPWORDS:
            continue
        ident = el.get("ref") or el.get(_XML_ID)
        if ident and ident.strip().lower() == text:
            errors.append((el.sourceline,
                            f'<{local} ref="{ident}"> wraps the English stop word "{text}" -- '
                            f'likely an accidental tag/word collision, not a deliberate mention'))
    return errors


def _iter_flow_holders(el, stop_tags):
    """Like _iter_text_holders, but never descends into a child whose local
    name is in `stop_tags` -- that child's own tail (its position in the
    parent's flow) is still yielded, just not its internal subtree. Used to
    scan one unit's own flow for literal-quote conversion without either
    reaching into a nested scope unit (which gets its own separate pass)
    or an already-legitimate <q>/<quote>/<bibl>."""
    yield (el, "text")
    for child in el:
        if _local(child) not in stop_tags:
            yield from _iter_flow_holders(child, stop_tags)
        yield (child, "tail")


def _wrap_quote_span(unit, open_holder, open_attr, open_pos, close_holder, close_attr, close_pos):
    """Replace the literal quote characters at (open_holder, open_attr,
    open_pos) and (close_holder, close_attr, close_pos) -- both positions
    within `unit`'s own top-level flow, i.e. either unit's own text or a
    direct child's tail, per _convert_quotes_in_unit's screening -- with a
    new <q> element wrapping everything in between (which may be nothing
    but text, or may include whichever <name>/<term>/etc. elements happen
    to sit inside the span). Returns the new <q> element."""
    q = etree.Element(f"{{{TEI_NS}}}q")

    if open_holder is close_holder and open_attr == close_attr:
        full = getattr(open_holder, open_attr)
        before, inside, after = full[:open_pos], full[open_pos + 1:close_pos], full[close_pos + 1:]
        setattr(open_holder, open_attr, before)
        q.text = inside
        q.tail = after
        if open_attr == "text":
            unit.insert(0, q)
        else:
            unit.insert(list(unit).index(open_holder) + 1, q)
        return q

    open_full = getattr(open_holder, open_attr)
    setattr(open_holder, open_attr, open_full[:open_pos])
    q.text = open_full[open_pos + 1:]

    children = list(unit)
    start_idx = 0 if open_attr == "text" else children.index(open_holder) + 1
    end_idx = children.index(close_holder)  # close_attr must be "tail" here
    moved = children[start_idx:end_idx + 1]

    close_full = close_holder.tail or ""
    inside_last, after = close_full[:close_pos], close_full[close_pos + 1:]

    for child in moved:
        q.append(child)  # lxml detaches `child` from `unit` automatically
    moved[-1].tail = inside_last
    q.tail = after
    unit.insert(start_idx, q)
    return q


def _convert_quotes_in_unit(unit):
    """Pair up every literal quote character in `unit`'s own flow (see
    _iter_flow_holders/_QUOTE_STOP_TAGS) and wrap each pair in a new <q>,
    removing the literal characters. Conservative: if the unit contains an
    odd number of quote characters, or one that sits deeper than the
    unit's own top flow level (nested inside a child's own text rather
    than in the unit's text or a direct child's tail -- vanishingly rare,
    e.g. a quote mark baked into a <name>'s own label), the whole unit is
    left untouched so the existing literal-quote-character check can still
    flag it for a human rather than risk mispairing. Returns the fixes
    made, as (line, matched_text)."""
    fixes = []
    while True:
        occurrences = []  # (holder, attr, offset, top_level)
        for holder, attr in _iter_flow_holders(unit, _QUOTE_STOP_TAGS):
            text = getattr(holder, attr)
            if not text:
                continue
            top_level = (
                (attr == "text" and holder is unit)
                or (attr == "tail" and holder.getparent() is unit)
            )
            for i, ch in enumerate(text):
                if ch in _QUOTE_CHARS:
                    occurrences.append((holder, attr, i, top_level))
        if not occurrences:
            return fixes
        if len(occurrences) % 2 != 0 or any(not top for *_, top in occurrences):
            return fixes
        (open_holder, open_attr, open_pos, _), (close_holder, close_attr, close_pos, _) = occurrences[:2]
        line = open_holder.sourceline or unit.sourceline
        q = _wrap_quote_span(unit, open_holder, open_attr, open_pos, close_holder, close_attr, close_pos)
        matched_text = "".join(q.itertext())
        if len(matched_text) > 60:
            matched_text = matched_text[:57] + "..."
        fixes.append((line, matched_text))
        # The tree just changed under us -- rescan `unit` from scratch for
        # any further pairs rather than trying to patch up stale indices.


def convert_literal_quotes_to_q(xml_paths):
    """Auto-fix every literal quote character (see _QUOTE_CHARS) into a
    proper <q> element instead of merely flagging it. Pairs are found and
    wrapped per-unit (paragraph, heading, table cell, footnote, etc. -- see
    _QUOTE_SCOPE_TAGS/_convert_quotes_in_unit); a unit with an odd
    quote-character count, or one nested too deep to safely relocate, is
    left alone so _check_punctuation_and_quotes can still flag it for a
    human. Runs early (like strip_never_tagged_references/
    relocate_first_occurrences), before first-occurrence/auto-tag
    processing, since wrapping only moves whichever <name>/<term> elements
    happen to sit inside the new <q> -- their own content/attributes, and
    the auto-tag registry built from them, are unaffected either way.
    Returns the fixes made, as (filename, line, message)."""
    fixes = []
    for path in xml_paths:
        tree = _parse_tei_xml(path)
        root = tree.getroot()
        changed = False
        for el in list(root.iter()):
            if _local(el) not in _QUOTE_SCOPE_TAGS:
                continue
            if _in_tag(el, "text", ("bibl",)):
                continue
            for line, matched_text in _convert_quotes_in_unit(el):
                fixes.append((path.name, line, f'wrapped literal quote "{matched_text}" in <q>...</q>'))
                changed = True
        if changed:
            tree.write(str(path), encoding="utf-8", xml_declaration=False)
    return fixes


_STRAY_MARKER_RE = re.compile(r"#[wy];")


def _check_punctuation_and_quotes(root):
    """Flag literal quote characters (must use <q> instead), literal "*"
    (must use <hi rend="italic"> instead), and any stray "#w;"/"#y;" text --
    skipping bibliographic metadata, and treating quote characters inside an
    existing <q>/<quote> as legitimate nested punctuation rather than a
    violation.

    A stray "#w;"/"#y;" can never be legitimate content -- it is the
    signature of a <name>/<term> occurrence marker that leaked into plain
    text during a Typst -> XML sync from before this script wrapped marker
    calls in a real, boundable #w[...]/#y[...] invocation (see
    _apply_typst_unit / _build_inline_node). It means the element that used
    to carry that marker was flattened to bare text and its ref/xml:id/type
    lost; there's no way to safely re-derive those from the stray text
    alone, so this is reported for a human to retag by hand, never
    auto-fixed.

    NOTE: the doc's <q rend="italic"> (for genuine quotations) vs. plain
    <q> (for everything else, e.g. scare quotes/mentioned terms) distinction
    is not checked here -- telling those two uses apart requires reading
    for meaning, which isn't something this structural pass can do."""
    errors = []
    for holder, attr in _iter_text_holders(root):
        text = getattr(holder, attr)
        if not text:
            continue
        if _in_tag(holder, attr, ("bibl",)):
            continue
        line = holder.sourceline or 0
        in_quote = _in_tag(holder, attr, ("q", "quote"))
        for i, ch in enumerate(text):
            if ch in _QUOTE_CHARS and not in_quote:
                errors.append((line, f'literal quote character {ch!r} -- use <q> instead'))
            elif ch == "*":
                # The one legitimate asterisk: the scholarly marker of a
                # reconstructed form (Proto-Indo-European *dyēws), written as
                # the first character of a <hi>/<term>/<foreign> that is
                # explicitly marked with an xml:lang.
                if (i == 0 and attr == "text" and len(text) > 1 and text[1].isalpha()
                        and _local(holder) in ("hi", "term", "foreign") and holder.get(_XML_LANG)):
                    continue
                # A run of two or more ("f***") is a deliberate censoring
                # mask, not emphasis markup: it stands as typed.
                if (i > 0 and text[i - 1] == "*") or (i + 1 < len(text) and text[i + 1] == "*"):
                    continue
                errors.append((line, 'literal "*" -- use <hi rend="italic"> for emphasis instead'))
        for m in _STRAY_MARKER_RE.finditer(text):
            errors.append((line,
                            f'stray "{m.group()}" occurrence-marker text -- a corrupted '
                            f'<name>/<term> from a past sync; retag it by hand'))
    return errors


# A run of Unicode letters -- [^\W\d_], the same "any-script letter" class
# _AUTHOR_FIRSTNAME_RE/_mint_xml_id use, so accented names (Agnès, Bénigne,
# Molière, ...) tokenize whole rather than splitting at the accent -- with
# apostrophes joining a contraction/possessive into the same token (e.g.
# "Augustus's", "don't"; aspell handles those as one word fine), or a
# digit-led ordinal suffix ("18th") kept as one match so it can be
# recognized and skipped below rather than spell-checked as the bare word
# "th".
_SPELL_TOKEN_RE = re.compile(r"\d+(?:st|nd|rd|th)\b|[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
# A bare Roman numeral (regnal number, century, ...) written in the
# all-caps convention this corpus uses ("Louis XIV", "Charles VII") --
# never a spelling target, even though it isn't an English dictionary word.
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
# A bare URL in running prose (e.g. a footnote citing a web source without
# wrapping it in <ref>) -- never a spelling target.
_URL_RE = re.compile(r"https?://\S+")
# A possessive/contraction token: a base word plus 's (straight or curly
# apostrophe), e.g. "Inanna's" -> base "Inanna".
_POSSESSIVE_RE = re.compile(r"^([^'’]+)['’]s$")
_aspell_missing_warned = False


def _effective_lang(holder, attr) -> str:
    """Primary xml:lang subtag in force at this text position -- nearest
    ancestor-or-self, mirroring _in_tag's .text-vs-.tail traversal (a
    <term xml:lang="fr">'s own text is foreign, but text right after it,
    back in the parent's flow, is not). "en" if nothing overrides it
    anywhere up to the document root."""
    node = holder if attr == "text" else holder.getparent()
    while node is not None:
        lang = node.get(_XML_LANG)
        if lang:
            return lang.split("-")[0].lower()
        node = node.getparent()
    return "en"


def _load_custom_dictionary() -> list:
    """Words the corpus's own vocabulary adds on top of aspell's en_US
    dictionary -- proper nouns (Peisistratos, Girardian, ...), coined
    terms (BDMB, Nimrodic, ...), etc. One word per line in
    custom_dictionary.txt, exactly as it should appear in the text
    (matching is case-sensitive, so "BDMB" does not also whitelist
    "bdmb"); blank lines and "#"-led comments are ignored. Missing file
    just means an empty project dictionary."""
    if not SPELL_DICT_PATH.exists():
        return []
    words = []
    for line in SPELL_DICT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def _run_aspell(words: list) -> set:
    """Return the subset of `words` (exact case) that aspell's en_US
    dictionary -- extended with custom_dictionary.txt -- doesn't
    recognize. Empty set (rather than raising) if the `aspell` CLI isn't
    installed; that's reported once, directly, the same way
    compile_typst_pdf/consolidate_pdfs report a missing `typst`/`pdftk`."""
    global _aspell_missing_warned
    if not words:
        return set()
    custom_words = _load_custom_dictionary()
    with tempfile.NamedTemporaryFile("w", suffix=".pws", delete=False, encoding="utf-8") as f:
        # The trailing "utf-8" is the pws format's encoding field -- without
        # it aspell mis-decodes any accented word (e.g. "Agnès" comes back
        # as the mojibake "AgnÃ¨s" and the whole personal dict is rejected).
        f.write(f"personal_ws-1.1 en {len(custom_words)} utf-8\n")
        for w in custom_words:
            f.write(w + "\n")
        personal_path = f.name
    try:
        result = subprocess.run(
            ["aspell", "list", "--lang=en_US", f"--personal={personal_path}"],
            input="\n".join(words), capture_output=True, text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        if not _aspell_missing_warned:
            print("    ERROR: `aspell` CLI not found on PATH -- spelling check skipped")
            _aspell_missing_warned = True
        return set()
    finally:
        os.unlink(personal_path)
    if result.returncode != 0:
        if not _aspell_missing_warned:
            print(f"    ERROR running aspell -- spelling check skipped:\n{result.stderr}")
            _aspell_missing_warned = True
        return set()
    return set(result.stdout.splitlines())


def _check_spelling(root) -> list:
    """Flag words aspell's en_US dictionary -- extended with
    custom_dictionary.txt, see _load_custom_dictionary -- doesn't
    recognize, across every English-language text run outside <bibl>
    (bibliographic titles/names/publishers are exempt, like the other
    text-content checks above) and outside <name>/<term> (a term or name
    the author has deliberately tagged as one is vocabulary by definition,
    not a typo -- first occurrence and ref-pointing later mentions alike).
    A run under a non-English xml:lang (e.g.
    the Latin <term>"bellatores") is skipped entirely -- it's expected to
    fail an English dictionary, that's not a misspelling. Ordinal
    suffixes ("18th") and bare Roman numerals ("XIV") are never spelling
    targets either.

    Each distinct misspelling is reported once per file, at its first
    occurrence's line, with the total occurrence count -- not once per
    occurrence -- since a genuine corpus term (rather than a typo) tends
    to recur many times before it's added to the dictionary, and a
    warning per occurrence would just be noise for the one fix (one
    dictionary entry) that clears all of them at once.

    A bare URL in running text (e.g. a footnote citing a web source
    without wrapping it in <ref>) is skipped rather than spell-checked
    word fragment by word fragment. A square-bracketed editorial
    interpolation flush against a word -- the standard convention for
    silently completing a quotation's grammar, e.g. "receiv[e]" -- has
    its brackets dropped before tokenizing, so it reads as the one word
    "receive" instead of splitting into "receiv" plus a dangling "e".

    A word with an 's ("Inanna's") is only flagged if its base word (the
    part before the 's) is itself not recognized."""
    occurrences = {}  # word (exact case) -> [line, ...], first-seen order
    for holder, attr in _iter_text_holders(root):
        if _in_tag(holder, attr, ("bibl", "name", "term")):
            continue
        if _effective_lang(holder, attr) != "en":
            continue
        text = getattr(holder, attr)
        if not text:
            continue
        text = _URL_RE.sub(" ", text).replace("[", "").replace("]", "")
        line = holder.sourceline or 0
        for m in _SPELL_TOKEN_RE.finditer(text):
            word = m.group(0)
            if word[0].isdigit() or _ROMAN_NUMERAL_RE.match(word):
                continue
            occurrences.setdefault(word, []).append(line)

    if not occurrences:
        return []

    # A token with an 's is checked as-is AND by its base word: a possessive
    # of a word aspell (or custom_dictionary.txt) recognizes -- "Inanna's"
    # with "Inanna" in the dictionary -- is never a misspelling, even though
    # aspell doesn't accept the 's form of a personal-dictionary entry.
    to_check = set(occurrences)
    for word in occurrences:
        possessive = _POSSESSIVE_RE.match(word)
        if possessive:
            to_check.add(possessive.group(1))

    misspelled = _run_aspell(list(to_check))
    if not misspelled:
        return []

    errors = []
    for word, lines in occurrences.items():
        if word not in misspelled:
            continue
        possessive = _POSSESSIVE_RE.match(word)
        if possessive and possessive.group(1) not in misspelled:
            continue
        suffix = f" ({len(lines)} occurrences)" if len(lines) > 1 else ""
        errors.append((lines[0],
                        f'spelling: "{word}" not recognized{suffix} -- fix the typo, or if this '
                        f'is deliberate vocabulary, add it to {SPELL_DICT_PATH.name}'))
    return errors


# The sheet number in a sheet's displayed name: the digits (or "NN-MM"
# range) right before the title's " — " separator, e.g. the "04" in
# "Calneh-Nimrod 04 — Proto-Indo-European Dynamics".
_SHEET_TITLE_NUMBER_RE = re.compile(r"(?<![\w-])(\d+(?:-\d+)*)(?=\s*—)")


def _strip_sheet_number_zeros(title: str) -> str:
    """Strip the leading zero(s) from the sheet number in a sheet's name --
    "Calneh-Nimrod 04 — ..." -> "Calneh-Nimrod 4 — ...", "01-02" -> "1-2",
    "00" -> "0". Only for the name as displayed: a file name (the lexicon's/
    concordance's, a <ref target>, a PDF) keeps its zero-padded NN, so this
    is never applied to one."""
    return _SHEET_TITLE_NUMBER_RE.sub(
        lambda m: "-".join(str(int(part)) for part in m.group(1).split("-")), title
    )


def _sheet_title(root):
    """The sheet's own (non-"sub") <title>, from teiHeader/fileDesc/titleStmt,
    with the leading zero of its sheet number stripped (see
    _strip_sheet_number_zeros)."""
    title_stmt = root.find(f".//{{{TEI_NS}}}titleStmt")
    if title_stmt is None:
        return ""
    for t in title_stmt.findall(f"{{{TEI_NS}}}title"):
        if t.get("type") is None:
            # the whole title, not just its own text: a title can hold a <q>
            return _strip_sheet_number_zeros(" ".join(_plain_heading_text(t).split()))
    return ""


# A conservative set of country/region names/demonyms the user has decided
# should never be a trackable <name>/<term> entity, even where (as with
# "Iran", "India" and "Egypt") the bare country name could also denote a
# substantial ancient/classical polity or historical civilizational concept
# this kind of document actively discusses -- the user has explicitly
# weighed and accepted that tradeoff for those specific cases. A distinct,
# more specific compound entity built on the same word (e.g. "Ptolemaic
# Egypt") stays a tracked entity of its own -- only the bare country name
# is excluded. For any other name not yet reviewed (Greece, Rome/Italy,
# Christendom...), stripping it would risk deleting a legitimate
# ancient/historical reference, so leave it out of this set until the user
# names it explicitly. Extend this set only as new explicit cases turn up.
# Deliberately shared by every directory: the same strings are never tracked in any of them.
_MODERN_COUNTRY_TERMS = {
    "iran",
    "india",
    "egypt", "egyptian",
    "france", "french",
    "germany", "german",
    "spain", "spanish",
    "portugal", "portuguese",
    "netherlands", "dutch",
    "belgium", "belgian",
    "switzerland", "swiss",
    "austria", "austrian",
    "poland", "polish",
    "sweden", "swedish",
    "norway", "norwegian",
    "denmark", "danish",
    "finland", "finnish",
    "ireland", "irish",
    "scotland", "scottish",
    "wales", "welsh",
    "united kingdom", "britain", "british", "england", "english",
    "united states", "america", "american", "usa",
    "canada", "canadian",
    "mexico", "mexican",
    "brazil", "brazilian",
    "argentina", "argentinian", "argentine",
    "australia", "australian",
    "russia", "russian",
    "china", "chinese",
    "japan", "japanese",
    # Modern regions (multi-country/continental groupings), not specific
    # countries -- same reasoning: not an ancient place, not a domain
    # concept, just ordinary modern geographic vocabulary.
    "middle east", "far east",
    "western europe", "eastern europe",
    "north america", "south america", "central america", "latin america",
    "sub-saharan africa", "north africa",
    "scandinavia", "balkans", "caucasus", "central asia", "southeast asia",
    "new england",
    "europe", "european",
    # Modern cities -- same reasoning. Every spelling variant is listed,
    # since both the xml:id ("Washington_D.C.") and the displayed text
    # ("Washington, D.C." / "Washington, D.C") are matched as-is. Bare
    # "washington" is deliberately absent: it would also catch the person.
    "washington d.c.", "washington d.c", "washington dc",
    "washington, d.c.", "washington, d.c", "washington, dc",
}

# The two Testaments, as divisions of the Bible corpus, are never a distinct
# historical entity or domain concept in their own right (unlike an
# individual book, prophet, or ruler) -- so, like a modern country/region
# name, never worth a <name>/<term> tag or a lexicon entry.
_GENERIC_SOURCE_DIVISIONS = {
    "new testament", "old testament",
}

# Generic royal/noble titles used constantly across the manuscript for
# dozens of distinct, individually-tracked monarchs -- never a trackable
# entity of their own. In particular "king" must never resolve to some
# unrelated compound term (e.g. "Priest-King") just because it shares a
# word with it. Extend this set as new unambiguous cases turn up; a title
# still specific enough to denote one concrete concept (e.g. "Pharaoh",
# "Caliph") does not belong here.
_GENERIC_TITLE_NOUNS = {
    "king",
}

# Broad religion names, too generic to track as an entity of their own. A
# more specific compound concept built on one ("Islamic Kingship") is not
# affected: only the bare word itself is matched.
_GENERIC_RELIGIONS = {
    "islam",
}

# Modern events, outside the corpus's ancient/historical scope: World War II
# in each of its spellings. Only the whole phrase is matched, so a tag on
# a longer phrase containing it is not affected.
_MODERN_EVENTS = {
    "world war ii", "world war 2", "world war two",
    "second world war", "the second world war",
    "wwii", "ww ii", "ww2", "ww 2",
}

# Union of every category of phrase a <name>/<term> must never denote --
# see strip_never_tagged_references().
_NEVER_TAGGED_PHRASES = (_MODERN_COUNTRY_TERMS | _GENERIC_SOURCE_DIVISIONS | _GENERIC_TITLE_NOUNS
                         | _GENERIC_RELIGIONS | _MODERN_EVENTS)


def _unwrap_element(el):
    """Remove el from the tree, splicing its own text + tail back into the
    surrounding flow exactly where the tag used to be."""
    parent = el.getparent()
    prev = el.getprevious()
    combined = (el.text or "") + (el.tail or "")
    if prev is not None:
        prev.tail = (prev.tail or "") + combined
    else:
        parent.text = (parent.text or "") + combined
    parent.remove(el)


def strip_never_tagged_references(xml_paths):
    """A <name>/<term> must never denote a phrase in _NEVER_TAGGED_PHRASES --
    a modern country/region name (this corpus tags ancient/historical
    entities and domain concepts, and a country like France or a region like
    the Middle East is neither) or a generic Bible-corpus division ("Old
    Testament"/"New Testament", neither a distinct historical entity in its
    own right). Wherever one does (checking both the identifying key/xml:id
    and the element's own displayed text against _NEVER_TAGGED_PHRASES),
    every tag for it -- the definitional one and every ref -- is removed,
    leaving the plain text behind. Runs before relocation/validation so the
    rest of the pipeline never sees these tags. Returns the fixes made, as
    (filename, line, message)."""
    fixes = []
    for path in xml_paths:
        tree = _parse_tei_xml(path)
        root = tree.getroot()
        changed = False
        for el in list(root.iter()):
            local = _local(el)
            if local not in ("name", "term"):
                continue
            if _in_tag(el, "text", ("bibl",)):
                continue
            ident = el.get(_XML_ID) or el.get("key")
            text_content = (el.text or "").strip()
            candidates = {c.lower() for c in (ident, text_content) if c}
            candidates |= {c.replace("_", " ") for c in candidates}
            if not candidates & _NEVER_TAGGED_PHRASES:
                continue
            fixes.append((path.name, el.sourceline,
                          f'removed <{local}> tag around never-tagged phrase '
                          f'"{text_content or ident}"'))
            _unwrap_element(el)
            changed = True
        if changed:
            tree.write(str(path), encoding="utf-8", xml_declaration=False)
    return fixes


def promote_orphan_refs(xml_paths, lex_path):
    """An identity that is only ever pointed to -- every <name>/<term>/
    <author> carrying it is a ref="...", none an xml:id/key -- has no first
    occurrence, so it registers nothing: its own text and its tagging-
    dictionary strings never tag anything. Wherever that happens, the
    earliest ref in sheet order (a type="blocker" never counts) is turned
    into the first occurrence: ref="X" becomes xml:id="X". A promoted <name>
    with no type of its own takes the one its entry in the previous lexicon
    has, and a promoted <name>/<term> with no xml:lang takes the entry's.
    Runs before relocate_first_occurrences(), which may then move the new
    definition onto an even earlier untagged mention. Writes back only the
    files it changed; returns the fixes made, as (filename, line, message)."""
    lex_info = {}
    if lex_path.exists():
        for entry in _parse_tei_xml(lex_path).getroot().iter(f"{{{TEI_NS}}}entry"):
            lex_info[entry.get(_XML_ID)] = (entry.get("type"), entry.get(_XML_LANG))

    def whole_key(ident):
        return tuple(m.group().lower() for m in _WORD_TOKEN_RE.finditer(ident))

    files = [(path, _parse_tei_xml(path)) for path in xml_paths]
    defined = {}  # whole key -> the defined id, as the registry matches them
    refs = []  # (file_idx, el), every ref in sheet order
    for file_idx, (_path, tree) in enumerate(files):
        for el in tree.getroot().iter(f"{{{TEI_NS}}}name", f"{{{TEI_NS}}}term", f"{{{TEI_NS}}}author"):
            if any(_local(a) == "teiHeader" for a in el.iterancestors()):
                continue
            if el.get("ref"):
                refs.append((file_idx, el))
                continue
            ident = el.get(_XML_ID) or el.get("key")
            if ident:
                defined.setdefault(whole_key(ident), ident if _VALID_ID_RE.match(ident) else _mint_xml_id(ident))

    fixes = []
    changed = set()
    # A ref spelled differently from an existing identity that the registry
    # would still take for it ("Crowley_Aleister" for CROWLEY-Aleister) is
    # pointed at that identity's own id, not promoted into a duplicate.
    first_ref = {}  # ref_id -> (file_idx, el), the earliest ref in sheet order
    for file_idx, el in refs:
        ref_id = el.get("ref")
        canonical = defined.get(whole_key(ref_id))
        if canonical is not None:
            if canonical != ref_id:
                el.set("ref", canonical)
                changed.add(file_idx)
                fixes.append((files[file_idx][0].name, el.sourceline,
                              f'<{_local(el)} ref="{ref_id}"> pointed at the existing identity "{canonical}"'))
            continue
        if el.get("type") != "blocker":
            first_ref.setdefault(ref_id, (file_idx, el))

    for ref_id, (file_idx, el) in first_ref.items():
        local = _local(el)
        del el.attrib["ref"]
        el.set(_XML_ID, ref_id)
        lex_type, lex_lang = lex_info.get(ref_id, (None, None))
        if local == "name" and not el.get("type") and lex_type and lex_type != "term":
            el.set("type", lex_type)
        if local in ("name", "term") and not el.get(_XML_LANG) and lex_lang:
            el.set(_XML_LANG, lex_lang)
        changed.add(file_idx)
        fixes.append((files[file_idx][0].name, el.sourceline,
                      f'no first occurrence of "{ref_id}" anywhere: its earliest '
                      f'<{local} ref="{ref_id}"> made the first occurrence (xml:id)'))

    for file_idx in changed:
        path, tree = files[file_idx]
        tree.write(str(path), encoding="utf-8", xml_declaration=False)
    return fixes


def relocate_first_occurrences(xml_paths):
    """A <name>/<term>'s tag only ever gets attached to the specific
    element it started on; it doesn't know whether the same word or phrase
    was already used, untagged, earlier in the sheet sequence -- e.g. a
    scene-setting introduction that mentions "Hammurabi" in passing before
    section 1 gives him his real <name key="Hammurabi" type="ruler">. Per
    the "first occurrence" rule, THAT earlier bare mention is the one that
    should carry xml:id/type; the later, already-tagged one should become
    a ref pointer instead.

    Scans every file in xml_paths (already sheet-ordered) for exactly that
    situation and, wherever found, moves the tagging to the truly-earliest
    span and converts the original tag to ref="...". Runs before the
    per-file validate_xml pass, which then proceeds normally from a
    corpus where every first occurrence is already in the right place.
    <author> is untouched -- a bibliographic citation isn't something a
    bare prose mention could stand in for. Writes back only the files it
    actually changed; returns the fixes made, as (filename, line, message)."""
    files = []
    for path in xml_paths:
        tree = _parse_tei_xml(path)
        files.append((path, tree, tree.getroot()))

    registry = {}
    tag_info = {}  # ref_id -> {file_idx, el, local, xml_lang, rend, type}

    def collect(file_idx, el):
        local = _local(el)
        if local in ("bibl", "teiHeader", "title"):
            return
        if local in ("name", "term") and not el.get("ref"):
            key_attr = el.get("key")
            ident = el.get(_XML_ID) or key_attr
            if ident is not None:
                whole_key = tuple(m.group().lower() for m in _WORD_TOKEN_RE.finditer(ident))
                if whole_key and whole_key not in registry:
                    ref_id = ident if _VALID_ID_RE.match(ident) else _mint_xml_id(ident)
                    xml_lang = el.get(_XML_LANG)
                    rend = el.get("rend") if local == "term" else None
                    text_content = "".join(el.itertext())
                    # Key and displayed text only, never a tagging-
                    # dictionary string: a mere partial name (e.g. bare
                    # "Ishtar" for "Inanna-Ishtar") must never count as a
                    # complete enough mention to relocate a whole entity's
                    # tag onto.
                    _register_candidate(registry, ident, ref_id, local, xml_lang, rend)
                    if text_content.strip().lower() != ident.strip().lower():
                        _register_candidate(registry, text_content, ref_id, local, xml_lang, rend,
                                            case_sensitive=_is_bare_surname_display(ident, text_content))
                    tag_info[ref_id] = {
                        "file_idx": file_idx, "el": el, "local": local,
                        "type": el.get("type") if local == "name" else None,
                        "xml_lang": xml_lang, "rend": rend,
                    }
        for child in el:
            collect(file_idx, child)

    for file_idx, (_path, _tree, root) in enumerate(files):
        collect(file_idx, root)

    if not tag_info:
        return []

    # Corpus-wide document-order walk #2: now that the full registry is
    # known, record the sequence position of every first-occurrence tag
    # AND of every bare-text match of its trigger words -- a plain integer
    # counter, not sourceline, since this corpus runs whole paragraphs on
    # one source line, where sourceline can't tell earlier from later.
    seq = [0]
    tag_seq = {}
    earliest_bare = {}  # ref_id -> (seq, file_idx, holder, attr, start, end, matched_str)

    def scan(file_idx, holder, attr, text):
        if not text:
            return
        for (start, end, ref_id, _tag, _lang, _rend, matched_str) in _find_matches(text, registry):
            s = seq[0]
            seq[0] += 1
            if ref_id not in earliest_bare or s < earliest_bare[ref_id][0]:
                earliest_bare[ref_id] = (s, file_idx, holder, attr, start, end, matched_str)

    def walk(file_idx, el):
        local = _local(el)
        # A figure's caption is never where a definition should be anchored
        # (it is not part of the running text), so it is not searched for an
        # "earlier mention" either. Nor is an <author>, even outside a <bibl>:
        # its text is a person's name as cited ("Samuel" in "Samuel Noah
        # Kramer" must not become the first occurrence of the prophet).
        if local in ("bibl", "teiHeader", "title", "figure", "author"):
            return
        if local in ("name", "term"):
            if not el.get("ref"):
                key_attr = el.get("key")
                ident = el.get(_XML_ID) or key_attr
                if ident is not None:
                    whole_key = tuple(m.group().lower() for m in _WORD_TOKEN_RE.finditer(ident))
                    hit = registry.get(whole_key)
                    if hit is not None:
                        s = seq[0]
                        seq[0] += 1
                        tag_seq.setdefault(hit[0], s)
            for child in el:
                walk(file_idx, child)
                scan(file_idx, child, "tail", child.tail)
            return
        scan(file_idx, el, "text", el.text)
        for child in el:
            walk(file_idx, child)
            scan(file_idx, child, "tail", child.tail)

    for file_idx, (_path, _tree, root) in enumerate(files):
        walk(file_idx, root)

    relocations = []
    for ref_id, tseq in tag_seq.items():
        bare = earliest_bare.get(ref_id)
        if bare is not None and bare[0] < tseq:
            relocations.append((ref_id, *bare[1:]))

    if not relocations:
        return []

    # Splice every relocated entity's new definitional element in, one
    # fragment at a time (several entities can share one fragment).
    by_fragment = {}
    for ref_id, file_idx, holder, attr, start, end, matched_str in relocations:
        by_fragment.setdefault((file_idx, id(holder), attr), []).append(
            (ref_id, holder, start, end, matched_str))

    fixes = []
    for (file_idx, _hid, attr), items in by_fragment.items():
        holder = items[0][1]
        text = getattr(holder, attr)
        items.sort(key=lambda it: it[2])
        spans = []
        for ref_id, _h, start, end, matched_str in items:
            info = tag_info[ref_id]
            elem = etree.Element(f"{{{TEI_NS}}}{info['local']}")
            elem.set(_XML_ID, ref_id)
            if info["type"]:
                elem.set("type", info["type"])
            if info["xml_lang"]:
                elem.set(_XML_LANG, info["xml_lang"])
            if info["local"] == "term" and info["rend"]:
                elem.set("rend", info["rend"])
            elem.text = matched_str
            spans.append((start, end, elem))
            fixes.append((files[file_idx][0].name, holder.sourceline,
                          f'relocated first occurrence of "{matched_str}" here (an earlier '
                          f'mention than its old tag)'))
        _splice_elements(holder, attr, text, spans)

    # The original tags are now duplicates of an earlier definition --
    # convert them to ref, exactly as a normal duplicate would be.
    for ref_id, file_idx, _holder, _attr, _start, _end, _matched in relocations:
        info = tag_info[ref_id]
        el = info["el"]
        el.attrib.pop("key", None)
        el.attrib.pop(_XML_ID, None)
        el.attrib.pop("type", None)
        el.set("ref", ref_id)
        fixes.append((files[info["file_idx"]][0].name, el.sourceline,
                      f'converted now-superseded first-occurrence tag (id="{ref_id}") '
                      f'into ref="{ref_id}"'))

    touched = {fname for fname, _line, _msg in fixes}
    for path, tree, _root in files:
        if path.name in touched:
            tree.write(str(path), encoding="utf-8", xml_declaration=False)

    return fixes


def validate_xml(xml_path: Path, registry=None, lexicon=None) -> bool:
    """Run the first-occurrence/subsequent-use auto-fix pass against
    xml_path, then report (file:line) every other TEI-convention violation
    found. Returns True if the file was modified on disk. `registry` and
    `lexicon`, when given, should be shared across every sheet file in the
    sequence (see _process_first_occurrence)."""
    tree = _parse_tei_xml(xml_path)
    root = tree.getroot()
    source_title = _sheet_title(root)

    fixes = _process_first_occurrence(
        root, registry=registry, lexicon=lexicon,
        source_file=xml_path.stem, source_title=source_title,
    )
    modified = bool(fixes)
    if fixes:
        for line, msg in sorted(fixes, key=lambda f: (f[0] or 0)):
            print(f"  {_tag('FIX')}{xml_path.name}:{line}: {msg}")
        tree.write(str(xml_path), encoding="utf-8", xml_declaration=False)
        tree = _parse_tei_xml(xml_path)
        root = tree.getroot()

    errors = _check_other_rules(root)
    for line, msg in sorted(errors, key=lambda e: (e[0] or 0)):
        print(f"  {_tag('WARN')}{xml_path.name}:{line}: {msg}")

    return modified


_LEXICON_CATEGORY_HEAD = "All Entries"


def _load_existing_lexicon_defs(path: Path) -> dict:
    """Return {xml:id: [(n, def_element), ...]} from a previously generated
    lexicon file, so a regeneration never clobbers hand-written
    definitions -- including any hand-added second/third <sense>
    disambiguating a word with multiple meanings, and any inline markup
    (e.g. <hi>) inside a <def>, not just its bare text. Empty dict if the
    file doesn't exist yet or won't parse."""
    if not path.exists():
        return {}
    try:
        tree = _parse_tei_xml(path)
    except etree.XMLSyntaxError:
        return {}
    defs = {}
    for entry in tree.getroot().iter(f"{{{TEI_NS}}}entry"):
        xid = entry.get(_XML_ID)
        if not xid:
            continue
        senses = []
        for i, sense in enumerate(entry.findall(f"{{{TEI_NS}}}sense"), start=1):
            def_el = sense.find(f"{{{TEI_NS}}}def")
            if def_el is not None and "".join(def_el.itertext()).strip():
                senses.append((sense.get("n") or str(i), copy.deepcopy(def_el)))
        if senses:
            defs[xid] = senses
    return defs


def _sorted_lexicon_xids(lexicon_entries: dict) -> list:
    """xml:ids of lexicon_entries in the corpus-wide alphabetical order
    shared by the lexicon and the concordance: by xml:id, so a person's
    LASTNAME-Firstname key puts the last name first ("GIRARD-Rene" files
    under G, not under R for "René Girard"). Case-insensitive, with "_" and
    "-" read as word breaks (WEST-Martin_L before WESTENHOLZ-Aage) and
    diacritics folded, so e.g. Männerbund sorts as if spelled "Mannerbund",
    not after every plain-ASCII "M" word -- 'ä' sorts after 'z' by raw
    codepoint otherwise. The raw xml:id breaks ties."""
    def fold(k):
        return " ".join(re.split(r"[_\-\s]+", _strip_diacritics(k).lower()))
    return sorted(lexicon_entries, key=lambda k: (fold(k), k))


def _build_tei_doc(title: str, description: str):
    """Common <TEI><teiHeader>.../><text><body> skeleton shared by the
    generated lexicon and concordance files. Returns (tei_root, body_el)."""
    tei = etree.Element(f"{{{TEI_NS}}}TEI", nsmap={None: TEI_NS})
    header = etree.SubElement(tei, f"{{{TEI_NS}}}teiHeader")
    file_desc = etree.SubElement(header, f"{{{TEI_NS}}}fileDesc")
    title_stmt = etree.SubElement(file_desc, f"{{{TEI_NS}}}titleStmt")
    etree.SubElement(title_stmt, f"{{{TEI_NS}}}title").text = title
    pub_stmt = etree.SubElement(file_desc, f"{{{TEI_NS}}}publicationStmt")
    etree.SubElement(pub_stmt, f"{{{TEI_NS}}}p").text = description
    src_desc = etree.SubElement(file_desc, f"{{{TEI_NS}}}sourceDesc")
    etree.SubElement(src_desc, f"{{{TEI_NS}}}p").text = "Derived from the TEI XML sheet files in this directory."

    text_el = etree.SubElement(tei, f"{{{TEI_NS}}}text")
    body = etree.SubElement(text_el, f"{{{TEI_NS}}}body")
    return tei, body


def _build_entry_shell(cat_div, n: int, xid: str, info: dict, existing_defs: dict):
    """Append an <entry> to cat_div with its identity attributes,
    <form>/<orth>, and <sense>/<def> list (carried over from
    existing_defs, same "never clobber a hand-written def" rule as
    generate_lexicon_file) -- everything an entry needs except its own
    <xr> occurrence(s), which the caller appends itself. Returns the new
    <entry> element."""
    entry = etree.SubElement(cat_div, f"{{{TEI_NS}}}entry")
    entry.set("n", str(n))
    entry.set(_XML_ID, xid)
    if info["entry_type"]:
        entry.set("type", info["entry_type"])
    if info["xml_lang"] and info["xml_lang"] != "en":
        entry.set(_XML_LANG, info["xml_lang"])
    form = etree.SubElement(entry, f"{{{TEI_NS}}}form")
    form.set("type", "lemma")
    etree.SubElement(form, f"{{{TEI_NS}}}orth").text = info["lemma"]
    for sn, def_content in existing_defs.get(xid, [("1", None)]):
        sense = etree.SubElement(entry, f"{{{TEI_NS}}}sense")
        sense.set("n", sn)
        if def_content is None:
            etree.SubElement(sense, f"{{{TEI_NS}}}def").text = "To be determined"
        else:
            sense.append(copy.deepcopy(def_content))
    return entry


def _finalize_entries_file(tei, cat_div, out_path: Path, after_indent=None) -> None:
    """Shared indent/format/write pass for the lexicon and concordance
    files. Collapses each entry's <form>, its <sense> (only when there's
    exactly one -- a word with multiple meanings is left expanded across
    multiple lines instead, matching how those are hand-authored), and
    every <xr> back to a single line each: indent() just put every
    element on its own line, including these -- undo that one level down
    (each collapsed container has exactly one child) while leaving the
    surrounding entry-level indentation (its own .tail) untouched. Mixed
    content inside (a <def> or <ref> with a <hi> in it) is left alone
    regardless -- indent() never reformats around non-whitespace text.
    Then separates entries with a blank line and writes out_path.
    after_indent, if given, is called right after the indent pass, to add
    content whose mixed text indent() must not touch (the bibliography)."""
    etree.indent(tei, space="  ")
    if after_indent is not None:
        after_indent()
    for entry in tei.iter(f"{{{TEI_NS}}}entry"):
        form = entry.find(f"{{{TEI_NS}}}form")
        if form is not None:
            form.text = None
            for child in form:
                child.tail = None
        senses = entry.findall(f"{{{TEI_NS}}}sense")
        if len(senses) == 1:
            senses[0].text = None
            for child in senses[0]:
                child.tail = None
        for xr in entry.findall(f"{{{TEI_NS}}}xr"):
            xr.text = None
            for ref in xr:
                ref.tail = None
                # A concordance <ref> with no context words on one/both
                # sides (occurrence at the very edge of its container) has
                # nothing but whitespace around its <hi> child to begin
                # with, so indent() treats it as purely structural and
                # reformats it multi-line same as it would an empty <xr>/
                # <ref> pair -- undo that here too, the same way as above.
                if ref.text is not None and not ref.text.strip():
                    ref.text = None
                for grandchild in ref:
                    if grandchild.tail is not None and not grandchild.tail.strip():
                        grandchild.tail = None

    entries = cat_div.findall(f"{{{TEI_NS}}}entry")
    for entry in entries[:-1]:
        entry.tail = "\n" + (entry.tail or "")

    # Skip the write entirely when the content hasn't actually changed --
    # touching out_path would otherwise bump its mtime on every run
    # regardless, defeating the Make-like staleness check _emit_typst_and_pdf
    # uses to decide whether Sheet 98/99's own .typ/.pdf need rebuilding.
    new_bytes = etree.tostring(tei, encoding="utf-8", xml_declaration=False)
    if out_path.exists() and out_path.read_bytes() == new_bytes:
        return
    out_path.write_bytes(new_bytes)


def generate_lexicon_file(lexicon_entries: dict, out_path: Path, xml_files=()) -> None:
    """Write the corpus-wide lexicon (every first-occurrence <name>/<term>
    key gathered across the sheet sequence by _process_first_occurrence) to
    out_path as a TEI <div type="lexicon"> document, alphabetized by
    headword. An entry's <def> is carried over unchanged from any previous
    version of the file; only a genuinely new entry gets "To be determined".
    The lexicon is followed by the book's bibliography, built from xml_files
    (see build_bibliography)."""
    _complete_author_lemmas(lexicon_entries, xml_files)
    existing_defs = _load_existing_lexicon_defs(out_path)
    bibls = build_bibliography(xml_files, lexicon_entries, out_path)
    tei, body = _build_tei_doc(
        _LEXICON_TITLE,
        "Auto-generated by generate_book.py from every first-occurrence "
        "<name>/<term> key across the sheet sequence; regenerated on every "
        "run. Only each entry's <def> text is hand-authored and preserved. "
        "The bibliography is rebuilt from every <bibl> of the sheets; only the "
        "entries of type=\"source\" identities (corresp=\"#key\") are "
        "hand-authored and preserved.",
    )
    lex_div = etree.SubElement(body, f"{{{TEI_NS}}}div")
    lex_div.set("type", "lexicon")
    etree.SubElement(lex_div, f"{{{TEI_NS}}}head").text = "Lexicon"
    cat_div = etree.SubElement(lex_div, f"{{{TEI_NS}}}div")
    cat_div.set("type", "category")
    etree.SubElement(cat_div, f"{{{TEI_NS}}}head").text = _LEXICON_CATEGORY_HEAD

    for n, xid in enumerate(_sorted_lexicon_xids(lexicon_entries), start=1):
        info = lexicon_entries[xid]
        entry = _build_entry_shell(cat_div, n, xid, info, existing_defs)
        xr = etree.SubElement(entry, f"{{{TEI_NS}}}xr")
        xr.set("type", "first-occurrence")
        ref = etree.SubElement(xr, f"{{{TEI_NS}}}ref")
        ref.set("target", f"{info['source_file']}.xml")
        ref.text = info["source_title"]

    _finalize_entries_file(tei, cat_div, out_path, lambda: _append_bibliography(body, bibls))


# ──────────────────────────────────────────────────────────────────────────────
# BIBLIOGRAPHY (Sheet 99, after the lexicon)
# ──────────────────────────────────────────────────────────────────────────────

_BIBLIOGRAPHY_HEAD = "Bibliography"
# A locator in front of a <citedRange> ("p. 45", "pp. 3-15", "lines 16-22").
_BIBL_LOCATOR_RE = re.compile(r"\s*(?:\b(?:pp?|lines?)\.?\s*)?<citedRange\b[^>]*>.*?</citedRange>", re.S)
_BIBL_IMPRINT_END_RE = re.compile(r"</(?:date|publisher|pubPlace)>")
_BIBL_LAST_PART_RE = re.compile(r"</(?:title|biblScope|date|author)>")
_BIBL_REF_RE = re.compile(r"\s*,?\s*(<ref\b[^>]*>.*?</ref>)", re.S)
_NAME_SUFFIXES = ("Jr.", "Sr.", "Jr", "Sr")
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
_LETTER_FOLD = str.maketrans({"æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "œ": "oe", "Œ": "OE",
                              "ß": "ss", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ł": "l", "Ł": "L"})


def _bib_fold(s, numerals=True):
    """Lowercased, diacritic- and punctuation-free form of s, words separated
    by single spaces: the spelling bibliography entries are compared and
    sorted on. With numerals, "Vol."/"Volume" and a lone roman numeral fold
    to one form, so "Vol. 1" and "Volume I" name the same book (for comparing
    titles only: "Inana I" must still sort as I, not 1)."""
    s = _strip_diacritics((s or "").translate(_LETTER_FOLD)).lower().replace("&", " and ")
    words = re.sub(r"[^\w\s]|_", " ", s).split()
    if numerals:
        words = ["vol" if w == "volume" else _ROMAN.get(w, w) for w in words]
    return " ".join(words)


def _bib_sort_title(s):
    """_bib_fold(s) without a leading article, for sorting by title."""
    folded = _bib_fold(s, numerals=False)
    for art in ("the ", "a ", "an "):
        if folded.startswith(art):
            return folded[len(art):]
    return folded


def _bib_author_fold(key):
    """The part of an author key two spellings of the same person share:
    a LASTNAME-Firstname key's last name with "_" and spaces dropped
    ("MAC_CANA" and "MACCANA" both fold to "maccana"), any other key whole."""
    split = _split_author_key(key or "")
    return re.sub(r"\s", "", _bib_fold(split[0] if split else key))


def _bib_author_sort(key):
    """Sort key of an author key: last name, then first name(s), as spelled in
    the key ("DE_WAAL-Frans" sorts under D, "Ibn_Fadlan" under I)."""
    split = _split_author_key(key or "")
    return (_bib_fold(split[0]), _bib_fold(split[1])) if split else (_bib_fold(key), "")


def _invert_author(author_el):
    """Rewrite a first author's display name "Forename Surname" as TEI
    <surname>Surname</surname>, <forename>Forename</forename> -- last name
    first, as in the key. The surname is the run of words, from wherever the
    key's last name starts, to the end of the name ("Wendy Doniger O'Flaherty"
    under DONIGER, "Frans de Waal" under DE_WAAL); a trailing "Jr."/"Sr." is
    moved after the forename, and a comma follows the inverted name before an
    "and" or "et al.". A key that is not LASTNAME-Firstname-shaped (Augustine,
    Ibn_Fadlan, ...), a single-word name, or one in which the key's last name
    cannot be found ("Pliny the Elder" under PLINY) is left as is."""
    split = _split_author_key(author_el.get("ref") or "")
    if split is None or len(author_el):
        return
    words = " ".join((author_el.text or "").split()).split(" ")
    suffix = None
    if len(words) > 2 and words[-1] in _NAME_SUFFIXES:
        suffix = words.pop()
    if len(words) < 2:
        return
    target = re.sub(r"\s", "", _bib_fold(split[0]))
    start = None
    for i in range(1, len(words)):
        acc = ""
        for w in words[i:]:
            acc += re.sub(r"\s", "", _bib_fold(w))
            if acc == target:
                start = i
                break
            if not target.startswith(acc):
                break
        if start is not None:
            break
    if start is None:
        return
    author_el.text = None
    surname = etree.SubElement(author_el, f"{{{TEI_NS}}}surname")
    surname.text = " ".join(words[start:])
    surname.tail = ", "
    forename = etree.SubElement(author_el, f"{{{TEI_NS}}}forename")
    forename.text = " ".join(words[:start])
    if suffix:
        forename.tail = f", {suffix}"
    tail = author_el.tail or ""
    if re.match(r"\s+(?:and\b|et al\.)", tail):
        author_el.tail = "," + tail


def _clean_bibl_markup(inner):
    """A citation's inner markup reduced to a bibliography entry: every page
    reference (a <citedRange> and the "p."/"pp."/"lines" in front of it) is
    dropped, and so is whatever locator follows the publication data -- the
    imprint's closing parenthesis (or, without an imprint, the last title,
    volume, date or author) ends the entry, except for a <ref> link right
    after it. Authors become refs (an entry defines no identity), and the
    entry ends with a single period."""
    s = _BIBL_LOCATOR_RE.sub("", inner)
    s = re.sub(r'(<author\b[^>]*?)\s+type="[^"]*"', r"\1", s)
    s = re.sub(r'(<author\b[^>]*?)\bxml:id=', r"\1ref=", s)
    imprint = list(_BIBL_IMPRINT_END_RE.finditer(s))
    cut = -1
    if imprint:
        cut = s.find(")", imprint[-1].end())
        cut = cut + 1 if cut != -1 else -1
    if cut == -1:
        last = list(_BIBL_LAST_PART_RE.finditer(s))
        cut = last[-1].end() if last else len(s)
    head, rest = s[:cut], s[cut:]
    link = _BIBL_REF_RE.match(rest)
    if link:
        head += ", " + link.group(1)
    head = " ".join(head.split()).strip(" ,;:.")
    return head + "."


def _bibl_inner_markup(bibl):
    """bibl's content (text and children, not the tag itself) as markup,
    any nested <bibl> (the source a citation was quoted in) left out."""
    b = copy.deepcopy(bibl)
    for nested in b.findall(f"{{{TEI_NS}}}bibl"):
        tail = nested.tail or ""
        parent = nested.getparent()
        prev = nested.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(nested)
    raw = (b.text and xml_escape(b.text) or "") + "".join(
        etree.tostring(c, encoding="unicode", with_tail=True) for c in b)
    return re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", raw)


def _bib_record(el, corresp=None):
    """The comparison data of a bibliography entry element: its author keys,
    the title of the work it is (an article's own title, else its first
    title), its date and publisher, whether it has an imprint, and its text."""
    authors = [a.get("ref") for a in el.findall(f"{{{TEI_NS}}}author") if a.get("ref")]
    titles = el.findall(f"{{{TEI_NS}}}title")
    work = next((t for t in titles if t.get("level") == "a"), titles[0] if titles else None)
    date = el.find(f"{{{TEI_NS}}}date")
    publisher = el.find(f"{{{TEI_NS}}}publisher")
    text = " ".join("".join(el.itertext()).split())
    return {
        "el": el,
        "corresp": corresp,
        "authors": authors,
        "work": _bib_fold("".join(work.itertext())) if work is not None else "",
        "work_sort": _bib_sort_title("".join(work.itertext())) if work is not None else "",
        "date": date.get("when") if date is not None else None,
        "publisher": _bib_fold("".join(publisher.itertext())) if publisher is not None else None,
        "imprint": el.find(f"{{{TEI_NS}}}pubPlace") is not None or publisher is not None,
        "text": text,
    }


def _bib_author_signature(el):
    """The authorship of a bibliography entry, as (author keys, "et al.")
    -- empty for an anonymous work or an institutional author (plain text
    before the first element). Two entries with equal signatures have
    exactly the same authors, which is what the 3-em dash stands for."""
    if (el.text or "").strip():
        return ()
    authors = el.findall(f"{{{TEI_NS}}}author")
    if not authors or el[0] is not authors[0]:
        return ()
    et_al = bool(re.match(r"\s*,?\s*et al\.", authors[-1].tail or ""))
    return (tuple(a.get("ref") or "".join(a.itertext()) for a in authors), et_al)


def _bib_same_work(a, b):
    """True if two entries cite the same work: same first author (as folded
    by _bib_author_fold, or both anonymous) and one title equal to, or a
    short form of, the other ("Frederick II" / "Frederick II: A Medieval
    Emperor"); or the same title and the same year and publisher whatever the
    author's key (one edition cited under two keys)."""
    if not a["work"] or not b["work"]:
        return False
    fa = _bib_author_fold(a["authors"][0]) if a["authors"] else None
    fb = _bib_author_fold(b["authors"][0]) if b["authors"] else None
    short, long_ = sorted((a["work"], b["work"]), key=len)
    if fa == fb and (long_ == short or long_.startswith(short + " ")):
        return True
    return (a["work"] == b["work"] and a["date"] and a["date"] == b["date"]
            and a["publisher"] == b["publisher"])


def _bib_completeness(r):
    return (r["imprint"], r["date"] is not None, len(r["text"]))


def _load_existing_source_bibls(path: Path) -> dict:
    """{source key: <bibl> element} from the bibliography of a previously
    generated Sheet 99: the entry of every type="source" lexicon identity
    (its corresp="#key"), kept as hand-edited on regeneration -- the same
    rule as a lexicon <def>."""
    if not path.exists():
        return {}
    try:
        root = _parse_tei_xml(path).getroot()
    except etree.XMLSyntaxError:
        return {}
    out = {}
    for div in root.iter(f"{{{TEI_NS}}}div"):
        if div.get("type") != "bibliography":
            continue
        for b in div.iter(f"{{{TEI_NS}}}bibl"):
            corresp = (b.get("corresp") or "").lstrip("#")
            if corresp:
                out[corresp] = copy.deepcopy(b)
    return out


def _source_shell(key, lemma):
    """The default bibliography entry of a source identity: an author-shaped
    key (a person, e.g. PROCOPIUS-Procopius) as the author with no title yet,
    anything else as the title of an anonymous work."""
    b = etree.Element(f"{{{TEI_NS}}}bibl")
    if _is_author_key(key):
        a = etree.SubElement(b, f"{{{TEI_NS}}}author")
        a.set("ref", key)
        a.text = lemma
        a.tail = "."
    else:
        t = etree.SubElement(b, f"{{{TEI_NS}}}title")
        t.set("level", "m")
        t.set("rend", "italic")
        t.text = lemma
        t.tail = "."
    return b


def build_bibliography(xml_files, lexicon_entries: dict, lex_path: Path) -> list:
    """The bibliography entries of the book, as <bibl> elements in order:
    every work cited in a <bibl> of the chapters (a nested <bibl>, the source
    a quotation was taken from, counting as a citation of its own; a <bibl>
    with no <title>, such as a scripture reference, is not a work), plus
    every type="source" lexicon identity -- the latter kept as hand-edited
    from the previous file (see _load_existing_source_bibls).

    Page references and locators are dropped (_clean_bibl_markup); two
    citations of the same work (_bib_same_work) give one entry, the most
    complete one. A source whose key is the author of another entry
    (Herodotus, cited for his Histories) has no entry of its own. Entries
    are sorted by first author key -- last name first -- then title; an
    anonymous work sorts by its title, an institutional author by its name.
    The first author's name is written last name first (_invert_author)."""
    records = []
    for path in xml_files:
        root = _parse_tei_xml(path).getroot()
        for bibl in root.iter(f"{{{TEI_NS}}}bibl"):
            if _in_any_tag(bibl, ("teiHeader",)):
                continue
            if bibl.find(f"{{{TEI_NS}}}title") is None:
                continue
            markup = _clean_bibl_markup(_bibl_inner_markup(bibl))
            el = etree.fromstring(f'<bibl xmlns="{TEI_NS}">{markup}</bibl>')
            records.append(_bib_record(el))

    existing = _load_existing_source_bibls(lex_path)
    sources = []
    for key, info in lexicon_entries.items():
        if info.get("entry_type") != "source":
            continue
        el = existing.get(key)
        if el is None:
            el = _source_shell(key, info["lemma"])
        el.set("corresp", f"#{key}")
        sources.append(_bib_record(el, corresp=key))

    cited_authors = {k for r in records for k in r["authors"]}
    cited_authors |= {k for r in sources for k in r["authors"] if k != r["corresp"]}
    sources = [r for r in sources if r["corresp"] not in cited_authors]

    merged = []
    for r in records + sources:
        for i, m in enumerate(merged):
            if _bib_same_work(m, r):
                best, other = (r, m) if _bib_completeness(r) > _bib_completeness(m) else (m, r)
                corresp = best["corresp"] or other["corresp"]
                if corresp:
                    best["el"].set("corresp", f"#{corresp}")
                    best["corresp"] = corresp
                merged[i] = best
                break
        else:
            merged.append(r)

    def sort_key(r):
        if r["authors"]:
            who = _bib_author_sort(r["authors"][0])
        else:
            lead = r["el"].text or ""
            who = (_bib_sort_title(lead) if lead.strip(" ,") else r["work_sort"], "")
        # The works of one first author: alone first, then with each list of
        # co-authors (an "et al." group after the named ones), so that entries
        # with the same authors stay together for the 3-em dash.
        sig = _bib_author_signature(r["el"])
        coauthors = tuple(_bib_author_sort(k) for k in sig[0][1:]) if sig else ()
        et_al = sig[1] if sig else False
        return (who, et_al, coauthors, r["work_sort"], r["date"] or "", r["text"])

    out = []
    for r in sorted(merged, key=sort_key):
        el = r["el"]
        first = el.find(f"{{{TEI_NS}}}author")
        if first is not None:
            _invert_author(first)
        out.append(el)
    return out


def _complete_author_lemmas(lexicon_entries: dict, xml_files) -> None:
    """Give every LASTNAME-Firstname identity of lexicon_entries its full
    name as lemma. The lemma is first taken from the displayed text of the
    tag that created the entry, which is often a short form -- the surname
    alone when the person was first cited as an <author> and the entry came
    from a later <name>, "Westenholz" for WESTENHOLZ-Aage. Instead, the
    longest spelling of the identity anywhere in xml_files (any <name> or
    <author> carrying it, blockers excepted) that contains the key's last
    name as a whole word wins: "Aage Westenholz". When no spelling contains
    it ("Diana", "Rousseauian"), the name is rebuilt from the key: first
    name(s), then the last name capitalized ("Diana Broughton", "Jean-Jacques
    Rousseau"; "Pliny" for the one-name PLINY-Pliny, "Haimo of Auxerre" for
    the epithet HAIMO_OF_AUXERRE-Haimo). A one-letter last name
    (E-Anna, which only looks like an author key) is left alone. Mutates
    lexicon_entries in place, so the concordance gets the same lemmas."""
    keys = {}
    for k in lexicon_entries:
        split = _split_author_key(k)
        if split and len(re.sub(r"\W|_", "", split[0])) > 1:
            keys[k] = split
    if not keys:
        return
    spellings = {k: set() for k in keys}
    for path in xml_files:
        for el in _parse_tei_xml(path).getroot().iter(f"{{{TEI_NS}}}name", f"{{{TEI_NS}}}author"):
            k = el.get(_XML_ID) or el.get("ref")
            if k in spellings and el.get("type") != "blocker":
                text = " ".join("".join(el.itertext()).split())
                if text:
                    spellings[k].add(text)
    for k, (last, first) in keys.items():
        surname = re.sub(r"\s", "", _bib_fold(last, numerals=False))
        current = lexicon_entries[k]["lemma"]

        def has_surname(text):
            # Whole words, compared without the breaks inside them: "O'Brien"
            # folds to "o brien" and must still match OBRIEN.
            words = _bib_fold(text, numerals=False).split()
            return any("".join(words[i:j]) == surname
                       for i in range(len(words)) for j in range(i + 1, len(words) + 1))

        full = [t for t in spellings[k] if has_surname(t)]
        if full:
            best = max(full, key=lambda t: (len(t), t == current, t))
        else:
            fore = " ".join(w + "." if len(w) == 1 else w for w in first.split("_"))
            sur = " ".join(w.lower() if w in ("OF", "THE") else w.capitalize() for w in last.split("_"))
            if sur.lower() == fore.lower() or sur.lower().startswith(fore.lower() + " "):
                best = sur  # a one-name key, or an epithet: HAIMO_OF_AUXERRE-Haimo
            else:
                best = f"{fore} {sur}"
        lexicon_entries[k]["lemma"] = best


def _append_bibliography(body, bibls) -> None:
    """Append the <div type="bibliography"> (a <head> and a <listBibl>, each
    entry on its own line) to the already-indented <body> of Sheet 99."""
    if not bibls:
        return
    last = body[-1] if len(body) else None
    if last is not None:
        last.tail = "\n      "
    div = etree.SubElement(body, f"{{{TEI_NS}}}div")
    div.set("type", "bibliography")
    div.text = "\n        "
    head = etree.SubElement(div, f"{{{TEI_NS}}}head")
    head.text = _BIBLIOGRAPHY_HEAD
    head.tail = "\n        "
    lst = etree.SubElement(div, f"{{{TEI_NS}}}listBibl")
    lst.text = "\n          "
    lst.tail = "\n      "
    for i, b in enumerate(bibls):
        b.tail = "\n          " if i < len(bibls) - 1 else "\n        "
        lst.append(b)
    div.tail = "\n    "



# ──────────────────────────────────────────────────────────────────────────────
# CONCORDANCE GENERATION
# ──────────────────────────────────────────────────────────────────────────────

_CONCORDANCE_CONTEXT_WORDS = 5


def _find_context_container(el):
    """Nearest ancestor of el that scopes flowing inline content (a <p>,
    <head>, <cell>, ... -- see _QUOTE_SCOPE_TAGS) -- the window the
    concordance draws its +/-5-word context from. Falls back to el's
    immediate parent if none qualifies (e.g. el sits directly under a
    <div>)."""
    node = el.getparent()
    while node is not None and _local(node) not in _QUOTE_SCOPE_TAGS:
        node = node.getparent()
    return node if node is not None else el.getparent()


def _split_around_occurrence(container, target):
    """Return (before, occurrence, after): container's flowing text (tags
    stripped) split into what precedes target's own span, target's own
    text (including anything nested inside it, e.g. a <q> split across a
    label), and what follows. A footnote (<note place="foot">) is a
    floating aside, not part of the sentence it's anchored to -- its own
    content never contributes context words (matching how _inline()
    renders it as a separate #footnote[...], not inline text), though
    whatever text continues the flow right after its marker still does."""
    phase = [0]
    parts = {0: [], 1: [], 2: []}

    def emit(s):
        if s:
            parts[phase[0]].append(s)

    def walk(node, top=False):
        is_target = node is target
        if is_target:
            phase[0] = 1
        # Only a *nested* note (reached while descending, not the
        # container itself) is a floating aside to skip -- when the
        # container passed in IS the note (the target sits directly
        # inside a footnote and _find_context_container picked the
        # footnote's own flow as the context window), its content must
        # still be walked normally.
        if top or _local(node) != "note":
            emit(node.text)
            for child in node:
                walk(child)
                emit(child.tail)
        if is_target:
            phase[0] = 2

    walk(container, top=True)
    return "".join(parts[0]), "".join(parts[1]), "".join(parts[2])


def _in_any_tag(el, tags) -> bool:
    """True if el is a descendant of an element whose local name is in tags."""
    p = el.getparent()
    while p is not None:
        if _local(p) in tags:
            return True
        p = p.getparent()
    return False


_WORD_SPAN_RE = re.compile(r"\S+")


def _last_words_suffix(text, n):
    """Return the raw suffix of text starting at its n-th-from-the-end
    whitespace-delimited token (the whole (whitespace-collapsed) text if
    it has fewer than n). Slicing rather than re-joining tokens preserves
    the exact original character gluing the last word to whatever follows
    it (a space, or punctuation with none, e.g. an occurrence right after
    an opening parenthesis) instead of guessing one."""
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    matches = list(_WORD_SPAN_RE.finditer(text))
    if not matches:
        return ""
    start = matches[max(0, len(matches) - n)].start()
    return text[start:]


def _first_words_prefix(text, n):
    """Return the raw prefix of text up through its n-th whitespace-
    delimited token (the whole (whitespace-collapsed) text if it has
    fewer than n) -- the mirror of _last_words_suffix, for the context
    words after an occurrence."""
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    matches = list(_WORD_SPAN_RE.finditer(text))
    if not matches:
        return ""
    end = matches[min(n, len(matches)) - 1].end()
    return text[:end]


def build_concordance(xml_files, lexicon_entries: dict) -> dict:
    """Scan every (already-validated, on-disk) sheet in xml_files for
    every <name>/<term> occurrence -- the first, xml:id-bearing one and
    every ref-pointing subsequent mention alike -- and return {xml:id:
    [occurrence, ...]}, each list in corpus reading order (xml_files is
    already sheet-ordered; within a file, document order). Each
    occurrence is {"source_file", "line", "before", "occurrence_text",
    "after"}: up to _CONCORDANCE_CONTEXT_WORDS words on each side, drawn
    from the occurrence's nearest enclosing flow container (see
    _find_context_container) -- fewer if the container itself runs out
    before reaching that many -- with the exact original spacing/
    punctuation gluing them to the occurrence preserved (see
    _last_words_suffix/_first_words_prefix). Skips anything inside
    <bibl>/<teiHeader>/<title>, matching what _process_first_occurrence
    itself never tags. Also skips a <name ref="..." type="blocker">: it's
    deliberately not a genuine occurrence of the identity it points to
    (see _occurrence_marker)."""
    concordance = {}
    for xml_path in xml_files:
        root = _parse_tei_xml(xml_path).getroot()
        source_file = xml_path.stem
        for tag in ("name", "term"):
            for el in root.iter(f"{{{TEI_NS}}}{tag}"):
                if _in_any_tag(el, ("bibl", "teiHeader", "title")):
                    continue
                if el.get("type") == "blocker":
                    continue
                ident = el.get(_XML_ID) or el.get("ref")
                if not ident or ident not in lexicon_entries:
                    continue
                container = _find_context_container(el)
                if container is None:
                    continue
                before, occurrence, after = _split_around_occurrence(container, el)
                occurrence_text = " ".join(occurrence.split())
                if not occurrence_text:
                    continue
                concordance.setdefault(ident, []).append({
                    "source_file": source_file,
                    "line": el.sourceline,
                    "before": _last_words_suffix(before, _CONCORDANCE_CONTEXT_WORDS),
                    "occurrence_text": occurrence_text,
                    "after": _first_words_prefix(after, _CONCORDANCE_CONTEXT_WORDS),
                })
    return concordance


def generate_concordance_file(lexicon_entries: dict, concordance: dict, out_path: Path, lex_path: Path) -> None:
    """Write the corpus-wide concordance (same entry set/order as
    generate_lexicon_file) to out_path as a TEI <div type="concordance">
    document: each entry lists every occurrence of its identity across the
    sheet sequence as its own <xr>, instead of just the first. Each <xr>'s
    <ref target="..."> keeps the same target (the sheet file name) the
    lexicon uses, plus an n="..." line number, and its text is a +/-5-word
    context window with the occurrence itself wrapped in <hi rend="bold">.
    Unlike the lexicon, an entry's <def> here is never hand-authored in
    this file itself -- it's copied fresh from lex_path (Sheet 99) every
    run, so the two files never drift apart; hand-edit the definition in
    the lexicon and it carries over here automatically."""
    existing_defs = _load_existing_lexicon_defs(lex_path)
    tei, body = _build_tei_doc(
        _CONCORDANCE_TITLE,
        "Auto-generated by generate_book.py: every occurrence -- first and "
        "every subsequent ref alike -- of each lexicon identity across the "
        "sheet sequence, with a 5-word context window on each side. "
        "Regenerated on every run. Each entry's <def> text is copied from "
        "the Sheet 99 lexicon, not hand-authored here -- edit it there.",
    )
    conc_div = etree.SubElement(body, f"{{{TEI_NS}}}div")
    conc_div.set("type", "concordance")
    etree.SubElement(conc_div, f"{{{TEI_NS}}}head").text = "Concordance"
    cat_div = etree.SubElement(conc_div, f"{{{TEI_NS}}}div")
    cat_div.set("type", "category")
    etree.SubElement(cat_div, f"{{{TEI_NS}}}head").text = _LEXICON_CATEGORY_HEAD

    for n, xid in enumerate(_sorted_lexicon_xids(lexicon_entries), start=1):
        info = lexicon_entries[xid]
        entry = _build_entry_shell(cat_div, n, xid, info, existing_defs)
        for occ in concordance.get(xid, []):
            xr = etree.SubElement(entry, f"{{{TEI_NS}}}xr")
            xr.set("type", "occurrence")
            ref = etree.SubElement(xr, f"{{{TEI_NS}}}ref")
            ref.set("target", f"{occ['source_file']}.xml")
            ref.set("n", str(occ["line"]))
            # occ["before"]/occ["after"] already carry whatever character
            # (a space, or punctuation with none, e.g. a occurrence right
            # after an opening parenthesis) originally glued them to the
            # occurrence -- see _last_words_suffix/_first_words_prefix.
            ref.text = occ["before"] or None
            hi = etree.SubElement(ref, f"{{{TEI_NS}}}hi")
            hi.set("rend", "bold")
            hi.text = occ["occurrence_text"]
            hi.tail = occ["after"] or None

    _finalize_entries_file(tei, cat_div, out_path)


# ──────────────────────────────────────────────────────────────────────────────
# ROUND-TRIP MARKERS (XML <-> Typst)
# ──────────────────────────────────────────────────────────────────────────────
# Every leaf text unit emitted to Typst (a paragraph, a heading, a table
# cell, a quote line, a lexicon definition...) is wrapped in a matched pair
# of Typst /* */ comments encoding a sequential id: /*rtN*/...content.../*rt/N*/
# Comments are stripped by the Typst compiler, so markers never show up in
# the rendered PDF. `_collector` records, in the same order the ids are
# handed out, the XML node each unit came from -- so re-running the
# generator against an unchanged XML tree reproduces the exact same
# id -> node mapping, letting a later sync pass match hand-edited units
# back to their source elements without needing to persist ids into the
# XML file itself.

_collector = None
# The <p> of the document being rendered that gets the illuminated initial
# (see _choose_initial_paragraph); None when there is none.
_initial_target = None


def _mark(node, content):
    """Wrap Typst content in a hidden round-trip marker tied to `node`."""
    idx = len(_collector) + 1
    _collector.append(node)
    return f"/*rt{idx}*/{content}/*rt/{idx}*/"


_MARKER_RE = re.compile(r"/\*rt(\d+)\*/(.*?)/\*rt/\1\*/", re.DOTALL)


def _parse_markers(typst_text):
    """Return {id: raw_content} for every round-trip marker pair found."""
    return {int(m.group(1)): m.group(2) for m in _MARKER_RE.finditer(typst_text)}


def _find_matching_bracket(s, start):
    """start is the index of a '[' ; return the index of its matching ']',
    skipping backslash-escaped characters. Returns len(s) if unmatched."""
    depth = 0
    i, n = start, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _skip_args(s, start):
    """start is the index of a '(' ; return the index just past its
    matching ')' (handling nested parens, e.g. rgb("#...") inside a
    #text(...) call's arguments)."""
    depth = 0
    i, n = start, len(s)
    while i < n:
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


_FUNC_NAME_RE = re.compile(r"#(\w+)")


def _choose_initial_paragraph(body):
    """The <p> that takes the illuminated initial: in the first division of
    `body` whose type is in INITIAL_DIV_TYPES, the first paragraph before the
    first sub-division whose text begins with a letter. None if the feature
    is off or no paragraph qualifies (e.g. it opens with a name or term)."""
    if not INITIAL_FONT or body is None:
        return None
    for div in body.findall(f"{{{TEI_NS}}}div"):
        if div.get("type") not in INITIAL_DIV_TYPES:
            continue
        for child in div:
            if _local(child) == "div":
                break
            if _local(child) == "p":
                text = (child.text or "").lstrip()
                if text and text[0].isalpha():
                    return child
        return None
    return None
# An id-shaped Typst label, e.g. "<Some_Id>" -- same shape as _VALID_ID_RE,
# so a stray "<" in ordinary prose (not a label at all) is never mistaken
# for one.
_FOOTNOTE_REF_ARG_RE = re.compile(r'^\(\s*<([^\W\d][\w.\-]*)>\s*\)$')
_TRAILING_LABEL_RE = re.compile(r'\s*<([^\W\d][\w.\-]*)>')


def _build_inline_node(fname, args, children, name_term_queue):
    """Map one recognized Typst inline call back to a TEI element. Anything
    outside this bounded set (the exact constructs this script's forward
    pass can emit) falls back to a plain <span>, so an edit is never lost
    even if its specific semantic tag can't be recovered.

    "w"/"y" (the first-/subsequent-occurrence markers) are special: rather
    than minting a new element, the call is matched -- in document order --
    against `name_term_queue`, the unit's *original* <name>/<term>
    descendants collected before the edit was applied. The matched
    original's tag and every attribute (ref/xml:id/key/type/xml:lang/rend)
    carry over onto a fresh element holding the freshly edited content, so
    a hand-edit anywhere else in the same unit never strips a name or term
    back down to bare text. A marker with nothing left in the queue to
    match (more #w/#y calls in the edit than original name/term elements)
    falls back to a generic <span>, same as any other unrecognized
    construct."""
    if fname in ("w", "y"):
        if name_term_queue:
            orig = name_term_queue.pop(0)
            el = etree.Element(orig.tag)
            for k, v in orig.attrib.items():
                el.set(k, v)
        else:
            el = etree.Element(f"{{{TEI_NS}}}span")
        _attach_inline_children(el, children)
        return el
    if fname == "emph":
        el = etree.Element(f"{{{TEI_NS}}}hi")
        el.set("rend", "italic")
    elif fname == "strong":
        el = etree.Element(f"{{{TEI_NS}}}hi")
        el.set("rend", "bold")
    elif fname == "smallcaps":
        el = etree.Element(f"{{{TEI_NS}}}term")
    elif fname == "footnote":
        el = etree.Element(f"{{{TEI_NS}}}note")
        el.set("place", "foot")
    elif fname == "text" and "italic" in args:
        el = etree.Element(f"{{{TEI_NS}}}hi")
        el.set("rend", "italic")
    else:
        el = etree.Element(f"{{{TEI_NS}}}span")
    _attach_inline_children(el, children)
    return el


def _attach_inline_children(parent, children):
    """Assign a flat list mixing plain strings and lxml elements onto
    parent as text/tail, in order."""
    text_buf = []
    last_el = None
    for item in children:
        if isinstance(item, str):
            text_buf.append(item)
            continue
        joined = "".join(text_buf)
        if last_el is None:
            parent.text = (parent.text or "") + joined
        else:
            last_el.tail = (last_el.tail or "") + joined
        parent.append(item)
        last_el = item
        text_buf = []
    joined = "".join(text_buf)
    if last_el is None:
        parent.text = (parent.text or "") + joined
    else:
        last_el.tail = (last_el.tail or "") + joined


_NAME_TERM_STYLE_WRAP_RE = re.compile(r"#(?:smallcaps|emph)\[")


def _strip_name_term_style_wrapper(inner):
    """A <term>'s smallcaps/italic styling is re-derived on every future
    render from its own xml:lang/rend attributes, not chosen by hand -- so
    when a #w[...]/#y[...] marker's entire inner content is exactly one
    #smallcaps[...]/#emph[...] call (the forward pass's own term-styling
    wrapper, see _term_style), that wrapper is stripped down to its own
    inner text before the reused <name>/<term> element is rebuilt. Left
    in place, it would otherwise round-trip as a second, attribute-less
    <term>/<hi> nested inside the reused element. A style call that wraps
    only PART of the content (e.g. a hand-added #strong[...] on one word)
    is left completely alone -- only a wrapper spanning the *entire*
    trimmed content is ever stripped."""
    s = inner.strip()
    m = _NAME_TERM_STYLE_WRAP_RE.match(s)
    if not m:
        return inner
    open_idx = m.end() - 1
    close_idx = _find_matching_bracket(s, open_idx)
    if close_idx == len(s) - 1:
        return s[open_idx + 1:close_idx]
    return inner


def _typst_inline_to_nodes(s, name_term_queue=None):
    """Parse a bounded subset of Typst inline markup -- the constructs this
    script's forward pass itself emits (#emph/#strong/#smallcaps/#footnote/
    #text(...)/#w(...)/#y(...)/#super(...)/#fn_ref(...), "quoted" spans, and
    \\-escaped literals) -- back into a flat list mixing plain strings and
    lxml elements, in document order. Any construct outside this set (or
    malformed edit) degrades gracefully: unrecognized #calls become a
    generic <span>, stray '#'/'"' with no matching close are treated as
    literal characters. #super[...] is unwrapped back to its own bare
    content rather than kept as a <span> -- see the fname == "super" branch
    below. #fn_ref(<label>) becomes <ptr target="#label"/>; a label trailing
    a #footnote[...]'s closing bracket is re-attached as that rebuilt
    <note>'s xml:id -- see the fname == "fn_ref"/"footnote" branches.

    `name_term_queue` is threaded through every recursive call so a
    #w[...]/#y[...] marker anywhere in `s` -- however deeply nested -- is
    matched against the unit's original <name>/<term> descendants in the
    same document order (see _build_inline_node)."""
    if name_term_queue is None:
        name_term_queue = []
    result = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "#":
            m = _FUNC_NAME_RE.match(s, i)
            if m:
                j = m.end()
                fname = m.group(1)
                args = ""
                if j < n and s[j] == "(":
                    end = _skip_args(s, j)
                    args = s[j:end]
                    j = end
                if fname == "fn_ref" and (j >= n or s[j] != "["):
                    # #fn_ref(<label>) is a plain-text (non-superscript)
                    # reference to an already-defined footnote's number --
                    # see the ptr branch of _inline(). Unwrap it back to
                    # <ptr target="#label"/> instead of falling through to
                    # the generic-#call/literal-text fallback below.
                    ref_m = _FOOTNOTE_REF_ARG_RE.match(args)
                    if ref_m:
                        ptr = etree.Element(f"{{{TEI_NS}}}ptr")
                        ptr.set("target", f"#{ref_m.group(1)}")
                        result.append(ptr)
                        i = j
                        continue
                if j < n and s[j] == "[":
                    bracket_end = _find_matching_bracket(s, j)
                    inner = s[j + 1:bracket_end]
                    if fname in ("w", "y"):
                        inner = _strip_name_term_style_wrapper(inner)
                    children = _typst_inline_to_nodes(inner, name_term_queue)
                    i = bracket_end + 1
                    if fname in ("super", "initial"):
                        # #super[...] around an ordinal suffix is auto-derived
                        # by _tescape() from plain text, not a TEI element --
                        # unwrap it back to bare text instead of minting a
                        # <span>, so an untouched ordinal round-trips as
                        # plain text rather than accreting a spurious tag.
                        # #initial[X] (the illuminated initial) is likewise
                        # purely decorative, added by the renderer.
                        result.extend(children)
                        continue
                    if fname == "qcaps":
                        # An all-caps run inside a quotation, rendered in
                        # capitalized small caps by _qcaps_text(): back to
                        # the original uppercase plain text.
                        result.extend(c.upper() if isinstance(c, str) else c for c in children)
                        continue
                    node_el = _build_inline_node(fname, args, children, name_term_queue)
                    if fname == "footnote":
                        # A label immediately following the footnote's
                        # closing bracket (#footnote[...]<label>) is the
                        # note's own xml:id, re-attached to the freshly
                        # rebuilt <note> so a <ptr> elsewhere that targets it
                        # doesn't go dangling on the next sync.
                        label_m = _TRAILING_LABEL_RE.match(s, i)
                        if label_m:
                            node_el.set(_XML_ID, label_m.group(1))
                            i = label_m.end()
                    result.append(node_el)
                    continue
            result.append("#")
            i += 1
            continue
        if c == '"':
            j = s.find('"', i + 1)
            if j == -1:
                j = n
            inner = s[i + 1:j]
            q = etree.Element(f"{{{TEI_NS}}}q")
            _attach_inline_children(q, _typst_inline_to_nodes(inner, name_term_queue))
            result.append(q)
            i = j + 1
            continue
        if c == "\\" and i + 1 < n:
            result.append(s[i + 1])
            i += 2
            continue
        j = i
        while j < n and s[j] not in '#"\\':
            j += 1
        if j == i:
            j += 1
        result.append(s[i:j])
        i = j
    return result


def _apply_typst_unit(node, typst_content):
    """Replace node's content in place with the (edited) Typst unit content,
    converting the bounded set of inline constructs back to TEI elements.

    Every #w[...]/#y[...] occurrence marker in the edited content is
    matched, in document order, against node's *original* <name>/<term>
    descendants -- reusing each one's tag and attributes so that a
    hand-edit anywhere else in the unit never strips a name or term back
    down to bare text (see _build_inline_node)."""
    name_term_queue = [el for el in node.iter() if _local(el) in ("name", "term")]
    for child in list(node):
        node.remove(child)
    node.text = None
    _attach_inline_children(node, _typst_inline_to_nodes(typst_content.strip(), name_term_queue))


def sync_typst_to_xml(xml_path: Path, typ_path: Path, fmt_key=None, dry_run=False) -> bool:
    """Update xml_path from any hand-edited round-trip units in typ_path
    (a .typ of paper format `fmt_key`). With dry_run, change nothing and only
    report whether typ_path holds any edit.

    Only units whose on-disk marker content differs from a fresh XML->Typst
    regeneration are touched, so untouched text keeps its original TEI
    markup exactly (no gradual erosion of tags like <term>/<foreign> into
    generic <hi> just from re-running the sync). Returns True if the XML
    was changed."""
    fresh_text, collector, root = generate_typst_doc(xml_path, fmt_key)
    if not collector or root is None:
        return False
    working_tree = root.getroottree()
    fresh_units = _parse_markers(fresh_text)
    edited_units = _parse_markers(typ_path.read_text(encoding="utf-8"))

    # Collect (xpath, edited_content) rather than mutating `root` directly:
    # generate_typst_doc() ran _normalize_bibl_whitespace() on it, which
    # collapses pretty-printed whitespace inside <bibl>/<cit>/<note>
    # containers purely so _inline() reads clean text for Typst -- writing
    # that tree back out would flatten every such container's formatting,
    # not just the edited unit's.
    edits = []
    for idx, node in enumerate(collector, start=1):
        fresh = fresh_units.get(idx)
        edited = edited_units.get(idx)
        if fresh is None or edited is None or edited.strip() == fresh.strip():
            continue
        edits.append((working_tree.getpath(node), edited))

    if not edits:
        return False
    if dry_run:
        return True

    # Re-parse a pristine copy of the file so every untouched node keeps
    # its exact original formatting; structure/order match `working_tree`
    # exactly (whitespace normalization never adds/removes/reorders
    # elements), so each positional xpath resolves to the same node.
    clean_tree = _parse_tei_xml(xml_path)
    for path, edited in edits:
        matches = clean_tree.xpath(path)
        if matches:
            _apply_typst_unit(matches[0], edited)

    clean_tree.write(str(xml_path), encoding="utf-8", xml_declaration=False)
    return True


# ──────────────────────────────────────────────────────────────────────────────
# TYPST GENERATION
# ──────────────────────────────────────────────────────────────────────────────

_TYPST_ESC = re.compile(r'([#*_`$@\\~\[\]])')
_ORDINAL_RE = re.compile(r'(\d+)(st|nd|rd|th)\b')

def _superscript_ordinals(text):
    """Raise an ordinal abbreviation's suffix into superscript, e.g.
    "21st" -> "21#super[st]"."""
    return _ORDINAL_RE.sub(lambda m: f'{m.group(1)}#super[{m.group(2)}]', text)


def _tescape(text):
    """Escape Typst markup-mode special characters in plain text."""
    if not text:
        return ""
    # Superscripting runs after escaping -- it injects a literal "#", which
    # must not itself be escaped by the substitution above.
    return _superscript_ordinals(_TYPST_ESC.sub(r'\\\1', text))


def _typst_str(text):
    """Escape for use inside a Typst double-quoted string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


_TEI_FMT = f"{{{TEI_NS}}}"


def _occurrence_marker(el):
    """Return the preamble's marker function name ("w" or "y") for the tiny
    superscript square rendered immediately before a <name>/<term>: "w"
    (accent-blue) for its first (defining) occurrence, "y" (light gray) for
    every later ref-pointing occurrence of the same identity. None if el
    carries neither -- e.g. an as-yet-invalid <name> missing every
    identifying attribute, a case validation reports but never blocks
    rendering for -- or if it's a <name ref="..." type="blocker">: not a
    genuine occurrence of the identity it points to, so no marker at all."""
    if el.get("type") == "blocker":
        return None
    if el.get("ref"):
        return "y"
    if el.get(_XML_ID) or el.get("key"):
        return "w"
    return None


# All-caps text inside a quotation (<q>/<quote>) -- e.g. the inscription on
# the Whore's forehead in Rev. 17:5 -- renders as a run of capitalized
# small-cap words: "MYSTERY, BABYLON THE GREAT" -> #qcaps[Mystery, Babylon
# The Great]. Only a run of at least two consecutive all-caps words (at least
# two of them two letters or longer) qualifies, so a lone acronym quoted in
# passing ("US", "VIP", "FCNSW") is left alone. A run may span element
# boundaries (the <name> around "BABYLON" above); a footnote inside the
# quotation is not part of its flow and is never converted. #qcaps is its
# own preamble function (not #smallcaps, which syncs back as a <term>) so a
# hand-edited unit syncs back to the original uppercase text.
_QCAPS_SEGMENTS = {}
_QCAPS_DONE = set()


def _qcaps_outer_quotation(el):
    """The outermost <q>/<quote> among el and its ancestors, or None. A
    <note> boundary stops the search: a footnote's text isn't quoted text."""
    found = None
    while el is not None:
        name = _local(el)
        if name == "note":
            break
        if name in ("q", "quote"):
            found = el
        el = el.getparent()
    return found


def _qcaps_index(quote):
    """Map every text segment of `quote` -- keyed (element, "text"/"tail")
    -- to the (start, end) ranges within it covered by an all-caps run."""
    if quote in _QCAPS_DONE:
        return
    _QCAPS_DONE.add(quote)
    segs = []

    def walk(el):
        segs.append(((el, "text"), el.text or ""))
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if _local(child) != "note":
                walk(child)
            segs.append(((child, "tail"), child.tail or ""))

    walk(quote)
    flat = "".join(text for _, text in segs)
    runs, cur = [], []
    prev_end = 0
    for m in _WORD_TOKEN_RE.finditer(flat):
        tok = m.group()
        is_caps = tok.isupper()
        if is_caps and cur and re.search(r"[^\W\d_]", flat[prev_end:m.start()]) is None:
            cur.append(m)
        else:
            runs.append(cur)
            cur = [m] if is_caps else []
        prev_end = m.end()
    runs.append(cur)
    spans = [(r[0].start(), r[-1].end()) for r in runs
             if len(r) >= 2 and sum(len(x.group()) >= 2 for x in r) >= 2]
    if not spans:
        return
    offset = 0
    for key, text in segs:
        seg_end = offset + len(text)
        local = [(max(s, offset) - offset, min(e, seg_end) - offset)
                 for s, e in spans if s < seg_end and e > offset]
        if local:
            _QCAPS_SEGMENTS[key] = local
        offset = seg_end


def _qcaps_text(owner, kind, context, text):
    """_tescape(text), with every all-caps run inside a quotation (see
    _QCAPS_SEGMENTS) rendered as #qcaps[...] in capitalized words. `owner`
    and `kind` name the segment ((el, "text") or (child, "tail")); `context`
    is the element whose flow the text belongs to."""
    if not text:
        return ""
    quote = _qcaps_outer_quotation(context)
    if quote is None:
        return _tescape(text)
    _qcaps_index(quote)
    ranges = _QCAPS_SEGMENTS.get((owner, kind))
    if not ranges:
        return _tescape(text)
    out, pos = [], 0
    for s, e in ranges:
        out.append(_tescape(text[pos:s]))
        words = _WORD_TOKEN_RE.sub(lambda m: m.group()[0] + m.group()[1:].lower(), text[s:e])
        out.append(f"#qcaps[{_tescape(words)}]")
        pos = e
    out.append(_tescape(text[pos:]))
    return "".join(out)


def _inline(node):
    """Render element and its descendants as Typst inline content."""
    children = list(node)
    parts = [_qcaps_text(node, "text", node, node.text or "")]
    for idx, child in enumerate(children):
        t = _local(child)
        inner = _inline(child)
        rend = child.get("rend", "")
        rend_words = rend.split()
        rend_italic = "italic" in rend_words
        lang_italic = _is_roman_foreign(child) and not _in_cit(child)
        already_italic = False
        # True once `out` ends in visibly-italicized content -- used below to
        # decide whether a gap is needed before whatever follows immediately
        # (see _ITALIC_GAP). Deliberately excludes the note/measure branches:
        # their already_italic=True only suppresses the generic rend_italic
        # wrap, it doesn't mean `out` itself reads as italic.
        wraps_italic = False

        if t == "hi":
            is_bold = "bold" in rend_words
            is_italic = rend_italic or lang_italic
            out = inner
            if is_italic:
                out = f"#emph[{out}]"
                already_italic = True
                wraps_italic = True
            if is_bold:
                out = f"#strong[{out}]"
        elif t == "foreign":
            if lang_italic:
                out = f'#text(style: "italic", fill: rgb("#3a5f8a"))[{inner}]'
                already_italic = True
                wraps_italic = True
            else:
                out = inner
        elif t == "term":
            # _term_style() already factors in @rend="italic" (with its own
            # script-based exceptions), so it fully owns italic handling here.
            style = _term_style(child)
            if style == "smallcaps":
                out = f"#smallcaps[{inner}]"
            elif style == "italic":
                out = f"#emph[{inner}]"
                wraps_italic = True
            else:
                out = inner
            already_italic = True
        elif t == "title":
            if child.get("level") == "a":
                out = f'"{inner}"'
            else:
                out = f"#emph[{inner}]"
                already_italic = True
                wraps_italic = True
        elif t == "quote":
            q = inner
            if len(q) > 1 and q.startswith('"') and q.endswith('"'):
                q = q[1:-1]
            if lang_italic or rend_italic:
                out = f'#text(style: "italic")["{q}"]'
                already_italic = True
                wraps_italic = True
            else:
                out = f'"{q}"'
        elif t == "q":
            out = f'"{inner}"'
        elif t == "span":
            if child.get("type") == "reason":
                out = f'#text(fill: muted, size: 0.9em, style: "italic")[{inner}]'
                already_italic = True
                wraps_italic = True
            elif lang_italic:
                out = f"#emph[{inner}]"
                already_italic = True
                wraps_italic = True
            else:
                out = inner
        elif t == "lb":
            out = "#linebreak()"
        elif t == "l":
            # A verse line: a line break after it unless it is the last one.
            nxt = child.getnext()
            out = inner + ("#linebreak()" if nxt is not None and _local(nxt) == "l" else "")
        elif t == "note":
            if child.get("place") == "foot":
                note_id = child.get(_XML_ID)
                out = f"#footnote[{inner}]<{note_id}>" if note_id else f"#footnote[{inner}]"
            else:
                out = ""
            already_italic = True
        elif t == "ptr":
            # A same-document pointer to a footnote's xml:id (target="#id"):
            # renders as that footnote's plain (non-superscript) number via
            # the fn_ref() preamble helper -- unlike an actual footnote mark,
            # this sits at normal baseline with normal word-spacing on both
            # sides, so (unlike <note>) it's never squeezed flush against
            # neighboring text.
            target = child.get("target") or ""
            label = target[1:] if target.startswith("#") else ""
            out = f"#fn_ref(<{label}>)" if label else ""
        elif t == "measure":
            out = ""
            already_italic = True
        else:
            out = inner

        if rend_italic and not already_italic:
            out = f"#emph[{out}]"
            wraps_italic = True

        if t in ("name", "term"):
            marker = _occurrence_marker(child)
            if marker:
                out = f"#{marker}[{out}]"

        tail = child.tail or ""
        tail_out = _qcaps_text(child, "tail", node, tail)
        if wraps_italic:
            if tail:
                # A "?"/"!" landing flush against the closing italic slant
                # reads as kerned into it -- nudge it off with a hair of
                # space.
                if tail[0] in "?!":
                    tail_out = _ITALIC_GAP + tail_out
            else:
                # No tail text at all: if the very next sibling is a
                # footnote, its superscript digit would otherwise sit flush
                # against the italic close.
                next_child = children[idx + 1] if idx + 1 < len(children) else None
                if next_child is not None and _local(next_child) == "note" \
                        and next_child.get("place") == "foot":
                    tail_out = _ITALIC_GAP

        parts.append(out)
        parts.append(tail_out)
    return "".join(parts)


def _typst_table(node):
    """Render a TEI <table> to a Typst #table(...)."""
    rows = [c for c in node if _local(c) == "row"]
    if not rows:
        return ""
    ncols = max(
        (sum(1 for c in row if _local(c) == "cell") for row in rows),
        default=0,
    )
    if not ncols:
        return ""

    header_cells = []
    body_cells = []
    for row in rows:
        is_hdr = row.get("role") == "label"
        for cell in row:
            if _local(cell) != "cell":
                continue
            inner = _mark(cell, _inline(cell))
            if is_hdr:
                header_cells.append(
                    f'  table.cell(fill: rgb("#1e4d8c"))'
                    f'[#text(fill: white, weight: "bold")[{inner}]]'
                )
            else:
                body_cells.append(f"  [{inner}]")

    # Tables of more than 3 columns repeat their header row on every page
    # they span; those of LANDSCAPE_TABLE_MIN_COLUMNS or more (by default the
    # same ones) also get their own landscape page sequence so they have
    # room to breathe.
    wide = ncols > 3
    landscape = ncols >= LANDSCAPE_TABLE_MIN_COLUMNS

    if wide and header_cells:
        cell_lines = (
            "    table.header(\n"
            + ",\n".join("    " + s for s in header_cells)
            + "\n    ),\n"
            + ",\n".join("  " + s for s in body_cells)
        )
    else:
        cell_lines = ",\n".join("  " + s for s in header_cells + body_cells)

    table_call = (
        "#table(\n"
        f"  columns: {ncols},\n"
        '  align: left,\n'
        '  stroke: (paint: rgb("#bfd7f0"), thickness: 0.5pt),\n'
        + cell_lines
        + "\n)"
    )

    if landscape:
        return (
            "#page(flipped: true)[\n"
            '  #set text(size: 10pt)\n'
            '  #set par(justify: false)\n'
            '  #set table.cell(breakable: false)\n'
            f"  {table_call}\n"
            "]"
        )

    return (
        "#block[\n"
        '  #set text(size: 10pt)\n'
        '  #set par(justify: false)\n'
        '  #set table.cell(breakable: false)\n'
        f"  {table_call}\n"
        "]"
    )


def _typst_cit(node):
    """Render a <cit> block: the original <quote>(s), and the
    transliteration and translation <note>s, superposed as separate lines
    inside a 2em-indented block. Each line is its own paragraph with its
    own hanging indent, so every line starts flush at the block's left edge
    and only its own wrapped continuation (if it exceeds the page width) is
    indented. Every <quote> and the translation are italicized (the
    automatic language-based italicization rule explicitly excludes content
    inside <cit> blocks, so this is done by hand here); the transliteration
    is not. The closing <bibl> citation becomes a footnote attached to the
    original-language quote line (the first <quote>, not the translation).

    A <cit> may hold two <quote> tags instead of a <quote>/translation
    <note> pair -- one per language, in source order -- e.g. a French
    original followed by its English rendering. Each renders on its own
    line, and any <quote> whose xml:lang isn't English is preceded by
    "[Language]" in a small muted label, so a reader can tell which line is
    the original at a glance.

    When the <cit> holds only a single <quote> (no transliteration,
    translation, or second <quote>), it reads as a plain block quotation
    instead: no hanging indent. The block never breaks across a page,
    regardless of which case applies. Any trailing <p> (commentary on the
    quote) renders below everything else, in a smaller non-italicized font
    with no hanging indent of its own."""
    quote_data = []
    for quote_el in node.findall(f"{_TEI_FMT}quote"):
        quote_content = _inline(quote_el).strip()
        if len(quote_content) > 1 and quote_content.startswith('"') and quote_content.endswith('"'):
            quote_content = quote_content[1:-1]
        if not quote_content.strip():
            continue
        quote_content = _mark(quote_el, quote_content)
        quote_data.append((quote_content, quote_el.get(f"{{{XML_NS}}}lang")))
    if not quote_data:
        return None

    translit_el = node.find(f"{_TEI_FMT}note[@type='transliteration']")
    translit_content = _inline(translit_el).strip() if translit_el is not None else ""
    if translit_content:
        translit_content = _mark(translit_el, translit_content)

    translation_el = node.find(f"{_TEI_FMT}note[@type='translation']")
    translation_content = _inline(translation_el).strip() if translation_el is not None else ""
    if translation_content:
        translation_content = _mark(translation_el, translation_content)

    quote_only = len(quote_data) == 1 and not translit_content and not translation_content

    quote_count = len(quote_data)
    lines = []
    for quote_content, lang in quote_data:
        quote_content = f'#text(style: "italic")[{quote_content}]'
        if lang and lang != "en":
            lang_name = _tescape(_lang_display_name(lang))
            quote_content = f'#text(fill: muted, size: 0.85em)[[{lang_name}]] {quote_content}'
        lines.append(quote_content)
    if translit_content:
        lines.append(translit_content)
    if translation_content:
        lines.append(f'#text(style: "italic")[{translation_content}]')

    bibl_el = node.find(f"{_TEI_FMT}bibl")
    if bibl_el is not None:
        bibl_content = _inline(bibl_el)
        if bibl_content.strip():
            bibl_content = _mark(bibl_el, bibl_content)
            # lines[0] (the first/original-language quote) is always
            # italicized above, so the footnote marker would otherwise land
            # flush against the italic close -- same collision _ITALIC_GAP
            # fixes in _inline().
            lines[0] += f'{_ITALIC_GAP}#footnote[#text(style: "normal")[{bibl_content}]]'

    # Between two <quote> lines (original + translation-as-quote, see above),
    # bump the gap by 0.2em over the block's default 0.3em paragraph
    # spacing, so the pair doesn't read as tightly as same-language lines.
    pars = "\n  ".join(
        f'#par(spacing: 0.5em)[{line}]' if i < quote_count - 1 else f"#par[{line}]"
        for i, line in enumerate(lines)
    )
    hanging_indent = "0em" if quote_only else "1em"

    note_pars = []
    for p_el in node.findall(f"{_TEI_FMT}p"):
        content = _inline(p_el).strip()
        if content:
            note_pars.append(_mark(p_el, content))
    note_block = ""
    if note_pars:
        note_content = "\n\n  ".join(note_pars)
        note_block = (
            "\n  #block(above: 0.6em, below: 0em)[\n"
            '    #set text(size: 0.85em, style: "normal")\n'
            '    #set par(hanging-indent: 0em)\n'
            f"    {note_content}\n"
            "  ]"
        )

    return (
        "#block(inset: (left: 2em), above: 1.5em, below: 1.5em, breakable: false)[\n"
        f'  #set par(hanging-indent: {hanging_indent}, spacing: 0.3em, leading: 0.5em)\n'
        f"  {pars}\n"
        f"{note_block}\n"
        "]"
    )


_ITEM_BLOCK_TAGS = {"head", "p", "list", "table", "figure"}


def _typst_list_item(item, heading_level):
    """Render an <item> that has block-level children (head/p/...) to a
    Typst list-item body: a <head> becomes a bold lead-in line, each <p>
    becomes its own paragraph, and everything after the first line is
    indented so Typst's markup-mode list parser keeps it inside the same
    item instead of leaking the source XML's raw whitespace/newlines into
    the item text (which read as stray indentation in the .typ output)."""
    parts = []
    for child in item:
        ct = _local(child)
        if ct == "head":
            content = _inline(child)
            if content.strip():
                parts.append(f"*{_mark(child, content)}*")
        elif ct == "p":
            content = _inline(child)
            if content.strip():
                parts.append(_mark(child, content))
        else:
            chunk = _block_el(child, heading_level)
            if chunk:
                parts.append(chunk)
    if not parts:
        return None

    first, *rest = parts
    if not rest:
        return first
    indented_rest = "\n\n".join("  " + p.replace("\n", "\n  ") for p in rest)
    return f"{first}\n\n{indented_rest}"


# ──────────────────────────────────────────────
# FIGURES AND IMAGES
# ──────────────────────────────────────────────
# A <figure> holds one or two <graphic url="..."/> images, a <head> (the
# caption) and optionally descriptive <p>/<list> text. Images live in the
# images/ folder next to the script; a <graphic> may point at a web URL
# (downloaded once, then found again through the registry), or at a local
# file. This whole section is a port of the figure handling of the legacy
# tei_to_typst.py, with the same registry file and the same sizing rules.

IMAGES_DIR = DIR / "images"
_IMAGE_REGISTRY_FILE = ".download_registry.json"
# Wikimedia asks every client to identify itself with a contact address.
# Add one here (e.g. "...; contact: you@example.org") before downloading much.
_IMG_HEADERS = {"User-Agent": "generate_book/1.0 (educational use)"}

# Common English stop-words stripped when building Commons search queries
_IMG_STOP = frozenset(
    "a an the of in on at to for and or with from by its this that these those "
    "demonstrating showing depicting illustrating view detail plan image photo "
    "photograph digital reconstruction c ca circa bce bc ce ad ".split()
)
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".svg")
_IMG_SKIP_EXTS = (".pdf", ".ogg", ".webm", ".ogv")

# Per-run memo: url -> local file name (or None after a failure), so one image
# is looked up (and, if it fails, retried) at most once per run.
_image_memo = {}
_image_registry_cache = None


def _image_log(msg):
    print(f"  {_tag('IMAGE')}{msg}")


def _wikimedia_filename_from_thumb(url):
    """The bare file name inside a Wikimedia /thumb/ URL, or None."""
    m = re.search(r"/thumb/[a-f0-9]/[a-f0-9]{2,4}/(.+?)/\d+px-.+$", url)
    return urllib.parse.unquote(m.group(1)) if m else None


def _wikimedia_filename_from_special_filepath(url):
    """The bare file name inside a Special:FilePath URL, or None."""
    m = re.search(r"/Special:FilePath/(.+?)(?:\?|$)", url, re.IGNORECASE)
    return urllib.parse.unquote(m.group(1)) if m else None


def _safe_local_name(url):
    """A safe local file name derived from a URL."""
    raw = urllib.parse.unquote(urllib.parse.urlparse(url).path.split("/")[-1])
    raw = re.sub(r"^\d+px-", "", raw)         # strip "800px-"
    if raw.endswith(".svg.png"):               # an SVG thumbnail: keep the .svg
        raw = raw[:-4]
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)


def _fetch_commons_api(params, delay=0.0):
    """Call the Commons MediaWiki API, backing off exponentially on HTTP 429."""
    if delay:
        time.sleep(delay)
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=_IMG_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _image_log(f"Commons rate-limited, waiting {2 ** attempt}s")
                time.sleep(2 ** attempt)
            else:
                _image_log(f"HTTP {e.code} from the Commons API")
                return None
        except Exception as exc:
            _image_log(f"Commons API error: {exc}")
            return None
    return None


def _commons_direct_url(filename):
    """The direct-download URL of a Commons file (exact name lookup)."""
    data = _fetch_commons_api(
        f"action=query&titles=File:{urllib.parse.quote(filename, safe='')}"
        "&prop=imageinfo&iiprop=url&format=json",
        delay=1.0,
    )
    if not data:
        return None
    for pid, page in data.get("query", {}).get("pages", {}).items():
        if pid != "-1":
            for info in page.get("imageinfo", []):
                if info.get("url"):
                    return info["url"]
    return None


def _commons_first_hit(query, limit, delay):
    """Search Commons files for `query`; return the direct URL of the first
    suitable bitmap/SVG result, or None."""
    data = _fetch_commons_api(
        f"action=query&list=search&srnamespace=6"
        f"&srsearch={urllib.parse.quote(query)}&srlimit={limit}&format=json",
        delay=delay,
    )
    if not data:
        return None
    for res in data.get("query", {}).get("search", []):
        title = res.get("title", "")
        low = title.lower()
        if low.endswith(_IMG_SKIP_EXTS) or not low.endswith(_IMG_EXTS):
            continue
        direct = _commons_direct_url(title.replace("File:", ""))
        if direct:
            return direct
        time.sleep(0.5)
    return None


def _commons_search_by_stem(stem):
    """Search Commons by the URL's file-name stem -- finds the right file when
    the XML carries an outdated or shortened name."""
    query = re.sub(r"\.[^.]+$", "", stem).replace("_", " ").replace("-", " ")
    _image_log(f"searching Commons by name: {query!r}")
    return _commons_first_hit(query, 5, 1.5)


def _commons_search_url(caption):
    """Search Commons for an image relevant to `caption`. Proper nouns
    (capitalized words) are tried first: they match file titles far more
    reliably than descriptive words."""
    proper = [w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", caption) if w.lower() not in _IMG_STOP]
    if proper:
        query = " ".join(proper[:4])
    else:
        words = re.findall(r"[A-Za-z]+", caption.lower())
        keywords = [w for w in words if w not in _IMG_STOP and len(w) > 3]
        if not keywords:
            return None
        query = " ".join(keywords[:4])
    _image_log(f"searching Commons for: {query!r}")
    return _commons_first_hit(query, 8, 3.0)


def _image_registry():
    """The URL -> local file name registry (shared with the legacy pipeline)."""
    global _image_registry_cache
    if _image_registry_cache is None:
        try:
            with open(IMAGES_DIR / _IMAGE_REGISTRY_FILE, encoding="utf-8") as f:
                _image_registry_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _image_registry_cache = {}
    return _image_registry_cache


def _image_registry_set(url, local_name):
    """Record (or, with None, forget) a URL -> file name mapping on disk."""
    reg = _image_registry()
    if local_name is None:
        if url not in reg:
            return
        del reg[url]
    else:
        if reg.get(url) == local_name:
            return
        reg[url] = local_name
    try:
        IMAGES_DIR.mkdir(exist_ok=True)
        with open(IMAGES_DIR / _IMAGE_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=2, sort_keys=True)
    except Exception as exc:
        _image_log(f"could not save the image registry: {exc}")


def _is_web_url(url):
    return url.startswith(("http://", "https://"))


def _cached_image_name(url):
    """The local file name of `url`'s image if it is already on disk -- via
    the registry, or the name derivable from the URL -- else None. Never
    touches the network."""
    if not _is_web_url(url):
        src = Path(url) if os.path.isabs(url) else DIR / url
        return src.name if src.exists() else None
    cached = _image_registry().get(url)
    if cached and (IMAGES_DIR / cached).exists():
        return cached
    safe = _safe_local_name(url)
    if safe and (IMAGES_DIR / safe).exists():
        return safe
    return None


def _handle_local_image(url):
    """A <graphic url> that is a local path: resolved against the script's
    directory, copied into images/ if it is not already there (and
    autocropped), and returned as a bare file name -- or None if missing."""
    src = Path(os.path.normpath(url if os.path.isabs(url) else DIR / url))
    if not src.exists():
        _image_log(f"local image not found: {src}  (FAILED)")
        return None
    dst = IMAGES_DIR / src.name
    if src.resolve() != dst.resolve():
        IMAGES_DIR.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        _image_log(f"copied {src.name}")
        _maybe_autocrop(dst)
    return src.name


def _download_to(url, dest, label):
    """Download `url` into `dest`; True on success."""
    try:
        req = urllib.request.Request(url, headers=_IMG_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        IMAGES_DIR.mkdir(exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    except Exception as exc:
        _image_log(f"download failed ({dest.name}): {exc}")
        return False
    _image_log(f"{label}: {dest.name} ({len(data) // 1024} KB)")
    _maybe_autocrop(dest)
    return True


def _resolve_image(url, caption=""):
    """The local file name (inside images/) for a <graphic url>, downloading
    it when needed; None if no image could be found. Resolution order:
      1. a local path (no network);
      2. the registry, or the name derivable from the URL, already on disk;
      3. the canonical URL from the Commons API, if that file is on disk;
      4. a direct download (the URL itself, then the canonical URL);
      5. a Commons search by the URL's file-name stem;
      6. a Commons search by the figure's caption -- a *substitute* image,
         so it is always logged."""
    if url in _image_memo:
        return _image_memo[url]
    result = _resolve_image_uncached(url, caption)
    _image_memo[url] = result
    return result


def _resolve_image_uncached(url, caption):
    if not _is_web_url(url):
        return _handle_local_image(url)
    reg = _image_registry()
    cached = reg.get(url)
    if cached and (IMAGES_DIR / cached).exists():
        return cached
    if cached:
        _image_registry_set(url, None)          # stale entry
    safe = _safe_local_name(url)
    if safe and (IMAGES_DIR / safe).exists():
        _image_registry_set(url, safe)
        return safe

    candidates = [url]
    dest = IMAGES_DIR / safe
    wiki_name = _wikimedia_filename_from_thumb(url) or _wikimedia_filename_from_special_filepath(url)
    if wiki_name:
        api_url = _commons_direct_url(wiki_name)
        if api_url and api_url != url:
            candidates.append(api_url)
            alt = _safe_local_name(api_url)
            if (IMAGES_DIR / alt).exists():
                _image_registry_set(url, alt)
                return alt
            safe, dest = alt, IMAGES_DIR / alt
    for i, candidate in enumerate(candidates):
        if i:
            time.sleep(1)
        if _download_to(candidate, dest, "downloaded"):
            _image_registry_set(url, safe)
            return safe
    if wiki_name:
        stem_url = _commons_search_by_stem(wiki_name)
        if stem_url:
            alt = _safe_local_name(stem_url)
            if (IMAGES_DIR / alt).exists() or _download_to(stem_url, IMAGES_DIR / alt, "found by name"):
                _image_registry_set(url, alt)
                return alt
    if caption:
        search_url = _commons_search_url(caption)
        if search_url:
            alt = _safe_local_name(search_url)
            if (IMAGES_DIR / alt).exists() or _download_to(search_url, IMAGES_DIR / alt, "SUBSTITUTE image"):
                _image_registry_set(url, alt)
                return alt
    _image_log(f"no image found for {url}  (FAILED)")
    return None


def _maybe_autocrop(path, padding=18):
    """Crop uniform-background margins from an image file in place. Acts only
    when Pillow is available, the four corners share a uniform colour
    (std-dev < 28 per channel) and cropping would save at least 5% of the
    area. Works for light (museum matte) and dark (studio) backgrounds."""
    if not _PIL_AVAILABLE:
        return
    try:
        img = Image.open(path).convert("RGB")
        W, H = img.size
        s = min(30, W // 6, H // 6)
        corners = [img.crop((0, 0, s, s)), img.crop((W - s, 0, W, s)),
                   img.crop((0, H - s, s, H)), img.crop((W - s, H - s, W, H))]
        px = [p for c in corners for p in c.getdata()]
        n = len(px)
        bg = [sum(p[i] for p in px) // n for i in range(3)]
        if max((sum((p[i] - bg[i]) ** 2 for p in px) / n) ** 0.5 for i in range(3)) > 28:
            return                              # not a uniform background
        bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        tol = 35 if bg_lum < 120 else 25        # wider for dark backgrounds

        def dist(p):
            return 0.299 * abs(p[0] - bg[0]) + 0.587 * abs(p[1] - bg[1]) + 0.114 * abs(p[2] - bg[2])

        mask = Image.new("L", (W, H))
        mask.putdata([255 if dist(p) > tol else 0 for p in img.getdata()])
        bbox = mask.getbbox()
        if bbox is None:
            return
        l, t, r, b = bbox
        l, t, r, b = max(0, l - padding), max(0, t - padding), min(W, r + padding), min(H, b + padding)
        saving = 1.0 - (r - l) * (b - t) / (W * H)
        if saving >= 0.05:
            img.crop((l, t, r, b)).save(path, quality=92)
            _image_log(f"autocropped {Path(path).name} ({W}x{H} -> {r - l}x{b - t}, saved {saving:.0%})")
    except Exception as exc:
        _image_log(f"autocrop skipped for {Path(path).name}: {exc}")


# The text area a figure is sized against is that of the format being
# rendered (see _text_area_mm): A4 is 155mm x 237mm, US Trade 114.4mm x 183.6mm.
_GUTTER_MM = 3.88          # 1em at 11pt
_IMG_HEIGHT_CAP = 0.33     # a figure never exceeds 33% of the text height


def _image_size(path):
    """(width, height) in pixels, or None (an SVG, or unreadable)."""
    if not _PIL_AVAILABLE:
        return None
    try:
        w, h = Image.open(path).size
        return (w, h) if w and h else None
    except Exception:
        return None


def _image_width_pct(path, max_pct=90):
    """A Typst width percentage that keeps the image's height within 33% of the
    text height: if the figure does not fit in what is left of the page, the
    gap left behind is then at most a third of the page. Without a readable
    size (Pillow missing, or an SVG) it is simply max_pct."""
    size = _image_size(path)
    if size is None:
        return max_pct
    ar = size[0] / size[1]
    col_w, text_h = _text_area_mm()
    limited = (_IMG_HEIGHT_CAP * text_h * ar / col_w) * 100
    return int(f"{max(15, min(max_pct, limited)):.0f}")


def _side_by_side_height_mm(path1, path2):
    """The common display height (mm) for a side-by-side pair, or None if the
    pair should be stacked. Each image, at the height cap, must be at most half
    the text column wide; the combined width is then checked against the
    column and the height scaled down if it overflows."""
    s1, s2 = _image_size(path1), _image_size(path2)
    if s1 is None or s2 is None:
        return None
    ar1, ar2 = s1[0] / s1[1], s2[0] / s2[1]
    col_w, text_h = _text_area_mm()
    cap = _IMG_HEIGHT_CAP * text_h
    half = 0.50 * col_w
    if cap * ar1 > half or cap * ar2 > half:
        return None
    common = cap
    if common * ar1 + _GUTTER_MM + common * ar2 > col_w:
        common *= (col_w - _GUTTER_MM) / (common * (ar1 + ar2))
    return common


_IMG_PLACEHOLDER = """\
rect(
    width: 90%,
    height: 6cm,
    stroke: 0.5pt + luma(160),
    fill: luma(245),
    inset: 1em,
    radius: 2pt,
  )[
    #align(center + horizon)[
      #text(size: 9.5pt, style: "italic", fill: luma(120))[Image not available]
    ]
  ]"""


def _typst_image(name, extra):
    return f'image("images/{_typst_str(name)}", {extra})'


def _figure_image_paths(xml_path):
    """The image files a document's figures already have on disk, so that a
    changed or replaced image makes the sheet stale. Never uses the network."""
    try:
        root = _parse_tei_xml(xml_path).getroot()
    except Exception:
        return []
    paths = []
    for g in root.iter(f"{_TEI_FMT}graphic"):
        name = _cached_image_name(g.get("url") or "")
        if name and (IMAGES_DIR / name).exists():
            paths.append(IMAGES_DIR / name)
    return paths


def _typst_figure(node):
    """Render a <figure>: its <graphic> image(s) with the <head> as caption
    and any <p>/<list> children as a descriptive block underneath.

    Two images go side by side when both are narrow enough, else are stacked;
    a missing image is drawn as a labelled placeholder box. The caption and
    each descriptive paragraph are round-trip units. A figure with no
    <graphic> is drawn as a bare caption from its <figDesc>."""
    graphics = node.findall(f"{_TEI_FMT}graphic")
    head_el = node.find(f"{_TEI_FMT}head")
    desc_el = node.find(f"{_TEI_FMT}figDesc")

    if not graphics:
        desc = _tescape((desc_el.text or "").strip()) if desc_el is not None else ""
        return f"#figure(caption: [{desc}])"

    # Caption: <head>, else <figDesc>
    caption = ""
    hint = ""
    if head_el is not None:
        content = _inline(head_el).strip()
        caption = _mark(head_el, content) if content else ""
        hint = "".join(head_el.itertext())
    elif desc_el is not None:
        hint = " ".join("".join(desc_el.itertext()).split())
        caption = _tescape(hint)

    # Descriptive block: <p> and <list> children
    desc_parts = []
    for child in node:
        t = _local(child)
        if t == "p":
            content = _inline(child).strip()
            if content:
                if not hint:
                    hint = "".join(child.itertext())
                desc_parts.append(_mark(child, content))
        elif t == "list":
            rendered = _block_el(child)
            if rendered:
                desc_parts.append(rendered)

    # Resolve every image; (file name or None, width %)
    entries = []
    for g in graphics:
        url = g.get("url") or ""
        if not url:
            continue
        name = _resolve_image(url, hint)
        entries.append((name, _image_width_pct(IMAGES_DIR / name) if name else 90))
    if not entries:
        return f"#figure(caption: [{caption}])"

    if len(entries) == 2 and all(n for n, _ in entries):
        common_h = _side_by_side_height_mm(IMAGES_DIR / entries[0][0], IMAGES_DIR / entries[1][0])
        if common_h is not None:
            h = f"{common_h:.1f}mm"
            img_body = (
                "grid(\n    columns: (auto, auto),\n    gutter: 1em,\n"
                f'    {_typst_image(entries[0][0], "height: " + h)},\n'
                f'    {_typst_image(entries[1][0], "height: " + h)},\n  )'
            )
        else:
            shared = min(p for _, p in entries)
            items = ",\n    v(0.6em),\n    ".join(_typst_image(n, f"width: {shared}%") for n, _ in entries)
            img_body = f"stack(\n    {items}\n  )"
    else:
        # Stacked images share the narrowest width, so none exceeds the cap.
        shared = min(p for _, p in entries)
        parts = [_typst_image(n, f"width: {shared}%") if n else _IMG_PLACEHOLDER for n, _ in entries]
        img_body = parts[0] if len(parts) == 1 else "stack(\n    " + ",\n    v(0.6em),\n    ".join(parts) + "\n  )"

    # A bare figure floats to the top of a page; one with descriptive text must
    # stay glued to it, so it does not float.
    placement = "" if desc_parts else "  placement: auto,\n"
    out = f"\n#figure(\n  {img_body},\n  caption: [{caption}],\n{placement})\n"
    if desc_parts:
        # Outside #figure() so the caption stays right under the image and the
        # text can break across a page instead of overflowing an unbreakable block.
        out += (
            "#block(width: 100%, inset: (x: 1em), above: 0.6em, below: 1.2em)[\n"
            "  #set text(size: 9.5pt)\n"
            "  #set par(first-line-indent: 0pt, justify: true, leading: 0.6em)\n"
            "  #set align(left)\n"
            "  " + "\n\n".join(desc_parts) + "\n]\n"
        )
    return out


def _block_el(node, heading_level=3):
    """Render a single block-level TEI element to Typst. Returns None to skip."""
    t = _local(node)

    if t == "p":
        content = _inline(node)
        if node is _initial_target:
            content = content.lstrip()
            if content and content[0].isalpha():
                content = f"#initial[{content[0]}]{content[1:]}"
        return _mark(node, content) if content.strip() else None

    if t == "head" or t == "speaker":
        prefix = "=" * heading_level
        content = _inline(node)
        if not content.strip():
            return None
        # A subsection heading carries a <subsection-page> marker inside its
        # body (so it moves with the heading to its page), read by `typst
        # query` when the table of contents lists subsections (--sub). It
        # sits outside the round-trip marker, so the sync never sees it.
        parent = node.getparent()
        page_marker = (
            "#context [#metadata(counter(page).get().first()) <subsection-page>]"
            if t == "head" and parent is not None and _local(parent) == "div"
            and parent.get("type") == "subsection" else ""
        )
        return f"{prefix} {page_marker}{_mark(node, content)}"

    if t == "sp":
        return _blocks(node, heading_level) or None

    if t == "list":
        list_type = node.get("type", "")
        bullet = "+" if list_type == "ordered" else "-"
        lines = []
        for item in node:
            if _local(item) != "item":
                continue
            if any(_local(c) in _ITEM_BLOCK_TAGS for c in item):
                body = _typst_list_item(item, heading_level)
                if body:
                    lines.append(f"{bullet} {body}")
            else:
                content = _inline(item)
                if content.strip():
                    lines.append(f"{bullet} {_mark(item, content)}")
        # Close the list with an empty `#[]` at column 0. Typst measures a
        # line's indentation after any leading comment, so a following block
        # that opens with its /*rtN*/ marker would otherwise count as indented
        # and be swallowed into the last item. `#[]` renders nothing and sits
        # outside every marker, so the .typ -> XML sync never sees it.
        return "\n\n".join(lines) + "\n\n#[]" if lines else None

    if t == "table":
        return _typst_table(node) or None

    if t == "listBibl":
        return _typst_listbibl(node)

    if t == "figure":
        return _typst_figure(node)

    if t == "cit":
        return _typst_cit(node)

    if t == "div":
        dtype = node.get("type")
        if dtype == "metadata":
            return None
        if dtype == "objection":
            return _typst_objection(node, heading_level) or None
        if dtype == "lexicon":
            return _typst_lexicon(node) or None
        if dtype == "bibliography" and node.find(f"{_TEI_FMT}listBibl") is not None \
                and _local(node.getparent()) == "body":
            return _typst_book_bibliography(node) or None
        if dtype == "concordance":
            return _typst_concordance(node) or None
        if dtype == "section":
            # A section inside a chapter/sheet wrapper (the report format
            # renders its sections itself, in _typst_section): its own head
            # is a level-2 heading -- the one that carries the table of
            # contents' page marker -- and its subsections' stay level 3.
            return _blocks(node, heading_level, head_level=2) or None
        return _blocks(node, heading_level) or None

    if t in ("note", "measure", "teiHeader"):
        return None

    content = _inline(node)
    return content if content.strip() else None


def _blocks(node, heading_level=3, head_level=None):
    """Render the block-level children of a TEI element to Typst. With
    head_level, the element's own <head> children are headings of that
    level instead of heading_level."""
    parts = []
    if node.text and node.text.strip():
        parts.append(_tescape(node.text.strip()))
    for child in node:
        level = head_level if head_level and _local(child) == "head" else heading_level
        chunk = _block_el(child, level)
        if chunk:
            parts.append(chunk)
        if child.tail and child.tail.strip():
            parts.append(_tescape(child.tail.strip()))
    return "\n\n".join(p for p in parts if p and p.strip())


def _typst_listbibl(listbibl_node):
    """Render a <listBibl> to a Typst hanging-indent entry block (or None if empty)."""
    entries = listbibl_node.findall(f"{_TEI_FMT}bibl")
    entry_strs = []
    for b in entries:
        c = _inline(b).strip()
        if c:
            entry_strs.append(_mark(b, c))
    if not entry_strs:
        return None
    return (
        "#block[\n"
        '  #set par(hanging-indent: 1.5em, justify: false, leading: 0.65em)\n'
        '  #set text(size: 10pt)\n'
        + "\n\n".join(entry_strs)
        + "\n]"
    )


def _typst_bibliography(div_node):
    """Render a <back><div type='bibliography'> to Typst (hanging-indent entries)."""
    head_el = div_node.find(f"{_TEI_FMT}head")
    parts = []
    if head_el is not None:
        parts.append(f"= {_mark(head_el, _inline(head_el))}")

    listbibl = div_node.find(f"{_TEI_FMT}listBibl")
    if listbibl is not None:
        block = _typst_listbibl(listbibl)
        if block:
            parts.append(block)

    return "\n\n".join(parts)


def _typst_abstract(div_node):
    """Render a <front><div type='abstract'> to Typst: italicized, as per
    usual academic practice."""
    head_el = div_node.find(f"{_TEI_FMT}head")
    if head_el is not None:
        heading_text = _mark(head_el, _inline(head_el))
    else:
        heading_text = "Abstract"

    body_parts = []
    for child in div_node:
        if _local(child) == "head":
            continue
        chunk = _block_el(child, heading_level=2)
        if chunk:
            body_parts.append(chunk)
    body = "\n\n".join(p for p in body_parts if p.strip())
    if not body.strip():
        return ""

    return (
        '#v(1.6em, weak: true)\n'
        f'#align(center)[#text(size: 16pt, weight: "bold", fill: accent)[{heading_text}]]\n'
        '#v(0.9em, weak: true)\n\n'
        "#block(inset: (left: 2em, right: 2em))[\n"
        '  #set text(style: "italic")\n'
        f"  {body}\n"
        "]"
    )


def _typst_section(section_node):
    """Render a <div type='section'> (research report format) to Typst."""
    parts = []
    head_el = section_node.find(f"{_TEI_FMT}head")
    if head_el is not None:
        parts.append(f"= {_mark(head_el, _inline(head_el))}")

    for child in section_node:
        t = _local(child)
        if t == "head":
            continue
        if t == "div" and child.get("type") == "subsection":
            sub_parts = []
            sub_head = child.find(f"{_TEI_FMT}head")
            if sub_head is not None:
                sub_parts.append(f"== {_mark(sub_head, _inline(sub_head))}")
            # Render subsection body, skipping its own <head>
            sub_body_parts = []
            for grandchild in child:
                if _local(grandchild) == "head":
                    continue
                chunk = _block_el(grandchild, heading_level=3)
                if chunk:
                    sub_body_parts.append(chunk)
                if grandchild.tail and grandchild.tail.strip():
                    sub_body_parts.append(_tescape(grandchild.tail.strip()))
            sub_body = "\n\n".join(p for p in sub_body_parts if p and p.strip())
            if sub_body.strip():
                sub_parts.append(sub_body)
            parts.append("\n\n".join(p for p in sub_parts if p.strip()))
        elif t == "div" and child.get("type") == "metadata":
            continue
        else:
            chunk = _block_el(child, heading_level=3)
            if chunk:
                parts.append(chunk)
        if child.tail and child.tail.strip():
            parts.append(_tescape(child.tail.strip()))

    return "\n\n".join(p for p in parts if p and p.strip())


def _typst_objection(objection_node, heading_level=3):
    """Render a <div type='objection'> (alias of the cycle format) to Typst.

    Only the first <sp> (the question side) is italicized; all others
    (the answer side and beyond) render plain. <speaker> renders as a
    level-3 heading via _block_el's "speaker" case; the objection's own
    <head> is a level-4 heading (styled by the "heading.where(level: 4)"
    show rule), one level below <speaker>, so it reads as a subordinate
    marker rather than competing with it. Spacing between objections comes
    entirely from that heading's own weak #v() rules, not a hardcoded gap.
    """
    parts = []
    head_el = objection_node.find(f"{_TEI_FMT}head")
    if head_el is not None:
        parts.append(f"==== {_mark(head_el, _inline(head_el))}")

    for i, sp in enumerate(objection_node.findall(f"{_TEI_FMT}sp")):
        sp_content = _blocks(sp, heading_level)
        if not sp_content.strip():
            continue
        if i == 0:
            parts.append(
                '#[\n'
                '  #set text(style: "italic")\n'
                f'  {sp_content}\n'
                ']'
            )
        else:
            parts.append(sp_content)

    return "\n\n".join(p for p in parts if p and p.strip())


def _typst_entry_headword(entry):
    """Render a lexicon/concordance <entry>'s headword: bold (italicized
    too when its xml:lang marks it non-English, matching the corpus-wide
    convention of italicizing roman-script foreign terms), followed by
    its category label in small caps. None if the entry has no/an empty
    <orth>."""
    orth_el = entry.find(f"{_TEI_FMT}form[@type='lemma']/{_TEI_FMT}orth")
    if orth_el is None:
        return None
    orth = _tescape(" ".join((orth_el.text or "").split()))
    if not orth:
        return None
    marked_orth = _mark(orth_el, orth)
    lang = entry.get(f"{{{XML_NS}}}lang")
    if lang and lang != "en":
        lang_name = _tescape(_lang_display_name(lang))
        # #strong[]/#emph[] (function-call form), not literal */_ markup --
        # a literal "*" landing right against a round-trip marker's leading
        # "/*" reads to Typst as an (unmatched) block-comment close.
        term = f'#strong[#emph[{marked_orth}]] #text(fill: muted, size: 0.85em)[[{lang_name}]]'
    else:
        term = f"#strong[{marked_orth}]"

    category_label = _lexicon_category_label(entry.get("type"))
    if category_label:
        term += f' #text(fill: muted, size: 0.85em)[#smallcaps[{_tescape(category_label)}]]'
    return term


def _typst_entry_defs(entry):
    """Render a lexicon/concordance <entry>'s <sense>/<def> list to Typst
    inline content, one rendered string per sense. Uses _inline() (not a
    bare .text grab) so inline markup inside a <def> -- e.g. <hi
    rend="italic"> -- renders instead of being silently dropped."""
    defs = []
    for sense in entry.findall(f"{_TEI_FMT}sense"):
        def_el = sense.find(f"{_TEI_FMT}def")
        if def_el is not None:
            text = _inline(def_el).strip()
            if text:
                defs.append(_mark(def_el, text))
    return defs


def _typst_entry_def_block(defs):
    """A sense list's Typst rendering, shared by the lexicon and
    concordance entry renderers: " --- def" for a single sense, or an
    indented #enum(...) (a numbered list) for more than one."""
    if not defs:
        return ""
    if len(defs) > 1:
        items = ",\n    ".join(f"[{d}]" for d in defs)
        return f"\n  #enum(\n    {items},\n  )"
    return f" --- {defs[0]}"


def _typst_lexicon_entry(entry):
    """Render a lexicon <entry> (form/orth + sense/def) to Typst: its
    headword, its definition(s), and a "First defined in" line."""
    term = _typst_entry_headword(entry)
    if term is None:
        return None
    defs = _typst_entry_defs(entry)
    if not defs:
        return None

    xr_el = entry.find(f"{_TEI_FMT}xr/{_TEI_FMT}ref")
    xr_line = ""
    if xr_el is not None:
        title = _tescape(" ".join((xr_el.text or "").split()))
        if title:
            xr_line = (
                f'\n  #text(fill: muted, size: 0.85em)'
                f'[First defined in: #text(style: "italic")[{_mark(xr_el, title)}]]'
            )

    return f"{term}{_typst_entry_def_block(defs)}{xr_line}"


_SHEET_NUM_RE = re.compile(r"(?:Sheet|Chapter)\s+(\d+(?:-\d+)*)", re.IGNORECASE)


def _sheet_number_label(target: str) -> str:
    """Extract the bare sheet number from a <ref>'s target filename, e.g.
    "Calneh - Nimrod Sheet 04 - IE Dynamics.xml" -> "4", "...Sheet 01-02
    ...xml" -> "1-2" -- leading zeroes stripped from each part of a
    range. Falls back to the target's filename (extension stripped) if it
    doesn't match the "Sheet NN[-MM]" pattern."""
    m = _SHEET_NUM_RE.search(target)
    if not m:
        return target[:-4] if target.lower().endswith(".xml") else target
    return "-".join(str(int(p)) for p in m.group(1).split("-"))


def _typst_concordance_entry(entry):
    """Render a concordance <entry> (form/orth + sense/def, same as the
    lexicon) to Typst: its headword, its definition(s), and a 3-column
    table of every occurrence -- sheet number (leading zeroes stripped),
    line number, and context (the 5-word-before/bold-occurrence/5-word-
    after text already baked into each <xr>'s <ref> by
    generate_concordance_file)."""
    term = _typst_entry_headword(entry)
    if term is None:
        return None
    defs = _typst_entry_defs(entry)
    if not defs:
        return None

    rows = []
    for xr in entry.findall(f"{_TEI_FMT}xr"):
        ref_el = xr.find(f"{_TEI_FMT}ref")
        if ref_el is None:
            continue
        # _inline() so the <hi rend="bold"> wrapping the occurrence itself
        # renders as #strong[...], not a bare/dropped text grab.
        content = _inline(ref_el).strip()
        if not content:
            continue
        sheet_label = _tescape(_sheet_number_label(ref_el.get("target", "")))
        line_label = _tescape(ref_el.get("n", ""))
        rows.append((sheet_label, line_label, _mark(ref_el, content)))

    occ_block = ""
    if rows:
        header_cells = [
            f'    table.cell(fill: rgb("#1e4d8c"))[#text(fill: white, weight: "bold")[{h}]]'
            for h in ("Chapter", "Line", "Context")
        ]
        body_cells = []
        for sheet_label, line_label, content in rows:
            body_cells.append(f"    [{sheet_label}]")
            body_cells.append(f"    [{line_label}]")
            body_cells.append(f"    [{content}]")
        cell_lines = (
            "    table.header(\n"
            + ",\n".join(header_cells)
            + "\n    ),\n"
            + ",\n".join(body_cells)
        )
        occ_block = (
            "\n  #block[\n"
            '    #set text(size: 9pt)\n'
            '    #set par(justify: false)\n'
            '    #set table.cell(breakable: false)\n'
            "    #table(\n"
            "      columns: (auto, auto, 1fr),\n"
            "      align: (center, center, left),\n"
            '      stroke: (paint: rgb("#bfd7f0"), thickness: 0.5pt),\n'
            + cell_lines
            + "\n    )\n"
            "  ]"
        )

    return f"{term}{_typst_entry_def_block(defs)}{occ_block}"


def _typst_entries_category(category_node, entry_renderer):
    """Render a <div type='category'> (shared skeleton for the lexicon and
    the concordance) to Typst: a level-2 heading followed by its <entry>
    children, each rendered by entry_renderer."""
    parts = []
    head_el = category_node.find(f"{_TEI_FMT}head")
    if head_el is not None:
        parts.append(f"== {_mark(head_el, _inline(head_el))}")

    entries = [
        r for r in (
            entry_renderer(e)
            for e in category_node.findall(f"{_TEI_FMT}entry")
        )
        if r
    ]
    if entries:
        parts.append("\n\n".join(entries))

    return "\n\n".join(p for p in parts if p and p.strip())


def _typst_entries_div(div_node, entry_renderer):
    """Render a <div type='lexicon'|'concordance'> (glossary-format
    document) to Typst: a level-1 heading followed by each <div
    type='category'> child, each entry rendered by entry_renderer."""
    parts = []
    head_el = div_node.find(f"{_TEI_FMT}head")
    if head_el is not None:
        parts.append(f"= {_mark(head_el, _inline(head_el))}")

    for category in div_node.findall(f"{_TEI_FMT}div[@type='category']"):
        chunk = _typst_entries_category(category, entry_renderer)
        if chunk:
            parts.append(chunk)

    return "\n\n".join(p for p in parts if p and p.strip())


# Sheet 99 is listed in the table of contents by its two parts, the lexicon
# and the bibliography: each part's level-1 heading carries a <part-page>
# marker in its body (so it moves with the heading to its page), read by
# `typst query` like a chapter's <section-page> markers (see
# _toc_section_label). It sits outside the round-trip marker, so the sync
# never sees it.
_PART_PAGE_MARKER = "#context [#metadata(counter(page).get().first()) <part-page>]"


def _typst_lexicon(lexicon_node):
    out = _typst_entries_div(lexicon_node, _typst_lexicon_entry)
    return out.replace("= ", f"= {_PART_PAGE_MARKER}", 1) if out.startswith("= ") else out


def _typst_book_bibliography(div_node):
    """Render Sheet 99's <div type="bibliography"> to Typst: a new page, a
    level-1 heading (with its <part-page> marker) and the <listBibl> as
    hanging-indent entries. The entries carry no round-trip marker: they are
    rebuilt from the chapters on every run (the hand-authored source entries
    are edited in the XML), so an edit made in the .typ would be lost.

    An entry with exactly the same authors as the one before it
    (_bib_author_signature) shows a 3-em dash in their place, the authors'
    names (and an "et al.") being printed only in the first of the series.
    This is a rendering convention only: the XML keeps every <author>."""
    head_el = div_node.find(f"{_TEI_FMT}head")
    title = _inline(head_el) if head_el is not None else _tescape(_BIBLIOGRAPHY_HEAD)
    entries = []
    previous = None
    for b in div_node.find(f"{_TEI_FMT}listBibl").findall(f"{_TEI_FMT}bibl"):
        sig = _bib_author_signature(b)
        shown = b
        if sig and sig == previous:
            shown = copy.deepcopy(b)
            authors = shown.findall(f"{{{TEI_NS}}}author")
            rest = re.sub(r"^\s*,?\s*et al\.", "", authors[-1].tail or "") if sig[1] else (authors[-1].tail or "")
            for a in authors:
                shown.remove(a)
            shown.text = "\u2014\u2014\u2014" + rest
        previous = sig
        content = _inline(shown).strip()
        if content:
            entries.append(content)
    if not entries:
        return None
    return (
        "#pagebreak()\n\n"
        f"= {_PART_PAGE_MARKER}{title}\n\n"
        "#block[\n"
        '  #set par(hanging-indent: 1.5em, justify: false, leading: 0.65em, spacing: 0.9em)\n'
        '  #set text(size: 10pt)\n'
        + "\n\n".join(entries)
        + "\n]"
    )


def _typst_concordance(concordance_node):
    return _typst_entries_div(concordance_node, _typst_concordance_entry)


def _typst_shared_defs() -> str:
    """The colours, helper functions and heading/figure show rules every
    chapter-like document shares (a chapter, the lexicon, the foreword of
    the front matter)."""
    return (
        '#let accent  = rgb("#1e4d8c")\n'
        '#let q-bg    = rgb("#dbeeff")\n'
        '#let q-border = rgb("#a9d7ff")\n'
        '#let muted   = rgb("#6b7280")\n'
        '#let occurrence-gray = rgb("#d1d5db")\n'
        + (f'#let initial(letter) = text(size: {INITIAL_SIZE}, font: ("{INITIAL_FONT}", "New Computer Modern"))[#letter]\n'
           if INITIAL_FONT else '')
        + ''
        '#let w(body) = [#super[#box(width: 4pt, height: 4pt, fill: q-border)]#h(1pt)#sym.wj#body]\n'
        '#let y(body) = [#super[#box(width: 4pt, height: 4pt, fill: occurrence-gray)]#h(1pt)#sym.wj#body]\n'
        '#let fn_ref(lbl) = context counter(footnote).at(query(lbl).first().location()).first()\n'
        '#let qcaps(body) = smallcaps(body)\n'
        '\n'
        '#show heading.where(level: 1): it => [\n'
        '  #v(1.6em, weak: true)\n'
        '  #text(size: 16pt, weight: "bold", fill: accent)[#it]\n'
        '  #v(0.2em, weak: true)\n'
        '  #line(length: 100%, stroke: 0.5pt + q-border)\n'
        '  #v(0.9em, weak: true)\n'
        ']\n'
        # The <section-page> marker (read by `typst query` for the table of
        # contents) sits in the same paragraph as the heading text, so it can
        # never be left on the previous page when the heading moves to the next.
        # A chapter's sections are level 2 (their subsections level 3), so the
        # markers are exactly the sections the table of contents lists.
        # Everything after the first em dash of a section/subsection heading
        # is its subtitle, set on its own muted italic line below it. The
        # split is done here, on the rendered heading body, so the heading
        # stays one plain round-trip unit in the .typ (see _mark()).
        '#let dash-split(body) = {\n'
        '  let kids = if body.has("children") { body.children } else { (body,) }\n'
        '  let head = ()\n'
        '  let sub = ()\n'
        '  let found = false\n'
        '  for k in kids {\n'
        '    if found { sub.push(k) }\n'
        '    else if k.func() == text and k.text.contains("—") {\n'
        '      let parts = k.text.split("—")\n'
        '      let first = parts.first().trim(at: end)\n'
        '      if first != "" { head.push(text(first)) }\n'
        '      let rest = parts.slice(1).join("—").trim(at: start)\n'
        '      if rest != "" { sub.push(text(rest)) }\n'
        '      found = true\n'
        '    } else { head.push(k) }\n'
        '  }\n'
        '  if not found { return (body, none) }\n'
        # Only the spaces right at the dash go: those inside the subtitle
        # (e.g. around a tagged name or term) must stay.
        '  while head.len() > 0 and head.last() == [ ] { head = head.slice(0, -1) }\n'
        '  while sub.len() > 0 and sub.first() == [ ] { sub = sub.slice(1) }\n'
        '  (head.join(), sub.join())\n'
        '}\n'
        '#let subtitled(body, size, weight, sub-size) = {\n'
        '  let (h, sub) = dash-split(body)\n'
        '  text(size: size, weight: weight, fill: accent, h)\n'
        '  if sub != none {\n'
        '    linebreak()\n'
        '    text(size: sub-size, weight: "regular", style: "italic", fill: muted, sub)\n'
        '  }\n'
        '}\n'
        '#show heading.where(level: 2): it => [\n'
        '  #v(1.4em, weak: true)\n'
        '  #block(sticky: true, width: 100%)[#context [#metadata(counter(page).get().first()) <section-page>]'
        '#subtitled(it.body, 14pt, "bold", 11.5pt)]\n'
        '  #v(1.3em, weak: true)\n'
        ']\n'
        '#show heading.where(level: 3): it => [\n'
        '  #v(1.4em, weak: true)\n'
        '  #block(sticky: true, width: 100%)[#subtitled(it.body, 12pt, "regular", 10.5pt)]\n'
        '  #v(1em, weak: true)\n'
        ']\n'
        '#show heading.where(level: 4): it => [\n'
        '  #v(1.6em, weak: true)\n'
        '  #line(length: 35%, stroke: 0.6pt + q-border)\n'
        '  #v(0.5em, weak: true)\n'
        '  #subtitled(it.body, 11pt, "bold", 10pt)\n'
        '  #v(0.7em, weak: true)\n'
        ']\n'
        '\n'
        '#set figure(gap: 1.2em)\n'
        '#show figure: it => {\n'
        '  set par(first-line-indent: 0pt)\n'
        '  align(center, it)\n'
        '}\n'
        '#show figure.caption: it => {\n'
        '  set text(size: 9.5pt, style: "italic")\n'
        '  set par(first-line-indent: 0pt, justify: false)\n'
        '  align(center, it)\n'
        '}\n'
    )


def generate_typst_doc(xml_path, fmt_key=None, start_page=1):
    """Parse a TEI XML file and return (typst_document, units, root):
    - typst_document: complete Typst document string
    - units: the round-trip collector, i.e. an ordered list of XML nodes,
      where units[i-1] is the node whose text produced marker id i
    - root: the parsed XML root element the units belong to (or None on
      a parse error)
    `fmt_key` names the paper format (a key of PAPER_FORMATS) the document is
    set for; the round-trip units are the same for every format. `start_page`
    is the book page number the document's first page carries.
    """
    global _collector, _current_format
    _collector = []
    _current_format = fmt_key or next(iter(PAPER_FORMATS))
    try:
        doc = _parse_tei_xml(xml_path)
    except Exception as e:
        return f"// ERROR: could not parse {xml_path.name}: {e}\n", _collector, None

    root = doc.getroot()
    _normalize_bibl_whitespace(root)

    title_stmt_titles = root.findall(f".//{_TEI_FMT}titleStmt/{_TEI_FMT}title")
    title_el = next((t for t in title_stmt_titles if t.get("type") != "sub"), None)
    raw_title = (
        _strip_sheet_number_zeros(" ".join("".join(title_el.itertext()).split()))
        if title_el is not None else xml_path.stem
    )

    subtitle_el = next((t for t in title_stmt_titles if t.get("type") == "sub"), None)
    raw_subtitle = (
        " ".join("".join(subtitle_el.itertext()).split())
        if subtitle_el is not None else ""
    )

    author_el = root.find(f".//{_TEI_FMT}titleStmt/{_TEI_FMT}author")
    raw_author = (author_el.text or "").strip() if author_el is not None else ""

    date_el = root.find(f".//{_TEI_FMT}publicationStmt/{_TEI_FMT}date")
    raw_date = (date_el.text or "").strip() if date_el is not None else ""

    # The title's "N — Sheet Name" is split visually into two differently
    # sized/weighted lines, the dash itself dropped -- but only when it's
    # plain text (no inline markup) with one or two dashes to split cleanly
    # on. With two ("Chapter N — Title — Subtitle"), the first separates the
    # chapter number from the title and the second the title from its
    # subtitle, which goes on a third, smaller line. An inline-marked-up
    # title (e.g. containing <q>), or one with no dash at all, falls back to
    # a single bold line instead of guessing at a split point. Either way the whole thing stays one _mark()-wrapped
    # unit, exactly as any other heading (see the head_el handling above)
    # -- the round-trip sync only ever sees/replaces this single unit's
    # plain semantic content, never the styling wrapped around it.
    if title_el is not None and len(title_el) == 0 and raw_title.count("—") in (1, 2):
        _t1, _t2, *_t3 = (p.strip() for p in raw_title.split("—"))
        title_content = (
            f'#text(size: 12pt, fill: accent)[{_tescape(_t1)}]\n'
            '  #linebreak()\n'
            f'  #text(size: 16pt, weight: "bold", fill: accent)[{_tescape(_t2)}]'
        )
        if _t3:
            title_content += (
                '\n  #linebreak()\n'
                f'  #text(size: 13pt, style: "italic", fill: accent)[{_tescape(_t3[0])}]'
            )
    elif title_el is not None:
        title_content = (
            f'#text(size: 16pt, weight: "bold", fill: accent)'
            f'[{_strip_sheet_number_zeros(_inline(title_el))}]'
        )
    else:
        title_content = f'#text(size: 16pt, weight: "bold", fill: accent)[{_tescape(raw_title)}]'
    title_markup = _mark(title_el, title_content) if title_el is not None else title_content
    subtitle_markup = (
        _mark(subtitle_el, _inline(subtitle_el))
        if subtitle_el is not None and raw_subtitle else _tescape(raw_subtitle)
    )
    meta_bits = []
    if author_el is not None and raw_author:
        meta_bits.append(_mark(author_el, _tescape(raw_author)))
    if date_el is not None and raw_date:
        meta_bits.append(_mark(date_el, _tescape(raw_date)))
    meta_line = " · ".join(meta_bits)

    body = root.find(f".//{_TEI_FMT}body")
    global _initial_target
    _initial_target = _choose_initial_paragraph(body)
    cycles   = body.findall(f"{_TEI_FMT}div[@type='cycle']")   if body is not None else []
    sections = body.findall(f"{_TEI_FMT}div[@type='section']") if body is not None else []
    # The concordance's occurrence tables are wide (sheet/line/context) --
    # give the whole document landscape pages rather than wrapping each
    # table individually (see _typst_table's own per-wide-table flip).
    is_concordance = body is not None and body.find(f"{_TEI_FMT}div[@type='concordance']") is not None

    body_parts = []

    front = root.find(f".//{_TEI_FMT}front")
    abstract_div = front.find(f"{_TEI_FMT}div[@type='abstract']") if front is not None else None
    if abstract_div is not None:
        abstract_str = _typst_abstract(abstract_div)
        if abstract_str.strip():
            body_parts.append(abstract_str)

    if cycles:
        for i, cycle in enumerate(cycles):
            n = cycle.get("n", str(i + 1))
            q_div = cycle.find(f"{_TEI_FMT}div[@type='question']")
            a_div = cycle.find(f"{_TEI_FMT}div[@type='answer']")

            body_parts.append(
                f'#text(size: 7pt, weight: "bold", fill: muted, tracking: 1.5pt)'
                f'[EXCHANGE {_tescape(n)}]\n#v(0.3em)'
            )

            if q_div is not None:
                q_content = _blocks(q_div, heading_level=3)
                body_parts.append(
                    '#rect(fill: q-bg, stroke: 0.5pt + q-border, radius: 7pt,'
                    ' width: 100%, inset: (x: 1em, y: 0.7em))[\n'
                    '  #set text(style: "italic")\n'
                    f'  {q_content}\n'
                    ']'
                )

            if a_div is not None:
                a_content = _blocks(a_div, heading_level=3)
                if a_content.strip():
                    body_parts.append(a_content)

            body_parts.append('#v(2em)')

    elif sections:
        for section in sections:
            body_parts.append(_typst_section(section))

    else:
        if body is not None:
            body_parts.append(_blocks(body))

    back = root.find(f".//{_TEI_FMT}back")
    bib_div = back.find(f"{_TEI_FMT}div[@type='bibliography']") if back is not None else None
    if bib_div is not None:
        bib_str = _typst_bibliography(bib_div)
        if bib_str.strip():
            body_parts.append(bib_str)

    body_str = "\n\n".join(p for p in body_parts if p.strip())

    preamble = (
        f'#set document(title: "{_typst_str(raw_title)}")\n'
        f'#set page({_page_setup(_current_format, flipped=is_concordance)}, numbering: "1")\n'
        '#set text(font: "New Computer Modern", size: 11pt, lang: "en")\n'
        '#set par(justify: true, leading: 0.75em, spacing: 1.2em)\n'
        '\n'
        + _typst_shared_defs() +
        '\n'
        + (f'#counter(page).update({start_page})\n' if start_page > 1 else '')
        + '\n'
        + _typst_title_block(title_markup, subtitle_markup if raw_subtitle else "", meta_line)
    )

    return f"// book-start-page: {start_page}\n" + preamble + body_str + "\n", _collector, root


def _typst_title_block(title_markup, subtitle_markup="", meta_line="") -> str:
    """A chapter's title block: the title, an optional subtitle, the meta line
    and a rule. It is itself a top float: a floating figure goes to the top
    of its page, ahead of ordinary content, and would otherwise land above
    the title. Floats keep source order, so the title stays first."""
    return (
        '#place(top + center, float: true, clearance: 1.5em)[\n'
        '#block(width: 100%)[\n'
        '#set par(justify: false)\n'
        '#align(center)[\n'
        f'  {title_markup}\n'
        + (
            '  #v(0.3em)\n'
            f'  #text(size: 12pt, fill: muted, style: "italic")[{subtitle_markup}]\n'
            if subtitle_markup else ""
        )
        + ']\n'
        '#v(0.3em)\n'
        + f'#text(size: 9pt, fill: muted)[{meta_line}]\n'
        '#v(0.5em)\n'
        '#line(length: 100%, stroke: 1.5pt + q-border)\n'
        ']]\n'
        '\n'
    )


# ──────────────────────────────────────────────────────────────────────────────
# PDF COMPILATION AND CONSOLIDATION
# ──────────────────────────────────────────────────────────────────────────────

_PDFINFO_PAGES_RE = re.compile(r"Pages:\s*(\d+)")
_PDFINFO_SIZE_RE = re.compile(r"Page size:\s*([\d.]+)\s*x\s*([\d.]+)\s*pts")


def _pad_to_even_pages(pdf_path: Path) -> bool:
    """If pdf_path has an odd page count, append one blank page (matching
    its own page size, so a landscape sheet like the concordance pads with
    a landscape blank) so it always ends even -- keeping every subsequent
    sheet starting on an odd, right-hand page once merged in sequence into
    the consolidated PDF. A no-op (returns True) if already even."""
    try:
        info = subprocess.run(
            ["pdfinfo", str(pdf_path)], cwd=str(DIR), capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("    ERROR: `pdfinfo` not found on PATH")
        return False
    if info.returncode != 0:
        print(f"    ERROR reading {pdf_path.name} page info:\n{info.stderr}")
        return False
    pages_m = _PDFINFO_PAGES_RE.search(info.stdout)
    size_m = _PDFINFO_SIZE_RE.search(info.stdout)
    if not pages_m or not size_m:
        print(f"    ERROR: could not parse pdfinfo output for {pdf_path.name}")
        return False
    if int(pages_m.group(1)) % 2 == 0:
        return True

    width, height = size_m.group(1), size_m.group(2)
    # Created inside DIR, not the system temp dir: the sandboxed `typst`
    # binary (snap-confined on this machine) can't read files under /tmp.
    with tempfile.TemporaryDirectory(dir=str(DIR)) as tmp:
        tmp_dir = Path(tmp)
        blank_typ = tmp_dir / "blank.typ"
        blank_pdf = tmp_dir / "blank.pdf"
        blank_typ.write_text(f"#set page(width: {width}pt, height: {height}pt)\n", encoding="utf-8")
        try:
            r1 = subprocess.run(
                ["typst", "compile", str(blank_typ), str(blank_pdf)],
                cwd=str(DIR), capture_output=True, text=True,
            )
        except FileNotFoundError:
            print("    ERROR: `typst` CLI not found on PATH")
            return False
        if r1.returncode != 0:
            print(f"    ERROR generating blank padding page for {pdf_path.name}:\n{r1.stderr}")
            return False

        padded_pdf = tmp_dir / "padded.pdf"
        try:
            r2 = subprocess.run(
                ["pdftk", str(pdf_path), str(blank_pdf), "cat", "output", str(padded_pdf)],
                cwd=str(DIR), capture_output=True, text=True,
            )
        except FileNotFoundError:
            print("    ERROR: `pdftk` not found on PATH")
            return False
        if r2.returncode != 0:
            print(f"    ERROR padding {pdf_path.name} to an even page count:\n{r2.stderr}")
            return False
        padded_pdf.replace(pdf_path)
    return True


def compile_typst_pdf(typ_path: Path) -> bool:
    """Compile a .typ file to a same-named .pdf in the same directory, then
    pad it with a trailing blank page if that leaves it on an odd page
    count (see _pad_to_even_pages)."""
    pdf_path = typ_path.with_suffix(".pdf")
    try:
        result = subprocess.run(
            ["typst", "compile", str(typ_path), str(pdf_path)],
            cwd=str(DIR), capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("    ERROR: `typst` CLI not found on PATH")
        return False
    if result.returncode != 0:
        print(f"    ERROR compiling {typ_path.name}:\n{result.stderr}")
        return False
    return _pad_to_even_pages(pdf_path)


def consolidate_pdfs(pdf_paths, output_path: Path) -> bool:
    """Merge pdf_paths (in order) into a single PDF at output_path via pdftk."""
    if not pdf_paths:
        return False
    try:
        result = subprocess.run(
            ["pdftk", *[str(p) for p in pdf_paths], "cat", "output", str(output_path)],
            cwd=str(DIR), capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("    ERROR: `pdftk` not found on PATH")
        return False
    if result.returncode != 0:
        print(f"    ERROR merging PDFs:\n{result.stderr}")
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def _outputs_stale(source_paths, output_path: Path) -> bool:
    """True if output_path doesn't exist yet, or any path in source_paths --
    or this script itself, since a generate_book.py change can alter how
    every output renders -- has a newer mtime than it. The Make-like check
    _emit_typst_and_pdf (and the consolidated-PDF step in main()) use to
    decide whether a rebuild is actually needed."""
    if not output_path.exists():
        return True
    out_mtime = output_path.stat().st_mtime
    if _SCRIPT_PATH.stat().st_mtime > out_mtime:
        return True
    return any(p.stat().st_mtime > out_mtime for p in source_paths)


_toc_cache = {}   # format key -> {chapter title: (start page, [(section title, page), ...])}


def _read_toc_cache():
    """The page numbers recorded in the existing front matter's table of
    contents, per paper format: a sheet that is not rebuilt keeps its section
    pages, so `typst query` need not be run for it again."""
    _toc_cache.clear()
    path = DIR / FRONT_MATTER_FILENAME
    if not path.exists():
        return
    try:
        root = _parse_tei_xml(path).getroot()
    except Exception:
        return
    for key in PAPER_FORMATS:
        cache = {}
        for item in root.findall(f".//{_TEI_FMT}div[@type='toc']/{_TEI_FMT}list/{_TEI_FMT}item"):
            title = " ".join((item.text or "").split())
            try:
                start = int(item.get(f"page_{key}") or 0)
                secs = [(" ".join((si.text or "").split()), int(si.get(f"page_{key}") or 0),
                         [(" ".join((ssi.text or "").split()), int(ssi.get(f"page_{key}") or 0))
                          for ssi in si.findall(f"{_TEI_FMT}list/{_TEI_FMT}item")])
                        for si in item.findall(f"{_TEI_FMT}list/{_TEI_FMT}item")]
            except ValueError:
                continue
            cache[title] = (start, secs)
        _toc_cache[key] = cache


def _plain_heading_text(el):
    """The text of a heading, as the table of contents shows it: markup is
    dropped, except that a <q> keeps its quotation marks (straight ones, which
    Typst sets as typographic quotes, exactly as in the heading itself)."""
    parts = [el.text or ""]
    for child in el:
        inner = _plain_heading_text(child)
        parts.append(f'"{inner}"' if _local(child) == "q" else inner)
        parts.append(child.tail or "")
    return "".join(parts)


def _toc_heading_title(div):
    """The table-of-contents title of a section/subsection division, or None
    if it has no <head>. A heading's subtitle (after its first em dash, see
    dash-split in the preamble) is left out of the TOC."""
    head = div.find(f"{{{TEI_NS}}}head")
    if head is None:
        return None
    return " ".join(_plain_heading_text(head).split("—", 1)[0].split())


def _toc_section_label(root):
    """The label of the page markers that stand for a sheet's table-of-
    contents sections: "part-page" for Sheet 99 (the lexicon and the
    bibliography, see _PART_PAGE_MARKER), "section-page" for a chapter."""
    is_lexicon = any(div.get("type") == "lexicon" for div in root.iter(f"{{{TEI_NS}}}div"))
    return "part-page" if is_lexicon else "section-page"


def _section_titles(root):
    """[(title, [subsection title, ...]), ...]: a sheet's <div type="section">
    divisions, in order, each with its <div type="subsection"> divisions --
    an empty list unless the table of contents lists them (TOC_SUBSECTIONS)."""
    if _toc_section_label(root) == "part-page":
        # Sheet 99: its parts (the lexicon, the bibliography) stand for sections.
        return [(_toc_heading_title(div), []) for div in root.iter(f"{{{TEI_NS}}}div")
                if div.get("type") in ("lexicon", "bibliography")
                and _local(div.getparent()) == "body" and _toc_heading_title(div)]
    out = []
    for div in root.iter(f"{{{TEI_NS}}}div"):
        if div.get("type") == "section":
            title = _toc_heading_title(div)
            if title is None:
                continue
            subs = []
            if TOC_SUBSECTIONS:
                for sub in div.iter(f"{{{TEI_NS}}}div"):
                    if sub.get("type") == "subsection":
                        sub_title = _toc_heading_title(sub)
                        if sub_title is not None:
                            subs.append(sub_title)
            out.append((title, subs))
    return out


def _sheet_sections(xml_path: Path, typ_path: Path, key: str, start: int, rebuilt: bool):
    """[(section title, page, [(subsection title, page), ...]), ...] of a sheet
    in format `key` (the subsection lists empty unless TOC_SUBSECTIONS): read
    from the table of contents recorded on the previous run when the sheet was
    not rebuilt (and starts at the same page), else queried from the .typ."""
    root = _parse_tei_xml(xml_path).getroot()
    titles = _section_titles(root)
    if not titles:
        return []
    cached = _toc_cache.get(key, {}).get(_sheet_title(root))
    if (not rebuilt and cached and cached[0] == start
            and [(t, [st for st, _ in subs]) for t, _, subs in cached[1]] == titles
            and all(p for _, p, _ in cached[1])
            and all(sp for _, _, subs in cached[1] for _, sp in subs)):
        return cached[1]
    pages = _query_section_pages(typ_path, _toc_section_label(root))
    if len(pages) != len(titles):
        print(f"  {_tag('WARN')}{typ_path.name}: found {len(pages)} section page markers for "
              f"{len(titles)} sections; the missing ones are listed on the sheet's first page")
        pages = (pages + [start] * len(titles))[:len(titles)]
    n_subs = sum(len(subs) for _, subs in titles)
    sub_pages = _query_section_pages(typ_path, "subsection-page") if n_subs else []
    if len(sub_pages) != n_subs:
        print(f"  {_tag('WARN')}{typ_path.name}: found {len(sub_pages)} subsection page markers for "
              f"{n_subs} subsections; the missing ones are listed on their section's page")
    out = []
    k = 0
    for (title, subs), page in zip(titles, pages):
        sub_entries = []
        for sub_title in subs:
            sub_entries.append((sub_title, sub_pages[k] if k < len(sub_pages) else page))
            k += 1
        out.append((title, page, sub_entries))
    return out


def _emit_typst_and_pdf(xml_path: Path, numbered: bool = True) -> None:
    """Regenerate xml_path's .typ files from its (now-validated) XML and
    compile each to a same-named .pdf -- one pair per paper format
    (PAPER_FORMATS; the concordance only in CONCORDANCE_FORMATS). A format is
    skipped when its .pdf is already newer than xml_path, its images and this
    script, and was generated for the page the sheet now starts at (Make-like:
    nothing that could change its content has changed since it was last
    built). A `numbered` sheet is part of the book: it starts where the
    previous one ended, and its layout (start page, page count, section
    pages) is recorded for the table of contents."""
    inputs = (xml_path, *_figure_image_paths(xml_path))
    for key in PAPER_FORMATS:
        if xml_path.name == CONCORDANCE_FILENAME and key not in CONCORDANCE_FORMATS:
            continue
        start = _book_next_page.get(key, 1) if numbered else 1
        typ_path = _sheet_typ(xml_path, key)
        pdf_path = typ_path.with_suffix(".pdf")
        rebuilt = _outputs_stale(inputs, pdf_path) or _typ_start_page(typ_path) != start
        if not rebuilt:
            print(f"  {_tag('SKIP')}{pdf_path.name} (up to date)")
        else:
            text, _, _ = generate_typst_doc(xml_path, key, start_page=start)
            typ_path.write_text(text, encoding="utf-8")
            # Pin the .typ mtime to the .xml mtime so an unchanged pair reads as
            # "in sync" next run, and only a subsequent hand-edit of the .typ
            # file (which bumps its mtime past the .xml's) triggers a new sync.
            xml_mtime = xml_path.stat().st_mtime
            os.utime(typ_path, (xml_mtime, xml_mtime))
            print(f"  {_tag('TYPST')}{typ_path.name}")
            if compile_typst_pdf(typ_path):
                print(f"  {_tag('PDF')}{pdf_path.name}")
            else:
                print(f"  {_tag('PDF')}{pdf_path.name}  (FAILED)")
        if not numbered:
            continue
        pages = _pdf_pages(pdf_path)
        if pages is None:
            continue
        sections = _sheet_sections(xml_path, typ_path, key, start, rebuilt)
        _book_next_page[key] = start + pages
        _book_layout.setdefault(key, {})[xml_path.name] = {"start": start, "pages": pages, "sections": sections}


# ──────────────────────────────────────────────
# FRONT MATTER: TITLE PAGE AND TABLE OF CONTENTS
# ──────────────────────────────────────────────

def _front_matter_header(root):
    """(title, subtitle, author, publication lines) from the front matter's
    <teiHeader>. The author is the <persName> without its <roleName>; each
    child of <publicationStmt> is one line of the title page."""
    title = subtitle = author = ""
    stmt = root.find(f".//{_TEI_FMT}titleStmt")
    if stmt is not None:
        for t in stmt.findall(f"{_TEI_FMT}title"):
            text = " ".join("".join(t.itertext()).split())
            if t.get("type") == "sub":
                subtitle = text
            else:
                title = text
        author_el = stmt.find(f"{_TEI_FMT}author")
        if author_el is not None:
            pers = author_el.find(f"{_TEI_FMT}persName")
            src = pers if pers is not None else author_el
            # the person's name, without the <roleName> ("Author") after it
            parts = [src.text or ""]
            for child in src:
                if child.tag != f"{_TEI_FMT}roleName":
                    parts.append("".join(child.itertext()))
                parts.append(child.tail or "")
            author = " ".join("".join(parts).split())
    lines = []
    pub = root.find(f".//{_TEI_FMT}publicationStmt")
    if pub is not None:
        for child in pub:
            text = " ".join("".join(child.itertext()).split())
            if text:
                lines.append(text)
    return title, subtitle, author, lines


def _toc_infos(xml_files, lex_path):
    """[(sheet title, [(section title, page_a4-or-whatever)], start)] per
    format key, for the sheets of the book in order (chapters, then the
    lexicon), from the layout recorded while they were built."""
    infos = {key: [] for key in PAPER_FORMATS}
    for path in (*xml_files, lex_path):
        if not path.exists():
            continue
        title = _sheet_title(_parse_tei_xml(path).getroot())
        for key in PAPER_FORMATS:
            lay = _book_layout.get(key, {}).get(path.name)
            if lay:
                infos[key].append((title, lay["sections"], lay["start"]))
    return infos


def _write_toc_xml(path: Path, infos) -> bool:
    """Rewrite the table of contents of the front matter file `path`: only
    its <div type="toc"> block is replaced, so the <teiHeader> and every
    other division of the <body> (a foreword, ...) are kept verbatim. Each
    chapter is an <item> with one page_<format> attribute per format, its
    sections a nested list, and (with --sub) each section's subsections a
    list nested in its <item>. Returns True if the file changed."""
    from xml.sax.saxutils import escape
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    keys = list(PAPER_FORMATS)
    first = infos[keys[0]]
    items = []
    for i, (title, secs, _st) in enumerate(first):
        attrs = " ".join(f'page_{k}="{infos[k][i][2]}"' for k in keys if i < len(infos[k]))
        items.append(f'          <item n="{i + 1}" {attrs}>{escape(title)}')
        if secs:
            items.append('            <list type="bulleted">')
            for j, (st, _pg, subs) in enumerate(secs):
                sattrs = " ".join(f'page_{k}="{infos[k][i][1][j][1]}"' for k in keys
                                  if i < len(infos[k]) and j < len(infos[k][i][1]))
                if not subs:
                    items.append(f'              <item {sattrs}>{escape(st)}</item>')
                    continue
                items.append(f'              <item {sattrs}>{escape(st)}')
                items.append('                <list type="bulleted">')
                for m, (sst, _spg) in enumerate(subs):
                    ssattrs = " ".join(f'page_{k}="{infos[k][i][1][j][2][m][1]}"' for k in keys
                                       if i < len(infos[k]) and j < len(infos[k][i][1])
                                       and m < len(infos[k][i][1][j][2]))
                    items.append(f'                  <item {ssattrs}>{escape(sst)}</item>')
                items.append('                </list>')
                items.append('              </item>')
            items.append('            </list>')
        items.append('          </item>')
    toc = ('      <div type="toc">\n        <list type="ordered">\n'
           + "\n".join(items) + '\n        </list>\n      </div>\n')
    toc_m = re.search(r'^[ \t]*<div type="toc">.*?</div>[ \t]*\n', raw, re.S | re.M)
    body_m = re.search(r'^[ \t]*<body>[ \t]*\n', raw, re.M)
    if toc_m:
        xml = raw[:toc_m.start()] + toc + raw[toc_m.end():]
    elif body_m:
        xml = raw[:body_m.end()] + toc + raw[body_m.end():]
    else:
        header = ("  <teiHeader>\n    <fileDesc>\n      <titleStmt>\n        <title></title>\n"
                  "        <title type=\"sub\"></title>\n      </titleStmt>\n      <publicationStmt>\n"
                  "        <p></p>\n      </publicationStmt>\n    </fileDesc>\n  </teiHeader>")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<TEI xmlns="http://www.tei-c.org/ns/1.0">\n'
               f'{header}\n  <text>\n    <body>\n{toc}    </body>\n  </text>\n</TEI>\n')
    if raw == xml:
        return False
    path.write_text(xml, encoding="utf-8")
    return True


def _typst_front_matter(root, key: str, chapters) -> str:
    """The Typst document of the front matter for format `key`: a title page
    (the title scaled to fill 90% of the text width, subtitle, author,
    publication lines), the table of contents and, when the file has a
    <div type="foreword">, the foreword (see _typst_foreword), listed first in
    the table of contents. The title page is unnumbered; the table of
    contents and the foreword are numbered in small-cap roman numerals."""
    title, subtitle, author, pub_lines = _front_matter_header(root)
    t = _tescape(title)
    out = [
        f'#set document(title: "{_typst_str(title)}")',
        f'#set page({_page_setup(key)}, numbering: none)',
        '#set text(font: "New Computer Modern", size: 11pt, lang: "en")',
        '#set par(justify: false, leading: 0.75em, spacing: 1.2em)',
        '#let accent = rgb("#1e4d8c")',
        '#let muted = rgb("#6b7280")',
        '',
        '#v(1fr)',
        '#layout(avail => {',
        f'  let probe = text(weight: "bold", size: 100pt)[{t}]',
        '  let w = measure(probe).width',
        '  let fs = 100pt * (0.9 * avail.width / w)',
        f'  align(center, text(weight: "bold", size: fs, fill: accent)[{t}])',
        '})',
    ]
    if subtitle:
        out += ['#v(0.35cm)', f'#align(center)[#text(size: 14pt, style: "italic", fill: muted)[{_tescape(subtitle)}]]']
    if author:
        out += ['#v(0.8cm)', f'#align(center)[#text(size: 13pt, style: "italic")[{_tescape(author)}]]']
    out.append('#v(1fr)')
    if pub_lines:
        out.append("#align(center)[" + " #linebreak() ".join(
            f'#text(size: 9pt, fill: muted)[{_tescape(ln)}]' for ln in pub_lines) + "]")
    # The table of contents and the foreword are numbered in small-cap roman
    # numerals; the title page counts as page i but shows no number.
    out += ['', '#pagebreak()',
            '#let fm-numbering(..nums) = smallcaps(numbering("i", nums.pos().first()))',
            '#set page(numbering: fm-numbering)', '',
            '#v(1cm)', '#align(center)[#text(size: 15pt, weight: "bold", fill: accent)[Table of Contents]]', '#v(1.2cm)', '']
    foreword = root.find(f".//{_TEI_FMT}body/{_TEI_FMT}div[@type='foreword']")
    fw_head = foreword.find(f"{_TEI_FMT}head") if foreword is not None else None
    fw_title = " ".join(_plain_heading_text(fw_head).split()) if fw_head is not None else "Foreword"
    if foreword is not None:
        # Its page is only known once laid out: read back from its label.
        out.append("#grid(\n  columns: (1fr, auto),\n  align: (left + horizon, right + horizon),\n"
                   "  column-gutter: 0.5em,\n"
                   f'  [#text(weight: "bold")[{_tescape(fw_title)}]],\n'
                   '  [#text(weight: "bold")[#context fm-numbering(counter(page).at('
                   'query(<foreword-start>).first().location()).first())]],\n)')
        out += ["#v(0.7em)", ""]
    for ch_title, sections, page in chapters:
        sub = None
        for dash in (" — ", " – "):
            if dash in ch_title:
                ch_title, sub = ch_title.split(dash, 1)
                break
        out.append("#grid(\n  columns: (1fr, auto),\n  align: (left + horizon, right + horizon),\n"
                   "  column-gutter: 0.5em,\n"
                   f'  [#text(weight: "bold")[{_tescape(ch_title)}]],\n  [#text(weight: "bold")[{page}]],\n)')
        if sub:
            out.append(f'#block(above: 0.35em, below: 0.5em)[#text(size: 9.5pt, style: "italic")[{_tescape(sub)}]]')
        if sections:
            if not sub:
                out.append("#v(0.35em)")
            cells = []
            for sec_title, sec_page, subs in sections:
                cells.append(f"    [#par(hanging-indent: 1.6em)[{_tescape(sec_title)}]],")
                cells.append(f"    [{sec_page}],")
                # Subsections (--sub): indented under their section, smaller.
                for sub_title, sub_page in subs:
                    cells.append(f"    [#pad(left: 1.6em)[#text(size: 9pt)[#par(hanging-indent: 2em)"
                                 f"[{_tescape(sub_title)}]]]],")
                    cells.append(f"    [#text(size: 9pt)[{sub_page}]],")
            out.append("#pad(left: 2em)[\n  #set text(size: 10pt)\n  #set par(spacing: 0pt, leading: 0.3em)\n"
                       "  #grid(\n    columns: (1fr, auto),\n    align: (left + top, right + top),\n"
                       "    row-gutter: 0.3em,\n    column-gutter: 0.5em,\n" + "\n".join(cells) + "\n  )\n]")
        out += ["#v(0.7em)", ""]
    if foreword is not None:
        out.append(_typst_foreword(foreword, fw_head, key))
    return "\n".join(out) + "\n"


def _typst_foreword(foreword, head, key: str) -> str:
    """The front matter's <div type="foreword">, set like a chapter on the
    next right-hand page after the table of contents: the chapter title block
    (its <head> as the title), the chapter styles, and the illuminated
    initial on its first paragraph. Its first page carries the
    <foreword-start> label the table of contents reads its page from."""
    global _collector, _current_format, _initial_target
    _collector = []  # the front matter is never round-tripped; the markers are inert
    _current_format = key
    _normalize_bibl_whitespace(foreword)
    _initial_target = None
    if INITIAL_FONT:
        for child in foreword:
            if _local(child) == "div":
                break
            if _local(child) == "p":
                text = (child.text or "").lstrip()
                if text and text[0].isalpha():
                    _initial_target = child
                    break
    title = _inline(head) if head is not None else "Foreword"
    parts = []
    if foreword.text and foreword.text.strip():
        parts.append(_tescape(foreword.text.strip()))
    for child in foreword:
        if child is not head:
            chunk = _block_el(child, 3)
            if chunk:
                parts.append(chunk)
        if child.tail and child.tail.strip():
            parts.append(_tescape(child.tail.strip()))
    _initial_target = None
    return (
        '#pagebreak(to: "odd")\n'
        '#metadata(none) <foreword-start>\n'
        '#set par(justify: true, leading: 0.75em, spacing: 1.2em)\n'
        '\n'
        + _typst_shared_defs()
        + '\n'
        + _typst_title_block(f'#text(size: 16pt, weight: "bold", fill: accent)[{title}]')
        + "\n\n".join(p for p in parts if p.strip())
    )


def _emit_front_matter(xml_files, lex_path) -> None:
    """Regenerate the table of contents of the front matter file from the
    layout recorded while the sheets were built, then render the title page
    and table of contents in every paper format (`Chapter 00_a4.pdf`, ...),
    ahead of the first chapter in each consolidated book."""
    path = DIR / FRONT_MATTER_FILENAME
    if not path.exists():
        return
    infos = _toc_infos(xml_files, lex_path)
    n = len(infos[next(iter(PAPER_FORMATS))])
    if _write_toc_xml(path, infos):
        print(f"  {_tag('TOC')}{path.name} ({n} entries, page numbers updated)")
    root = _parse_tei_xml(path).getroot()
    for key in PAPER_FORMATS:
        typ_path = _sheet_typ(path, key)
        pdf_path = typ_path.with_suffix(".pdf")
        if not _outputs_stale((path,), pdf_path):
            print(f"  {_tag('SKIP')}{pdf_path.name} (up to date)")
            continue
        typ_path.write_text(_typst_front_matter(root, key, infos[key]), encoding="utf-8")
        print(f"  {_tag('TYPST')}{typ_path.name}")
        ok = compile_typst_pdf(typ_path)
        print(f"  {_tag('PDF')}{pdf_path.name}" + ("" if ok else "  (FAILED)"))


def main():
    global TOC_SUBSECTIONS
    parser = argparse.ArgumentParser(description="Build the book's sheets, lexicon, concordance and PDFs.")
    parser.add_argument("--sub", action="store_true",
                        help="also list each section's subsections in the table of contents")
    TOC_SUBSECTIONS = parser.parse_args().sub

    lex_path = DIR / LEXICON_FILENAME
    conc_path = DIR / CONCORDANCE_FILENAME
    # The lexicon and concordance are generated artifacts, derived from
    # every other sheet -- never fed back into themselves as a source of
    # first-occurrence keys or occurrences.
    xml_files = sorted(
        p for p in DIR.glob("*.xml")
        if p not in (lex_path, conc_path) and p.name not in NON_SHEET_XML_FILENAMES
    )

    if not xml_files:
        print("No .xml files found in", DIR)
        sys.exit(1)

    # Refuse to start on a malformed file (see _parse_tei_xml) before any
    # pass has had a chance to write to another one.
    for xml_path in (*xml_files, lex_path, DIR / FRONT_MATTER_FILENAME):
        if xml_path.exists():
            _parse_tei_xml(xml_path)
    _load_tagging_dictionary()

    # Sync every hand-edited .typ back into its .xml first, so relocation
    # (next) and validation see the current text, not stale XML.
    # A sheet has one .typ per paper format, all rendering the same XML. Only
    # a .typ that actually holds an edit counts (merely touching one changes
    # nothing). If more than one does, only the newest can be synced back --
    # syncing a second would revert the first one's edits, since it is
    # compared against the already-updated XML -- so the others are reported.
    for xml_path in xml_files:
        xml_mtime = xml_path.stat().st_mtime
        newer = [
            (key, _sheet_typ(xml_path, key)) for key in PAPER_FORMATS
            if _sheet_typ(xml_path, key).exists()
            and _sheet_typ(xml_path, key).stat().st_mtime > xml_mtime
        ]
        edited = sorted(
            (kt for kt in newer if sync_typst_to_xml(xml_path, kt[1], kt[0], dry_run=True)),
            key=lambda kt: kt[1].stat().st_mtime, reverse=True,
        )
        if not edited:
            continue
        key, typ_path = edited[0]
        sync_typst_to_xml(xml_path, typ_path, key)
        print(f"  {_tag('SYNC')}{typ_path.name} -> {xml_path.name} (updated)")
        for _key, other in edited[1:]:
            print(f"  {_tag('WARN')}{other.name} was also edited but is not synced back "
                  f"(only the newest edited .typ of a sheet is: {typ_path.name}); it will be overwritten")

    never_tagged_fixes = strip_never_tagged_references(xml_files)
    for filename, line, msg in sorted(never_tagged_fixes, key=lambda f: (f[0], f[1] or 0)):
        print(f"  {_tag('STRIP')}{filename}:{line}: {msg}")
    if never_tagged_fixes:
        print()

    quote_fixes = convert_literal_quotes_to_q(xml_files)
    for filename, line, msg in sorted(quote_fixes, key=lambda f: (f[0], f[1] or 0)):
        print(f"  {_tag('QUOTE')}{filename}:{line}: {msg}")
    if quote_fixes:
        print()

    promotions = promote_orphan_refs(xml_files, lex_path)
    for filename, line, msg in sorted(promotions, key=lambda f: (f[0], f[1] or 0)):
        print(f"  {_tag('PROMOTE')}{filename}:{line}: {msg}")
    if promotions:
        print()

    relocations = relocate_first_occurrences(xml_files)
    for filename, line, msg in sorted(relocations, key=lambda f: (f[0], f[1] or 0)):
        print(f"  {_tag('RELOC')}{filename}:{line}: {msg}")
    if relocations:
        print()

    _load_previous_lexicon_ids(lex_path)
    _AUTHOR_DEFINED_IDS.clear()
    _read_toc_cache()
    _book_next_page.clear()
    _book_next_page.update({key: 1 for key in PAPER_FORMATS})
    _book_layout.clear()
    registry = {}
    lexicon_entries = {}

    for xml_path in xml_files:
        # `registry`/`lexicon_entries` are shared across every file in this
        # loop (sorted, i.e. sheet order) so "first occurrence" holds across
        # the whole sheet sequence, per the conventions doc.
        validate_xml(xml_path, registry=registry, lexicon=lexicon_entries)

        _emit_typst_and_pdf(xml_path)

    _save_tagging_dictionary()
    for key in sorted({e["key"] for e in _TAGGING_DICT_ENTRIES} - _tagging_dict_used_keys, key=str.lower):
        print(f"  {_tag('WARN')}{TAGGING_DICT_PATH.name}: key {key} has no first occurrence anywhere "
              f"in the corpus; its strings tag nothing")

    print()
    generate_lexicon_file(lexicon_entries, lex_path, xml_files)
    print(f"  {_tag('LEXICON')}{lex_path.name} ({len(lexicon_entries)} entries)")
    _emit_typst_and_pdf(lex_path)

    # Generated after the lexicon, and reads its <def>s from lex_path (not
    # from its own previous version) -- the concordance mirrors Sheet 99's
    # definitions rather than maintaining its own separately hand-authored
    # set (see generate_concordance_file).
    concordance_entries = build_concordance(xml_files, lexicon_entries)
    generate_concordance_file(lexicon_entries, concordance_entries, conc_path, lex_path)
    total_occurrences = sum(len(v) for v in concordance_entries.values())
    print(f"  {_tag('CONC')}{conc_path.name} ({len(lexicon_entries)} entries, {total_occurrences} occurrences)")
    _emit_typst_and_pdf(conc_path, numbered=False)

    print()
    _emit_front_matter(xml_files, lex_path)

    # One consolidated PDF per paper format. Only the series sheets (files
    # named "...Chapter NN..." / "...Sheet NN...") of that format go into it,
    # and among those the concordance is excluded -- it's a cross-reference
    # index of the other sheets, not part of the series text itself.
    for key in PAPER_FORMATS:
        suffix = f"_{key}.pdf"
        conc_pdf = conc_path.stem + suffix
        out_name = _fmt_name(CONSOLIDATED_PDF_NAME, key)
        pdf_files = sorted(
            p for p in DIR.glob(f"*{suffix}")
            if p.name != out_name and _SHEET_NUM_RE.search(p.name) and p.name != conc_pdf
        )
        front_matter = []
        for name in (TITLE_PAGE_PDF_NAME, *EXTRA_CONSOLIDATED_PDFS):
            for candidate in (_fmt_name(name, key), name):
                if (DIR / candidate).exists():
                    front_matter.append(DIR / candidate)
                    break
        pdf_files = front_matter + pdf_files
        out_path = DIR / out_name
        print()
        if not _outputs_stale(pdf_files, out_path):
            print(f"Done [{key.upper()}]. Consolidated PDF up to date: {out_path}")
        elif consolidate_pdfs(pdf_files, out_path):
            print(f"Done [{key.upper()}]. Consolidated PDF: {out_path}")
        else:
            print(f"Done [{key.upper()}]. (PDF consolidation skipped or failed.)")


if __name__ == "__main__":
    main()
