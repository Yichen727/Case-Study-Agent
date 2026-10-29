from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from app.db.repository import get_field_metadata
from app.ingestion.pptx_parser import parse_pptx
from app.services import ingest_service


def textbox(slide, text, y):
    shape = slide.shapes.add_textbox(Inches(0.5), Inches(y), Inches(8), Inches(1))
    shape.text = text
    return shape


def test_nonstandard_title_is_not_a_category_and_does_not_invent_sections(tmp_path):
    deck = Presentation()
    for index in range(2):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        textbox(slide, str(index + 1), 0)
        textbox(slide, "CASE STUDY CONTENT ECOSYSTEM", 0.3)
        textbox(slide, "CASE STUDY: STREET OF DREAMS", 1)
        textbox(slide, "THE IDEA\nOriginal nonstandard body", 2)
    separator = deck.slides.add_slide(deck.slide_layouts[6])
    textbox(separator, "CASE STUDIES", 1)
    path = tmp_path / "cases.pptx"
    deck.save(path)
    slides = parse_pptx(path)
    assert [slide.case_name for slide in slides[:2]] == ["STREET OF DREAMS"] * 2
    assert slides[0].original_slide_id != slides[1].original_slide_id
    assert all(slide.brief is None and slide.creative is None and slide.impact is None for slide in slides[:2])
    assert not slides[2].is_case_study


def test_inline_heading_and_grouped_text_keep_outcome_and_link_types(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    textbox(group, "CASE STUDY SHORT AND SOCIAL", 0.3)
    textbox(group, "SYNTHETIC: INLINE", 1)
    body = textbox(group, "", 2)
    paragraph = body.text_frame.paragraphs[0]
    paragraph.add_run().text = "BRIEF"
    paragraph.add_run().text = "Original brief without a paragraph break."
    for line in ("CREATIVE", "Original idea", "IMPACT", "TBC", "WATCH THE HERO VIDEO HERE."):
        body.text_frame.add_paragraph().text = line
    body.text_frame.paragraphs[-1].runs[0].hyperlink.address = "https://example.com/video"
    shape_link = textbox(slide, "READ MORE HERE.", 5)
    shape_link.click_action.hyperlink.address = "https://example.com/read-more"
    path = tmp_path / "inline.pptx"
    deck.save(path)
    parsed = parse_pptx(path)[0]
    assert parsed.brief == "Original brief without a paragraph break."
    assert parsed.creative == "Original idea"
    assert parsed.impact == "TBC"
    assert {link["link_type"] for link in parsed.hyperlink_labels} == {"READ_MORE", "WATCH_VIDEO"}


def test_original_client_services_take_priority_over_linked_page(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures" / "source_a.pptx"
    deck = Presentation(fixture)
    textbox(deck.slides[0], "Client: Original Client\nServices\nOriginal Services", 5.5)
    path = tmp_path / "metadata.pptx"
    deck.save(path)
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {
        "text": "Linked evidence", "client": "Other Client", "services": "Other Services",
    })
    db = tmp_path / "cases.sqlite3"
    result = ingest_service.ingest_deck(path, db)
    case = result["cases"][0]
    assert case.client == "Original Client" and case.services == "Original Services"
    fields = {row["field_name"]: row for row in get_field_metadata(db, case.case_id)}
    assert fields["client"]["source_kind"] == "SLIDE_EXTRACTED"
    assert fields["services"]["source_kind"] == "SLIDE_EXTRACTED"
