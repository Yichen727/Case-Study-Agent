from __future__ import annotations

import re
from pathlib import Path
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.models import FieldProvenance, ParsedSlide, ParsedCase

SECTION_NAMES = ("BRIEF", "CREATIVE", "IMPACT")
PLACEHOLDER_RE = re.compile(r"^REPLACE WITH (?:VIDEO|IMAGE)\b", re.I)
CASE_TITLE_RE = re.compile(r"^CASE\s+STUDY\s*:\s*(.+)$", re.I)


def _shape_text(shape) -> str:
    """Extract readable text from common PowerPoint shape types."""
    if not getattr(shape, "has_text_frame", False):
        return ""
    lines = []
    for paragraph in shape.text_frame.paragraphs:
        text = paragraph.text.strip()
        # Some slides put the heading and body in separate runs of one paragraph.
        runs = paragraph.runs
        if runs and runs[0].text.strip().rstrip(":").upper() in SECTION_NAMES:
            heading = runs[0].text.strip().rstrip(":").upper()
            body = "".join(run.text for run in runs[1:]).strip()
            lines.extend([heading, body] if body else [heading])
        elif text:
            lines.append(text)
    return "\n".join(lines).strip()


def _walk_shapes(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_shapes(shape.shapes)
        else:
            yield shape


def _link_type(label: str) -> str:
    label = label.strip().upper()
    if label.startswith("READ MORE"):
        return "READ_MORE"
    if label.startswith("WATCH") and "VIDEO" in label:
        return "WATCH_VIDEO"
    return "OTHER"


def _extract_hyperlink_records(shape) -> list[dict[str, str]]:
    """Return each hyperlink's visible run text and target URL from the source PPTX."""
    records: list[dict[str, str]] = []
    if getattr(shape, "has_text_frame", False):
        for paragraph in shape.text_frame.paragraphs:
            label = paragraph.text.strip()
            for run in paragraph.runs:
                address = run.hyperlink.address
                if address:
                    record = {"text": label or "Open link", "url": address, "link_type": _link_type(label)}
                    if record not in records:
                        records.append(record)
    address = shape.click_action.hyperlink.address
    if address:
        label = _shape_text(shape) or "Open link"
        record = {"text": label, "url": address, "link_type": _link_type(label)}
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
        if upper in SECTION_NAMES or _link_type(upper) in {"READ_MORE", "WATCH_VIDEO"}:
            next_heading = i
            break

    content = lines[heading_index + 1:next_heading]
    value = "\n".join(content).strip()
    return value or None


def _is_case_heading(text: str) -> bool:
    lines = _normalise_lines(text)
    return bool(lines and re.match(r"^CASE\s+STUDY\b", lines[0], flags=re.I)
                and not CASE_TITLE_RE.match(lines[0]))


def _extract_labelled_field(lines: list[str], name: str) -> str | None:
    for index, line in enumerate(lines):
        match = re.fullmatch(rf"{name}\s*:\s*(.+)", line, flags=re.I)
        if match:
            return match.group(1).strip() or None
        if line.rstrip(":").upper() == name and index + 1 < len(lines):
            value = lines[index + 1]
            if value.upper().rstrip(":") not in {*SECTION_NAMES, "CLIENT", "SERVICES"}:
                return value
    return None


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
        for shape_index, shape in enumerate(_walk_shapes(slide.shapes)):
            text = _shape_text(shape)
            if text:
                shape_records.append((shape_index, text))
            shape_links = _extract_hyperlink_records(shape)
            link_records.extend(shape_links)
            links.extend(item["url"] for item in shape_links)
        links = list(dict.fromkeys(links))
        # Preserve link labels/types even when the same URL has multiple actions.
        unique_link_records = []
        for record in link_records:
            if record not in unique_link_records:
                unique_link_records.append(record)

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
                if _link_type(value) in {"READ_MORE", "WATCH_VIDEO"}:
                    continue
                title_match = CASE_TITLE_RE.match(value)
                candidates.append(title_match.group(1).strip() if title_match else value)
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
                original_slide_id=slide.slide_id,
                client=_extract_labelled_field(_normalise_lines(all_text), "CLIENT") if is_case else None,
                services=_extract_labelled_field(_normalise_lines(all_text), "SERVICES") if is_case else None,
            )
        )

    return parsed


def build_cases(slides: list[ParsedSlide], source_deck: str, source_id: str) -> list[ParsedCase]:
    """One independent case per original slide, scoped to its import."""
    cases: list[ParsedCase] = []

    for slide in slides:
        if not slide.is_case_study:
            continue
        if not source_id or slide.original_slide_id is None:
            raise ValueError("A source ID and original slide ID are required.")

        case = ParsedCase(
            case_id=f"{source_id}:{slide.original_slide_id}",
            title=slide.case_name or f"Untitled case on slide {slide.slide_number}",
            pillar=slide.pillar,
            client=slide.client,
            services=slide.services,
            brief=slide.brief,
            creative=slide.creative,
            impact=slide.impact,
            source_deck=source_deck,
            slide_numbers=[slide.slide_number],
            source_links=slide.hyperlinks,
            source_link_labels=slide.hyperlink_labels,
            source_id=source_id,
            original_slide_id=slide.original_slide_id,
        )
        reference = f"source:{source_id}/slide:{slide.original_slide_id}"
        for name in ("title", "pillar", "client", "services", "brief", "creative", "impact", "original_slide_id", "slide_number", "source_links"):
            value = slide.case_name if name == "title" else (
                slide.hyperlinks if name == "source_links" else getattr(slide, name)
            )
            case.field_provenance[name] = FieldProvenance(
                "SLIDE_EXTRACTED" if value else "UNKNOWN",
                reference if value else None,
                "EXTRACTED" if value else "UNVERIFIED",
            )
        cases.append(case)

    return cases
