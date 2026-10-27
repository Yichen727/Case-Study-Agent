from app.ingestion.pptx_parser import _extract_section, _normalise_lines, _is_case_heading


def test_normalise_lines_removes_empty_lines():
    assert _normalise_lines("A\n\n B \n") == ["A", "B"]


def test_extract_section_stops_at_next_heading():
    lines = ["BRIEF", "The brief", "CREATIVE", "The idea", "IMPACT", "Results", "READ MORE HERE."]
    assert _extract_section(lines, "BRIEF") == "The brief"
    assert _extract_section(lines, "CREATIVE") == "The idea"
    assert _extract_section(lines, "IMPACT") == "Results"


def test_case_heading_is_detected_independently_of_slide_number():
    assert _is_case_heading("CASE STUDY SUSTAINABILITY")
    assert not _is_case_heading("1")
