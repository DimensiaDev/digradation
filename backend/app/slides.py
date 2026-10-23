"""Render the slides of a programme to a PPTX file.

:func:`render_slides` takes the material of a programme — the slide decks the
materials stage wrote — and returns the bytes of a PPTX file. The criterion is
"the slides for at least one module", so a caller can ask for one module's
decks; without a module the whole programme's decks are rendered.

The file is built from code, not from a template: a title slide for the
programme, a divider for every deck, and one slide per slide of that deck.

The default ``python-pptx`` template is 4:3, and widening the presentation does
not move the placeholders: they keep their 4:3 geometry, so a 16:9 file built
from that layout draws every title off-centre. Every placeholder used here is
therefore positioned explicitly. A slide with more bullets than a reader can
take in is split across continuation slides rather than shrunk until it is
unreadable.
"""

from __future__ import annotations

from io import BytesIO

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Cm, Pt

from app.agentic.models import Materials, SlideDeck
from app.render import DEFAULT_CITY, DEFAULT_YEAR, ORGANIZATION_LINES

#: The 16:9 surface of the presentation, in centimetres.
SLIDE_WIDTH = Cm(33.87)
SLIDE_HEIGHT = Cm(19.05)

#: Where the text of a slide starts, and how wide it may run.
MARGIN = Cm(1.8)
CONTENT_WIDTH = SLIDE_WIDTH - 2 * MARGIN

#: The palette. One dark accent for titles, one blue for rules and subtitles,
#: one grey for the footer, so the deck reads as one system rather than as
#: whatever colour each placeholder inherited.
NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE = RGBColor(0x2E, 0x75, 0xB6)
TEXT = RGBColor(0x26, 0x26, 0x26)
GREY = RGBColor(0x59, 0x59, 0x59)

FONT = "Arial"

#: How much of a deck fits on one slide before it is split. The limits are on
#: the count and on the written volume, because ten short bullets read fine and
#: four long ones do not.
MAX_BULLETS = 7
MAX_CHARS = 620


def render_slides(
    materials: Materials,
    *,
    programme_title: str,
    topic_ids: set[str] | None = None,
    organization: str = ORGANIZATION_LINES[-1],
    city: str = DEFAULT_CITY,
    year: int = DEFAULT_YEAR,
) -> bytes:
    """Return the PPTX bytes of the decks of a programme.

    `topic_ids` narrows the file to the topics named; every deck of one of them
    is rendered, and a file without decks is still a file — it holds the title
    slide alone. The decks are written per topic, so narrowing by topic is
    also how a caller narrows by module: the topics of the module.
    """
    decks = [deck for deck in materials.slide_decks if _wanted(deck, topic_ids)]

    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    _title_slide(prs, programme_title, organization=organization, city=city, year=year)
    for deck in decks:
        _deck_divider(prs, deck)
        for slide in deck.slides:
            _content_slides(prs, slide.title, slide.bullets, footer=programme_title)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _wanted(deck: SlideDeck, topic_ids: set[str] | None) -> bool:
    """Whether a deck is in the file the caller asked for."""
    return topic_ids is None or deck.topic_id in topic_ids


# --------------------------------------------------------------------------- geometry
def _rule(slide, *, left, top, width, height, color) -> None:
    """A filled rectangle with no outline and no theme shadow."""
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False


def _write(
    placeholder,
    text: str,
    *,
    size: int,
    bold: bool = False,
    color=TEXT,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.MIDDLE,
) -> None:
    """Replace a placeholder's text and style the run that carries it."""
    frame = placeholder.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    _style(run, size=size, bold=bold, color=color)


def _style(run, *, size: int, bold: bool = False, color=TEXT) -> None:
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _textbox(slide, *, left, top, width, height, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = Cm(0)
    frame.margin_right = Cm(0)
    frame.margin_top = Cm(0)
    frame.margin_bottom = Cm(0)
    frame.paragraphs[0].alignment = align
    return box


def _footer(slide, text: str, number: int) -> None:
    """The programme title on the left and the slide number on the right."""
    line = _textbox(slide, left=MARGIN, top=SLIDE_HEIGHT - Cm(1.5), width=CONTENT_WIDTH - Cm(2.4), height=Cm(0.8))
    run = line.text_frame.paragraphs[0].add_run()
    run.text = _shorten(text, 90)
    _style(run, size=9, color=GREY)

    page = _textbox(
        slide,
        left=SLIDE_WIDTH - MARGIN - Cm(2.0),
        top=SLIDE_HEIGHT - Cm(1.5),
        width=Cm(2.0),
        height=Cm(0.8),
        align=PP_ALIGN.RIGHT,
    )
    run = page.text_frame.paragraphs[0].add_run()
    run.text = str(number)
    _style(run, size=9, color=GREY)


# --------------------------------------------------------------------------- slides
def _title_slide(prs: Presentation, title: str, *, organization: str, city: str, year: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    _rule(slide, left=0, top=0, width=SLIDE_WIDTH, height=Cm(0.7), color=NAVY)

    heading = slide.shapes.title
    heading.left, heading.top, heading.width, heading.height = MARGIN, Cm(5.4), CONTENT_WIDTH, Cm(4.4)
    size = 36 if len(title) <= 60 else 30 if len(title) <= 110 else 26
    _write(heading, title, size=size, bold=True, color=NAVY, anchor=MSO_ANCHOR.TOP)

    _rule(slide, left=MARGIN, top=Cm(4.7), width=Cm(4.2), height=Cm(0.14), color=BLUE)

    subtitle = slide.placeholders[1]
    subtitle.left, subtitle.top, subtitle.width, subtitle.height = MARGIN, Cm(10.3), CONTENT_WIDTH, Cm(1.8)
    _write(subtitle, "Презентационные материалы программы", size=20, color=BLUE, anchor=MSO_ANCHOR.TOP)

    footer = _textbox(slide, left=MARGIN, top=SLIDE_HEIGHT - Cm(2.2), width=CONTENT_WIDTH, height=Cm(1.0))
    run = footer.text_frame.paragraphs[0].add_run()
    run.text = " · ".join(part for part in (organization, city, str(year)) if part)
    _style(run, size=12, color=GREY)


def _deck_divider(prs: Presentation, deck: SlideDeck) -> None:
    """The cover of one deck: the deck's title and the topic it belongs to."""
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    _rule(slide, left=0, top=0, width=Cm(1.2), height=SLIDE_HEIGHT, color=NAVY)

    heading = slide.shapes.title
    heading.left, heading.top, heading.width, heading.height = Cm(2.8), Cm(6.2), CONTENT_WIDTH - Cm(1.0), Cm(3.6)
    size = 30 if len(deck.title) <= 60 else 26
    _write(heading, deck.title, size=size, bold=True, color=NAVY, anchor=MSO_ANCHOR.BOTTOM)

    _rule(slide, left=Cm(2.8), top=Cm(10.2), width=Cm(4.2), height=Cm(0.14), color=BLUE)

    subtitle = slide.placeholders[1]
    subtitle.left, subtitle.top, subtitle.width, subtitle.height = Cm(2.8), Cm(10.7), CONTENT_WIDTH, Cm(1.6)
    _write(subtitle, f"Тема {deck.topic_id}", size=18, color=BLUE, anchor=MSO_ANCHOR.TOP)


def _content_slides(prs: Presentation, title: str, bullets: list[str], *, footer: str) -> None:
    """One slide per page of bullets, splitting a page that would overflow."""
    pages = _pages(bullets)
    for page_number, page in enumerate(pages, start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        _rule(slide, left=0, top=0, width=SLIDE_WIDTH, height=Cm(0.35), color=NAVY)

        heading = slide.shapes.title
        heading.left, heading.top, heading.width, heading.height = MARGIN, Cm(0.9), CONTENT_WIDTH, Cm(2.2)
        suffix = "" if page_number == 1 else " (продолжение)"
        size = 26 if len(title) <= 60 else 22 if len(title) <= 100 else 20
        _write(heading, f"{title}{suffix}", size=size, bold=True, color=NAVY, anchor=MSO_ANCHOR.TOP)

        _rule(slide, left=MARGIN, top=Cm(3.25), width=Cm(4.2), height=Cm(0.12), color=BLUE)

        body = slide.placeholders[1]
        body.left, body.top, body.width, body.height = MARGIN, Cm(3.8), CONTENT_WIDTH, Cm(12.4)
        frame = body.text_frame
        frame.word_wrap = True
        frame.clear()
        size = 20 if len(page) <= 5 else 18 if len(page) <= MAX_BULLETS else 16
        for index, bullet in enumerate(page):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = bullet
            paragraph.level = 0
            paragraph.space_after = Pt(10)
            for run in paragraph.runs:
                _style(run, size=size, color=TEXT)

        _footer(slide, footer, len(prs.slides))


def _pages(bullets: list[str]) -> list[list[str]]:
    """Split bullets into what a reader can take in on one slide."""
    if not bullets:
        return [[]]
    pages: list[list[str]] = []
    current: list[str] = []
    size = 0
    for bullet in bullets:
        if current and (len(current) >= MAX_BULLETS or size + len(bullet) > MAX_CHARS):
            pages.append(current)
            current, size = [], 0
        current.append(bullet)
        size += len(bullet)
    pages.append(current)
    return pages


def _shorten(text: str, limit: int) -> str:
    """A footer line that does not wrap into the slide body."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
