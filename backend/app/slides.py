"""Render the slides of a programme to a PPTX file.

:func:`render_slides` takes the material of a programme — the slide decks the
materials stage wrote — and returns the bytes of a PPTX file. The criterion is
"the slides for at least one module", so a caller can ask for one module's
decks; without a module the whole programme's decks are rendered.

The file is built from code, not from a template: a title slide for the
programme, then one slide per bullet of every deck.
"""

from __future__ import annotations

from io import BytesIO

from pptx import Presentation
from pptx.util import Cm, Pt

from app.agentic.models import Materials, SlideDeck


def render_slides(
    materials: Materials,
    *,
    programme_title: str,
    topic_ids: set[str] | None = None,
) -> bytes:
    """Return the PPTX bytes of the decks of a programme.

    `topic_ids` narrows the file to the topics named; every deck of one of them
    is rendered, and a file without decks is still a file — it holds the title
    slide alone. The decks are written per topic, so narrowing by topic is
    also how a caller narrows by module: the topics of the module.
    """
    decks = [deck for deck in materials.slide_decks if _wanted(deck, topic_ids)]

    prs = Presentation()
    prs.slide_width = Cm(33.87)
    prs.slide_height = Cm(19.05)

    title = prs.slides.add_slide(prs.slide_layouts[0])
    title.shapes.title.text = programme_title
    if title.placeholders[1] is not None:
        title.placeholders[1].text = "Презентационные материалы программы"

    for deck in decks:
        _render_deck(prs, deck)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _wanted(deck: SlideDeck, topic_ids: set[str] | None) -> bool:
    """Whether a deck is in the file the caller asked for."""
    return topic_ids is None or deck.topic_id in topic_ids


def _render_deck(prs: Presentation, deck: SlideDeck) -> None:
    """One deck: a title slide and one slide per bullet."""
    opener = prs.slides.add_slide(prs.slide_layouts[0])
    opener.shapes.title.text = deck.title
    if opener.placeholders[1] is not None:
        opener.placeholders[1].text = f"Тема {deck.topic_id}"

    for slide in deck.slides:
        page = prs.slides.add_slide(prs.slide_layouts[1])
        page.shapes.title.text = slide.title
        frame = page.placeholders[1].text_frame
        frame.clear()
        for index, bullet in enumerate(slide.bullets):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = bullet
            paragraph.font.size = Pt(20)
