from __future__ import annotations

import re
from pathlib import Path
from pptx import Presentation

from app.models import ParsedSlide, ParsedCase, determine_case_status

SECTION_NAMES = ("BRIEF", "CREATIVE", "IMPACT")
PLACEHOLDER_RE = re.compile(r"^REPLACE WITH (?:VIDEO|IMAGE)\b", re.I)


def _shape_text(shape) -> str:
    """Extract readable text from common PowerPoint shape types."""
    if not getattr(shape, "has_text_frame", False):
        return ""
    return "\n".join(
        paragraph.text.strip()
        for paragraph in shape.text_frame.paragraphs
        if paragraph.text.strip()
    ).strip()


def _extract_hyperlink_records(shape) -> list[dict[str, str]]:
    """Return each hyperlink's visible run text and target URL from the source PPTX."""
    records: list[dict[str, str]] = []
    if not getattr(shape, "has_text_frame", False):
        return records
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            address = run.hyperlink.address
            label = run.text.strip()
            if address:
                record = {"text": label or "Open link", "url": address}
                if record not in records:
                    records.append(record)
    return records


def _extract_hyperlinks(shape) -> list[str]:
    """Backwards-compatible URL-only helper."""
    return list(dict.fromkeys(item["url"] for item in _extract_hyperlink_records(shape)))


def _normalise_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_section(lines: list[str], section_name: str) -> str | None:
    """Extract text between standalone BRIEF / CREATIVE / IMPACT headings."""
    heading_index = next(
        (i for i, line in enumerate(lines)
         if line.strip().rstrip(":").upper() == section_name),
        None,
    )
    if heading_index is None:
        return None

    next_heading = len(lines)
    for i in range(heading_index + 1, len(lines)):
        upper = lines[i].strip().rstrip(":").upper()
        if upper in SECTION_NAMES or upper.startswith("READ MORE HERE") or upper.startswith("WATCH VIDEO HERE"):
            next_heading = i
            break

    content = lines[heading_index + 1:next_heading]
    value = "\n".join(content).strip()
    return value or None


def _is_case_heading(text: str) -> bool:
    lines = _normalise_lines(text)
    return bool(lines and re.match(r"^CASE\s+STUDY\b", lines[0], flags=re.I))


def parse_pptx(path: str | Path) -> list[ParsedSlide]:
    """Parse slides by inspecting individual shapes, not the first combined line.

    The supplied master deck has a slide-number shape before the CASE STUDY
    heading, and its case title is a separate shape. This parser accounts for
    that layout while retaining slide numbers and hyperlinks.
    """
    presentation = Presentation(str(path))
    parsed: list[ParsedSlide] = []

    for slide_number, slide in enumerate(presentation.slides, start=1):
        shape_records: list[tuple[int, str]] = []
        links: list[str] = []
        link_records: list[dict[str, str]] = []
        for shape_index, shape in enumerate(slide.shapes):
            text = _shape_text(shape)
            if text:
                shape_records.append((shape_index, text))
            shape_links = _extract_hyperlink_records(shape)
            link_records.extend(shape_links)
            links.extend(item["url"] for item in shape_links)
        links = list(dict.fromkeys(links))
        # Preserve original order and visible label for every unique URL.
        unique_link_records = []
        seen_urls = set()
        for record in link_records:
            if record["url"] not in seen_urls:
                unique_link_records.append(record)
                seen_urls.add(record["url"])

        heading_record = next(
            ((idx, text) for idx, text in shape_records if _is_case_heading(text)),
            None,
        )
        is_case = heading_record is not None
        pillar = None
        case_name = None
        brief = creative = impact = None

        if is_case:
            _, heading_text = heading_record
            heading_line = _normalise_lines(heading_text)[0]
            pillar = re.sub(r"^CASE\s+STUDY\b", "", heading_line, flags=re.I).strip(" :-/") or None

            # The body is normally a separate text box containing all three sections.
            body_record = next(
                ((idx, text) for idx, text in shape_records
                 if all(re.search(rf"(?im)^\s*{name}\s*:?[ \t]*$", text) for name in SECTION_NAMES)),
                None,
            )
            body_text = body_record[1] if body_record else ""
            body_lines = _normalise_lines(body_text)
            brief = _extract_section(body_lines, "BRIEF")
            creative = _extract_section(body_lines, "CREATIVE")
            impact = _extract_section(body_lines, "IMPACT")

            # Case title is another shape, e.g. "EY: SUSTAINABILITY PEOPLE".
            # Exclude slide number, the category heading, body text and placeholders.
            candidates: list[str] = []
            excluded_indices = {heading_record[0]}
            if body_record:
                excluded_indices.add(body_record[0])
            for idx, text in shape_records:
                if idx in excluded_indices:
                    continue
                lines = _normalise_lines(text)
                if len(lines) != 1:
                    continue
                value = lines[0]
                if value.isdigit() or _is_case_heading(value):
                    continue
                if PLACEHOLDER_RE.match(value):
                    continue
                if value.upper().startswith(("READ MORE HERE", "WATCH VIDEO HERE")):
                    continue
                candidates.append(value)
            # In this deck the first remaining non-placeholder text shape is the title.
            case_name = candidates[0] if candidates else None

        all_text = "\n".join(text for _, text in shape_records).strip()
        parsed.append(
            ParsedSlide(
                slide_number=slide_number,
                text=all_text,
                hyperlinks=links,
                hyperlink_labels=unique_link_records,
                is_case_study=is_case,
                pillar=pillar,
                case_name=case_name,
                brief=brief,
                creative=creative,
                impact=impact,
            )
        )

    return parsed


def build_cases(slides: list[ParsedSlide], source_deck: str) -> list[ParsedCase]:
    """First-pass grouping: one case per slide until continuation rules are verified."""
    cases: list[ParsedCase] = []

    for slide in slides:
        if not slide.is_case_study:
            continue

        case = ParsedCase(
            case_id=f"slide-{slide.slide_number}",
            title=slide.case_name or f"Untitled case on slide {slide.slide_number}",
            pillar=slide.pillar,
            client=None,
            services=None,
            brief=slide.brief,
            creative=slide.creative,
            impact=slide.impact,
            source_deck=source_deck,
            slide_numbers=[slide.slide_number],
            source_links=slide.hyperlinks,
            source_link_labels=slide.hyperlink_labels,
        )

        # Determine the status after the case has been created.
        case.review_status = determine_case_status(case)

        cases.append(case)

    return cases
